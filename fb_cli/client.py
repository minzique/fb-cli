"""HTTP client for Facebook's internal GraphQL endpoint.

Stdlib only. Handles gzip + form encoding + the long list of routing fields
Facebook expects on /api/graphql/.
"""
from __future__ import annotations

import gzip
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from typing import Any

from fb_cli.queries import DOC_IDS

GRAPHQL_URL = "https://www.facebook.com/api/graphql/"

# Substrings Facebook uses in error responses when the per-page CSRF tokens
# (fb_dtsg / lsd) are stale but the long-lived cookies are still valid. When
# we see one of these we can usually recover with a single cookie_refresh call.
_STALE_TOKEN_HINTS = (
    "fb_dtsg",
    "Login required",
    "You must be logged in",
    "Sorry, something went wrong",
    "www.facebook.com/checkpoint",
)


class FBError(RuntimeError):
    """Anything Facebook returned that wasn't usable data."""


class AuthExpiredError(FBError):
    """Cookies/tokens are no longer valid. Re-import HAR."""


def _decode(raw: bytes, encoding: str | None) -> str:
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        raw = zlib.decompress(raw)
    return raw.decode("utf-8", errors="replace")


# Per-query route hints (the `__crn` field). FB returns HTTP 500 on PDP if you
# omit a plausible route for the friendly_name.
ROUTE_HINTS: dict[str, str] = {
    "CometMarketplaceSearchContentContainerQuery": "comet.fbweb.CometMarketplaceSearchRoute",
    "CometMarketplaceSearchContentPaginationQuery": "comet.fbweb.CometMarketplaceSearchRoute",
    "CometMarketplaceSearchRootQuery": "comet.fbweb.CometMarketplaceSearchRoute",
    "MarketplacePDPContainerQuery": "comet.fbweb.CometMarketplaceSearchRoute",
    "MarketplacePDPC2CMediaViewerWithImagesQuery": "comet.fbweb.CometMarketplaceSearchRoute",
    "MarketplaceCometBrowseFeedLightContainerQuery": "comet.fbweb.CometMarketplaceMultiCategoryRoute",
    "MarketplaceSuggestionDataSourceQuery": "comet.fbweb.CometMarketplaceSearchRoute",
    "MarketplaceRecentDataSourceQuery": "comet.fbweb.CometMarketplaceSearchRoute",
    "CometMarketplaceLeftRailNavigationContainerQuery": "comet.fbweb.CometMarketplaceMultiCategoryRoute",
}


def _form_fields(auth: dict[str, Any], friendly_name: str, doc_id: str, variables: dict[str, Any]) -> dict[str, str]:
    user = auth["user_id"]
    rev = auth.get("rev", "1037953373")
    return {
        "av": user,
        "__user": user,
        "__a": "1",
        "__req": "1",
        "__hs": auth.get("hs", "20566.HYP:comet_pkg.2.1...0"),
        "dpr": "1",
        "__ccg": "GOOD",
        "__rev": rev,
        "__hsi": auth.get("hsi", ""),
        "__comet_req": "15",
        "__crn": ROUTE_HINTS.get(friendly_name, "comet.fbweb.CometMarketplaceMultiCategoryRoute"),
        "lsd": auth["lsd"],
        "jazoest": auth["jazoest"],
        "__spin_r": rev,
        "__spin_b": "trunk",
        "__spin_t": str(int(time.time())),
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": friendly_name,
        "variables": json.dumps(variables, separators=(",", ":")),
        "server_timestamps": "true",
        "doc_id": doc_id,
        "fb_dtsg": auth["fb_dtsg"],
    }


