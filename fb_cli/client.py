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
) -> dict[str, Any]:
    """POST /api/graphql/ and return parsed JSON.

    Facebook may return NDJSON for paginated queries — we parse the first JSON
    object only and treat the rest as deferred fragments (which we don't need
    for search/listing/suggest).
    """
    did = doc_id or DOC_IDS.get(friendly_name)
    if not did:
        raise FBError(f"Unknown friendly_name: {friendly_name}. Add it to queries.DOC_IDS.")

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

    # Take first JSON object — FB streams may concat multiple
    first = text.split("\n", 1)[0].strip()
    try:
        data = json.loads(first)
    except json.JSONDecodeError as e:
        raise FBError(f"non-JSON response: {first[:300]}") from e

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
