"""Import Facebook auth state from a live Chrome DevTools session.

Stdlib-only on purpose: fb-cli has no runtime dependencies, so this file carries a
small Chrome DevTools Protocol websocket client instead of depending on
websocket-client or pychrome.
"""
from __future__ import annotations

import base64
import json
import os
import select
import socket
import ssl
import struct
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

INTERESTING_COOKIES = ("c_user", "xs", "datr", "fr", "sb", "presence", "wd", "dpr")
REQUIRED_COOKIES = ("c_user", "xs", "datr")
DEFAULT_DEBUG_URL = "http://127.0.0.1:9222"
DEFAULT_MARKETPLACE_URL = "https://www.facebook.com/marketplace/search/?query={query}"


class BrowserAuthError(RuntimeError):
    """Chrome/CDP auth import could not produce a usable auth file."""


@dataclass(frozen=True)
class Target:
    id: str
    url: str
    title: str
    websocket_url: str


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
    with _CDPClient(target.websocket_url) as cdp:
        cdp.call("Runtime.enable")
        cdp.call("Network.enable", {"maxPostDataSize": 1024 * 1024})
        cdp.call("Page.enable")

        page_data = _evaluate_page_data(cdp)
        graphql_forms = _capture_graphql_forms(
            cdp,
            target_url=target.url,
            search_query=search_query,
            timeout=timeout,
            navigate=navigate,
        )
        cookies = _facebook_cookies(cdp.call("Network.getAllCookies"))
        form = _best_graphql_form(graphql_forms)

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


def _list_targets(debug_url: str) -> list[Target]:
    url = debug_url.rstrip("/") + "/json"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
    except OSError as e:
        raise BrowserAuthError(
            f"Could not connect to Chrome DevTools at {debug_url}. "
            "Run `fb-cli auth chrome start` (or pass --launch)."
        ) from e

    data = json.loads(raw)
    targets: list[Target] = []
    for item in data:
        ws = item.get("webSocketDebuggerUrl")
        if item.get("type") == "page" and ws:
            targets.append(
                Target(
                    id=str(item.get("id", "")),
                    url=str(item.get("url", "")),
                    title=str(item.get("title", "")),
                    websocket_url=str(ws),
                )
            )
    if not targets:
        raise BrowserAuthError("Chrome DevTools has no page targets. Open Facebook Marketplace and retry.")
    return targets


def _choose_target(targets: list[Target]) -> Target:
    def score(t: Target) -> tuple[int, int]:
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


def _evaluate_page_data(cdp: "_CDPClient") -> dict[str, str]:
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
        resp = cdp.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
    except BrowserAuthError:
        return {}
    value = ((resp.get("result") or {}).get("result") or {}).get("value")
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def _capture_graphql_forms(
    cdp: "_CDPClient",
    *,
    target_url: str,
    search_query: str,
    timeout: int,
    navigate: bool,
) -> list[dict[str, str]]:
    if navigate:
        if "facebook.com/marketplace" in target_url.lower():
            cdp.call("Page.reload", {"ignoreCache": True})
        else:
            url = DEFAULT_MARKETPLACE_URL.format(query=urllib.parse.quote(search_query))
            cdp.call("Page.navigate", {"url": url})

    forms: list[dict[str, str]] = []
    deadline = time.monotonic() + max(1, timeout)
    while time.monotonic() < deadline:
        msg = cdp.recv_json(timeout=max(0.1, min(1.0, deadline - time.monotonic())))
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


class _CDPClient:
    def __init__(self, websocket_url: str) -> None:
        self.websocket_url = websocket_url
        self._sock = self._connect(websocket_url)
        self._next_id = 0

    def __enter__(self) -> "_CDPClient":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._send_frame(b"", opcode=0x8)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 5) -> dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        payload: dict[str, Any] = {"id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send_json(payload)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = self.recv_json(timeout=max(0.1, min(1.0, deadline - time.monotonic())))
            if not msg:
                continue
            if msg.get("id") != msg_id:
                continue
            if "error" in msg:
                raise BrowserAuthError(f"CDP {method} failed: {msg['error']}")
            return msg
        raise BrowserAuthError(f"Timed out waiting for CDP {method}")

    def recv_json(self, *, timeout: float) -> dict[str, Any] | None:
        ready, _, _ = select.select([self._sock], [], [], timeout)
        if not ready:
            return None
        data = self._recv_message()
        if data is None:
            return None
        return json.loads(data.decode("utf-8"))

    def _send_json(self, payload: dict[str, Any]) -> None:
        self._send_frame(json.dumps(payload, separators=(",", ":")).encode("utf-8"), opcode=0x1)

    @staticmethod
    def _connect(websocket_url: str) -> socket.socket:
        parsed = urllib.parse.urlparse(websocket_url)
        if parsed.scheme not in ("ws", "wss"):
            raise BrowserAuthError(f"Unsupported websocket URL: {websocket_url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        raw_sock = socket.create_connection((host, port), timeout=5)
        sock = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=host) if parsed.scheme == "wss" else raw_sock
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise BrowserAuthError(f"Chrome rejected websocket handshake: {response[:200]!r}")
        return sock

    def _send_frame(self, payload: bytes, *, opcode: int) -> None:
        # Client websocket frames must be masked.
        first = 0x80 | opcode
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", first, 0x80 | length)
        elif length < 65536:
            header = struct.pack("!BBH", first, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", first, 0x80 | 127, length)
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        self._sock.sendall(header + mask + masked)

    def _recv_message(self) -> bytes | None:
        chunks: list[bytes] = []
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode == 0x8:  # close
                return None
            if opcode == 0x9:  # ping
                self._send_frame(payload, opcode=0xA)
                continue
            if opcode in (0x1, 0x2, 0x0):
                chunks.append(payload)
                if fin:
                    return b"".join(chunks)

    def _recv_frame(self) -> tuple[bool, int, bytes]:
        header = self._recv_exact(2)
        first, second = header[0], header[1]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return fin, opcode, payload

    def _recv_exact(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self._sock.recv(size - len(data))
            if not chunk:
                raise BrowserAuthError("Chrome DevTools websocket closed unexpectedly")
            data += chunk
        return data