def graphql(
    auth: dict[str, Any],
    friendly_name: str,
    variables: dict[str, Any],
    *,
    doc_id: str | None = None,
    referer: str = "https://www.facebook.com/marketplace/",
    timeout: int = 30,
    auto_refresh: bool = True,
) -> dict[str, Any]:
    """POST /api/graphql/ and return parsed JSON.

    Facebook may return NDJSON for paginated queries — we parse the first JSON
    object only and treat the rest as deferred fragments (which we don't need
    for search/listing/suggest).

    On stale-token errors we transparently call `cookie_refresh.refresh()` to
    pull fresh fb_dtsg/lsd from a /marketplace/ HTML fetch, persist the
    updated auth, and retry the request once. Disable with auto_refresh=False.
    """
    did = doc_id or DOC_IDS.get(friendly_name)
    if not did:
        raise FBError(f"Unknown friendly_name: {friendly_name}. Add it to queries.DOC_IDS.")

    try:
        return _graphql_once(auth, friendly_name, did, variables, referer=referer, timeout=timeout)
    except (AuthExpiredError, FBError) as e:
        if not auto_refresh or not _is_token_stale_error(e):
            raise
        # In-place refresh: fetch new fb_dtsg/lsd from the cookies we have.
        from fb_cli import auth as auth_mod, cookie_refresh

        try:
            refreshed = cookie_refresh.refresh(auth)
        except cookie_refresh.CookieRefreshError as refresh_err:
            raise AuthExpiredError(
                f"{e}\n  cookie refresh also failed: {refresh_err}\n"
                "  next: run `fb-cli auth import-browser` (auto-launches Chrome)."
            ) from e
        auth.update(refreshed)
        try:
            auth_mod.save_auth(auth)
        except OSError:
            pass
        return _graphql_once(auth, friendly_name, did, variables, referer=referer, timeout=timeout)


def _is_token_stale_error(err: Exception) -> bool:
    msg = str(err)
    return isinstance(err, AuthExpiredError) or any(h in msg for h in _STALE_TOKEN_HINTS)


def _graphql_once(
    auth: dict[str, Any],
    friendly_name: str,
    did: str,
    variables: dict[str, Any],
    *,
    referer: str,
    timeout: int,
) -> dict[str, Any]:

    body = urllib.parse.urlencode(_form_fields(auth, friendly_name, did, variables)).encode()
    cookie_header = "; ".join(f"{k}={v}" for k, v in auth["cookies"].items())

    req = urllib.request.Request(
        GRAPHQL_URL,
        data=body,
        method="POST",
        headers={
            "User-Agent": auth.get("user_agent")
            or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:151.0) Gecko/20100101 Firefox/151.0",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-FB-Friendly-Name": friendly_name,
            "X-FB-LSD": auth["lsd"],
            "X-ASBD-ID": "359341",
            "Origin": "https://www.facebook.com",
            "Referer": referer,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Cookie": cookie_header,
        },
    )

    ctx = ssl.create_default_context()
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read()
        text = _decode(body, e.headers.get("Content-Encoding"))
        if e.code in (401, 403):
            raise AuthExpiredError(f"HTTP {e.code}: {text[:300]}") from e
        raise FBError(f"HTTP {e.code}: {text[:300]}") from e

    text = _decode(resp.read(), resp.headers.get("Content-Encoding"))
    if not text.strip():
        raise FBError("empty response (likely auth expired or doc_id stale)")

    # Take first JSON object — FB streams may concat multiple. Some legacy-ish
    # error responses are guarded with Facebook's anti-JSON-hijacking prefix.
    first = text.split("\n", 1)[0].strip()
    if first.startswith("for (;;);"):
        first = first.removeprefix("for (;;);")
    try:
        data = json.loads(first)
    except json.JSONDecodeError as e:
        raise FBError(f"non-JSON response: {first[:300]}") from e

    if data.get("error"):
        summary = data.get("errorSummary") or "Facebook rejected the request"
        description = data.get("errorDescription") or ""
        code = data.get("error")
        hint = " Run `fb-cli auth refresh` (cheap), then `fb-cli auth import-browser` if that fails."
        raise FBError(f"Facebook error {code}: {summary}. {description}{hint}".strip())

    if "errors" in data:
        msgs = [
            err.get("message") or err.get("description") or str(err)
            for err in data.get("errors", [])
        ]
        joined = "; ".join(msgs)
        if any("Login required" in m or "session" in m.lower() for m in msgs):
            raise AuthExpiredError(joined)
        raise FBError(f"GraphQL errors: {joined}")

    if not data.get("data"):
        raise FBError(f"no data in response: {first[:300]}")

    return data
