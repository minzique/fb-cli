"""Import Facebook auth state from a live Chrome DevTools session.

Stdlib-only on purpose: fb-cli has no runtime dependencies, so this file carries a
small Chrome DevTools Protocol websocket client instead of depending on
websocket-client or pychrome.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from typing import Any

from fb_cli import cdp

INTERESTING_COOKIES = ("c_user", "xs", "datr", "fr", "sb", "presence", "wd", "dpr")
REQUIRED_COOKIES = ("c_user", "xs", "datr")
DEFAULT_DEBUG_URL = "http://127.0.0.1:9222"
DEFAULT_MARKETPLACE_URL = "https://www.facebook.com/marketplace/search/?query={query}"


class BrowserAuthError(RuntimeError):
    """Chrome/CDP auth import could not produce a usable auth file."""


def import_from_browser(
    *,
    debug_url: str = DEFAULT_DEBUG_URL,
    timeout: int = 15,
    search_query: str = "keyboard",
    navigate: bool = True,
    existing_auth: dict[str, Any] | None = None,
    launch: bool = True,
    copy_profile: bool = False,
) -> dict[str, Any]:
    """Build an auth.json payload from a Chrome instance on --remote-debugging-port.

    The browser must already be logged into facebook.com. We extract cookies via
    CDP, then either read Facebook's in-page modules or capture a fresh
    /api/graphql/ request to get fb_dtsg/lsd/jazoest/revision tokens.

    If `launch=True` and no debug Chrome is reachable on `debug_url`, we boot
    the fb-cli-managed Chrome (see chrome_launcher) and reuse it.
    """
    if launch:
        from fb_cli import chrome_launcher

        if not chrome_launcher.is_running(debug_url):
            try:
                chrome_launcher.start(
                    copy_profile=copy_profile,
                    landing_url=DEFAULT_MARKETPLACE_URL.format(
                        query=urllib.parse.quote(search_query)
                    ),
                )
            except chrome_launcher.ChromeLauncherError as e:
                raise BrowserAuthError(str(e)) from e
            # Give Facebook a moment to finish loading before we grab cookies
            time.sleep(1.5)

    target = _choose_target(_list_targets(debug_url))
    try:
        with cdp.CDPClient(target.websocket_url) as cdp_client:
            cdp_client.call("Runtime.enable")
            cdp_client.call("Network.enable", {"maxPostDataSize": 1024 * 1024})
            cdp_client.call("Page.enable")

            page_data = _evaluate_page_data(cdp_client)
            graphql_forms = _capture_graphql_forms(
                cdp_client,
                target_url=target.url,
                search_query=search_query,
                timeout=timeout,
                navigate=navigate,
            )
            cookies = _facebook_cookies(cdp_client.call("Network.getAllCookies"))
            form = _best_graphql_form(graphql_forms)
    except cdp.CDPError as e:
        raise BrowserAuthError(str(e)) from e

    missing = [name for name in REQUIRED_COOKIES if not cookies.get(name)]
    if missing:
        raise BrowserAuthError(
            "Chrome is not logged into facebook.com, or Facebook cookies are not visible. "
            f"Missing cookies: {', '.join(missing)}. Log in, open Marketplace, then retry."
        )

    fb_dtsg = form.get("fb_dtsg") or page_data.get("fb_dtsg")
    lsd = form.get("lsd") or page_data.get("lsd")
    if not fb_dtsg or not lsd:
        raise BrowserAuthError(
            "Could not find Facebook fb_dtsg/lsd tokens in the live browser. "
            "Open https://www.facebook.com/marketplace/ in the debug Chrome window and retry."
        )

    user_id = cookies.get("c_user") or page_data.get("user_id") or (existing_auth or {}).get("user_id")
    auth: dict[str, Any] = {
        "user_id": user_id,
        "user_agent": page_data.get("user_agent") or (existing_auth or {}).get("user_agent", ""),
        "cookies": cookies,
        "fb_dtsg": fb_dtsg,
        "lsd": lsd,
        "jazoest": form.get("jazoest") or _jazoest(fb_dtsg),
        "rev": form.get("__rev") or page_data.get("rev") or (existing_auth or {}).get("rev", ""),
        "hsi": form.get("__hsi") or page_data.get("hsi") or (existing_auth or {}).get("hsi", ""),
        "hs": form.get("__hs") or page_data.get("hs") or (existing_auth or {}).get("hs", ""),
        "buy_location": _buy_location_from_forms(graphql_forms)
        or (existing_auth or {}).get("buy_location", {}),
        "captured_at": int(time.time()),
        "source_har": f"chrome-devtools:{debug_url}",
    }
    return auth


def _list_targets(debug_url: str) -> list[cdp.Target]:
    try:
        return cdp.list_targets(debug_url)
    except cdp.CDPError as e:
        msg = str(e).replace(
            "Run `fb-cli auth chrome start`.",
            "Run `fb-cli auth chrome start` (or pass --launch).",
        )
        raise BrowserAuthError(msg) from e


def _choose_target(targets: list[cdp.Target]) -> cdp.Target:
    def score(t: cdp.Target) -> tuple[int, int]:
        url = t.url.lower()
        return (
            2 if "facebook.com/marketplace" in url else 1 if "facebook.com" in url else 0,
            0 if url.startswith("chrome://") else 1,
        )

    target = max(targets, key=score)
    if "facebook.com" not in target.url.lower():
        raise BrowserAuthError(
            "No facebook.com tab found in Chrome DevTools. "
            "Run `fb-cli auth chrome login` and sign in (with 'Save login info' checked), then retry."
        )
    return target


def _evaluate_page_data(cdp_client: cdp.CDPClient) -> dict[str, str]:
    expression = r"""
