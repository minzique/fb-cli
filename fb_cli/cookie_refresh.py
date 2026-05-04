"""Refresh fb_dtsg / lsd / __rev / __hsi from a single HTTP fetch.

The reverse-engineered Facebook auth model:

  - Long-lived cookies (`c_user`, `xs`, `datr`, `fr`, `sb`) are the ACTUAL
    persistent session. Checking "Save login info" at login sets `xs` with
    `Max-Age=31536000` (1 year) — that is FB's "remember me".
  - `fb_dtsg` and `lsd` are page-bound CSRF tokens embedded in the HTML/JS of
    every facebook.com page. They rotate every few hours but can be re-derived
    from any successful page load that was authenticated by the long-lived
    cookies above.

So as long as `xs` is alive (typically up to a year), we can refresh
fb_dtsg/lsd by fetching `https://www.facebook.com/marketplace/` over plain
HTTPS — no browser needed. This module does exactly that.

Stdlib only.
"""
from __future__ import annotations

import gzip
import re
import ssl
import time
import urllib.error
import urllib.request
import zlib
from typing import Any

REFRESH_URL = "https://www.facebook.com/marketplace/"

# Patterns Facebook uses to embed CSRF tokens / build identifiers in HTML.
# We try several variants because the markup differs between the `m.facebook`,
# legacy and Comet (React) bundles.
_PATTERNS = {
    "fb_dtsg": [
        re.compile(r'"DTSGInitialData"\s*,\s*\[\s*\]\s*,\s*\{\s*"token"\s*:\s*"([^"]+)"'),
        re.compile(r'"dtsg"\s*:\s*\{\s*"token"\s*:\s*"([^"]+)"'),
        re.compile(r'name="fb_dtsg"\s+value="([^"]+)"'),
    ],
    "lsd": [
        re.compile(r'"LSD"\s*,\s*\[\s*\]\s*,\s*\{\s*"token"\s*:\s*"([^"]+)"'),
        re.compile(r'name="lsd"\s+value="([^"]+)"'),
    ],
    "rev": [
        re.compile(r'"client_revision"\s*:\s*(\d+)'),
        re.compile(r'"server_revision"\s*:\s*(\d+)'),
        re.compile(r'"__spin_r"\s*:\s*(\d+)'),
    ],
    "hsi": [
        re.compile(r'"hsi"\s*:\s*"(\d+)"'),
    ],
    "hs": [
        re.compile(r'"haste_session"\s*:\s*"([^"]+)"'),
    ],
}


class CookieRefreshError(RuntimeError):
    """Cookies are dead, request was blocked, or HTML didn't contain tokens."""


def refresh(
    auth: dict[str, Any],
    *,
    url: str = REFRESH_URL,
    timeout: int = 20,
) -> dict[str, Any]:
    """Return a NEW auth dict with fb_dtsg/lsd/rev/hsi/hs refreshed in place.

    Does not mutate the input. Cookies are carried through unchanged — if FB
    rotates a `Set-Cookie`, that's fine, the long-lived cookies stay valid.

    Raises CookieRefreshError if the HTML didn't contain refreshable tokens
    (usually means the cookies expired and FB redirected us to a login page).
    """
    cookies = auth.get("cookies") or {}
    missing = [c for c in ("c_user", "xs", "datr") if not cookies.get(c)]
    if missing:
        raise CookieRefreshError(
            f"auth missing required long-lived cookies: {missing}. "
            "Run `fb-cli auth import-browser` to get a fresh session."
        )

    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items())
    user_agent = (
        auth.get("user_agent")
        or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Cookie": cookie_header,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        },
    )

    ctx = ssl.create_default_context()
    try:
        resp = urllib.request.urlopen(req, context=ctx, timeout=timeout)
    except urllib.error.HTTPError as e:
        body = e.read()[:500]
        raise CookieRefreshError(f"HTTP {e.code} fetching {url}: {body!r}") from e
    except urllib.error.URLError as e:
        raise CookieRefreshError(f"could not reach {url}: {e.reason}") from e

    raw = resp.read()
    encoding = resp.headers.get("Content-Encoding")
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    elif encoding == "deflate":
        raw = zlib.decompress(raw)
    html = raw.decode("utf-8", errors="replace")

    # Login-wall detection: an unauthenticated HTML page won't contain DTSGInitialData.
    extracted: dict[str, str] = {}
    for key, patterns in _PATTERNS.items():
        for pat in patterns:
            m = pat.search(html)
            if m:
                extracted[key] = m.group(1)
                break

    if "fb_dtsg" not in extracted or "lsd" not in extracted:
        if _looks_like_login_page(html):
            raise CookieRefreshError(
                "Facebook redirected to a login page — long-lived cookies (xs) are dead. "
                "Run `fb-cli auth chrome login` and re-import via "
                "`fb-cli auth import-browser`."
            )
        raise CookieRefreshError(
            "Did not find fb_dtsg/lsd in /marketplace/ HTML. Facebook may have rotated "
            "their bundle layout — please file an issue."
        )

    refreshed = dict(auth)
    refreshed["fb_dtsg"] = extracted["fb_dtsg"]
    refreshed["lsd"] = extracted["lsd"]
    refreshed["jazoest"] = _jazoest(extracted["fb_dtsg"])
    if "rev" in extracted:
        refreshed["rev"] = extracted["rev"]
    if "hsi" in extracted:
        refreshed["hsi"] = extracted["hsi"]
    if "hs" in extracted:
        refreshed["hs"] = extracted["hs"]
    refreshed["captured_at"] = int(time.time())
    refreshed["source_har"] = "cookie-refresh:" + url
    refreshed.setdefault("user_agent", user_agent)
    return refreshed


def _looks_like_login_page(html: str) -> bool:
    indicators = ('id="loginbutton"', 'name="email"', "/login/?", "Log in to Facebook")
    return any(s in html for s in indicators)


def _jazoest(fb_dtsg: str) -> str:
    return "2" + str(sum(ord(ch) for ch in fb_dtsg))