(() => {
  const out = {user_agent: navigator.userAgent};
  const read = (name) => {
    try {
      if (typeof require === 'function') return require(name);
    } catch (e) {}
    return null;
  };
  const dtsg = read('DTSGInitialData');
  if (dtsg && dtsg.token) out.fb_dtsg = String(dtsg.token);
  const lsd = read('LSD');
  if (lsd && lsd.token) out.lsd = String(lsd.token);
  const site = read('SiteData');
  if (site) {
    if (site.userID || site.actorID) out.user_id = String(site.userID || site.actorID);
    if (site.__spin_r || site.client_revision) out.rev = String(site.__spin_r || site.client_revision);
    if (site.hsi) out.hsi = String(site.hsi);
    if (site.haste_session) out.hs = String(site.haste_session);
  }
  return out;
})()
"""
    try:
        resp = cdp_client.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
    except cdp.CDPError:
        return {}
    value = ((resp.get("result") or {}).get("result") or {}).get("value")
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def _capture_graphql_forms(
    cdp_client: cdp.CDPClient,
    *,
    target_url: str,
    search_query: str,
    timeout: int,
    navigate: bool,
) -> list[dict[str, str]]:
    if navigate:
        if "facebook.com/marketplace" in target_url.lower():
            cdp_client.call("Page.reload", {"ignoreCache": True})
        else:
            url = DEFAULT_MARKETPLACE_URL.format(query=urllib.parse.quote(search_query))
            cdp_client.call("Page.navigate", {"url": url})

    forms: list[dict[str, str]] = []
    deadline = time.monotonic() + max(1, timeout)
    while time.monotonic() < deadline:
        msg = cdp_client.recv_json(timeout=max(0.1, min(1.0, deadline - time.monotonic())))
        if not msg or msg.get("method") != "Network.requestWillBeSent":
            continue
        request = ((msg.get("params") or {}).get("request") or {})
        url = str(request.get("url") or "")
        post_data = request.get("postData")
        if "/api/graphql" not in url or not isinstance(post_data, str):
            continue
        parsed = urllib.parse.parse_qs(post_data, keep_blank_values=True)
        form = {k: v[0] if v else "" for k, v in parsed.items()}
        if form.get("fb_dtsg") or form.get("lsd"):
            forms.append(form)
            if _form_has_marketplace_location(form):
                # We have everything: fresh tokens and a Marketplace location.
                break
    return forms


def _facebook_cookies(resp: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for cookie in resp.get("result", {}).get("cookies", []):
        domain = str(cookie.get("domain") or "")
        name = str(cookie.get("name") or "")
        value = cookie.get("value")
        if "facebook.com" not in domain or value is None:
            continue
        if name in INTERESTING_COOKIES or name.startswith("locale"):
            out[name] = str(value)
    return out


def _best_graphql_form(forms: list[dict[str, str]]) -> dict[str, str]:
    def score(form: dict[str, str]) -> tuple[int, int]:
        friendly = form.get("fb_api_req_friendly_name", "")
        variables = form.get("variables", "")
        return (
            2 if _form_has_marketplace_location(form) else 1 if "Marketplace" in friendly else 0,
            len(variables),
        )

    return max(forms, key=score) if forms else {}


def _form_has_marketplace_location(form: dict[str, str]) -> bool:
    variables = form.get("variables", "")
    return "buyLocation" in variables or "filter_location_latitude" in variables


def _buy_location_from_forms(forms: list[dict[str, str]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for form in forms:
        raw = form.get("variables")
        if not raw:
            continue
        try:
            variables = json.loads(raw)
        except json.JSONDecodeError:
            continue
        buy_location = variables.get("buyLocation")
        if isinstance(buy_location, dict):
            if "latitude" in buy_location:
                out.setdefault("latitude", buy_location.get("latitude"))
            if "longitude" in buy_location:
                out.setdefault("longitude", buy_location.get("longitude"))
        params = variables.get("params")
        if isinstance(params, dict):
            browse = params.get("browse_request_params")
            if isinstance(browse, dict):
                out.setdefault("latitude", browse.get("filter_location_latitude"))
                out.setdefault("longitude", browse.get("filter_location_longitude"))
        for key in ("searchPopularSearchesParams", "topicPageParams"):
            sub = variables.get(key)
            if isinstance(sub, dict) and sub.get("location_id"):
                out.setdefault("location_id", sub.get("location_id"))
    return {k: v for k, v in out.items() if v not in (None, "")}


def _jazoest(token: str) -> str:
    return "2" + str(sum(ord(ch) for ch in token))


