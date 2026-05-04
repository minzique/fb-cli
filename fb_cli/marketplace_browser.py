"""Browser-backed Marketplace helpers.

These commands drive fb-cli's managed Chrome through CDP. They are the escape
hatch when Facebook's web UI can do something our captured GraphQL variables do
not yet model (sort/filter encodings, layout experiments, visible page state).
"""
from __future__ import annotations

import base64
import json
import time
import urllib.parse
from pathlib import Path
from typing import Any

from fb_cli import cdp, chrome_launcher

DEFAULT_WAIT_SECONDS = 4.0
MARKETPLACE_HOME = "https://www.facebook.com/marketplace/"
MARKETPLACE_SEARCH = "https://www.facebook.com/marketplace/search/"


class BrowserError(RuntimeError):
    """Browser-backed Marketplace command failed."""


def browser_status(debug_url: str = chrome_launcher.DEFAULT_DEBUG_URL) -> dict[str, Any]:
    status = chrome_launcher.status(debug_url)
    targets: list[dict[str, str]] = []
    if status.get("running"):
        try:
            targets = [t.__dict__ for t in cdp.list_targets(debug_url)]
        except cdp.CDPError:
            targets = []
    return {**status, "targets": targets}


def open_url(
    url: str,
    *,
    debug_url: str = chrome_launcher.DEFAULT_DEBUG_URL,
    launch: bool = True,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> dict[str, Any]:
    with _session(debug_url=debug_url, launch=launch) as session:
        session.navigate(url, wait_seconds=wait_seconds)
        return session.page_info()


def search_url(
    query: str,
    *,
    min_price: float | None = None,
    max_price: float | None = None,
    radius: int | None = None,
    days: int | None = None,
    sort: str | None = None,
) -> str:
    params: dict[str, str] = {"query": query}
    if min_price is not None:
        params["minPrice"] = _money_param(min_price)
    if max_price is not None:
        params["maxPrice"] = _money_param(max_price)
    if radius is not None:
        params["radiusKM"] = str(radius)
    if days is not None:
        params["daysSinceListed"] = str(days)
    if sort:
        params["sortBy"] = sort
    return MARKETPLACE_SEARCH + "?" + urllib.parse.urlencode(params)


def search(
    query: str,
    *,
    debug_url: str = chrome_launcher.DEFAULT_DEBUG_URL,
    launch: bool = True,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    limit: int = 24,
    min_price: float | None = None,
    max_price: float | None = None,
    radius: int | None = None,
    days: int | None = None,
    sort: str | None = None,
) -> list[dict[str, Any]]:
    url = search_url(
        query,
        min_price=min_price,
        max_price=max_price,
        radius=radius,
        days=days,
        sort=sort,
    )
    with _session(debug_url=debug_url, launch=launch) as session:
        session.navigate(url, wait_seconds=wait_seconds)
        return session.extract_listings(limit=limit)


def extract(
    *,
    debug_url: str = chrome_launcher.DEFAULT_DEBUG_URL,
    launch: bool = True,
    limit: int = 48,
) -> list[dict[str, Any]]:
    with _session(debug_url=debug_url, launch=launch) as session:
        return session.extract_listings(limit=limit)


def scroll(
    *,
    debug_url: str = chrome_launcher.DEFAULT_DEBUG_URL,
    launch: bool = True,
    steps: int = 1,
    delay: float = 1.0,
) -> dict[str, Any]:
    with _session(debug_url=debug_url, launch=launch) as session:
        last: dict[str, Any] = {}
        for _ in range(max(1, steps)):
            last = session.evaluate(
                "(window.scrollBy({top: Math.max(document.documentElement.clientHeight, 800), behavior: 'instant'}), "
                "{scrollY: window.scrollY, height: document.documentElement.scrollHeight})"
            )
            time.sleep(max(0.0, delay))
        return last


def screenshot(
    path: str | Path,
    *,
    debug_url: str = chrome_launcher.DEFAULT_DEBUG_URL,
    launch: bool = True,
) -> Path:
    with _session(debug_url=debug_url, launch=launch) as session:
        raw = session.screenshot()
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    return out


def unsafe_eval(
    expression: str,
    *,
    debug_url: str = chrome_launcher.DEFAULT_DEBUG_URL,
    launch: bool = True,
) -> Any:
    with _session(debug_url=debug_url, launch=launch) as session:
        return session.evaluate(expression)


def _money_param(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value)


class _BrowserSession:
    def __init__(self, debug_url: str, launch: bool) -> None:
        self.debug_url = debug_url
        self.launch = launch
        self.client: cdp.CDPClient | None = None

    def __enter__(self) -> "_BrowserSession":
        if self.launch and not chrome_launcher.is_running(self.debug_url):
            try:
                chrome_launcher.start(landing_url=MARKETPLACE_HOME)
            except chrome_launcher.ChromeLauncherError as e:
                raise BrowserError(str(e)) from e
        try:
            target = cdp.choose_target(cdp.list_targets(self.debug_url), prefer_marketplace=True)
            self.client = cdp.CDPClient(target.websocket_url)
            self.client.call("Runtime.enable")
            self.client.call("Page.enable")
            return self
        except cdp.CDPError as e:
            raise BrowserError(str(e)) from e

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        if self.client:
            self.client.close()

    def navigate(self, url: str, *, wait_seconds: float) -> None:
        self._client.call("Page.navigate", {"url": url}, timeout=10)
        self.wait_ready(timeout=max(3.0, wait_seconds))
        if wait_seconds > 0:
            time.sleep(wait_seconds)

    def wait_ready(self, *, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                state = self.evaluate("document.readyState")
            except BrowserError:
                time.sleep(0.25)
                continue
            if state in ("interactive", "complete"):
                return
            time.sleep(0.25)

    def page_info(self) -> dict[str, Any]:
        value = self.evaluate(
            "({url: location.href, title: document.title, readyState: document.readyState, "
            "scrollY: window.scrollY, height: document.documentElement.scrollHeight})"
        )
        return value if isinstance(value, dict) else {"value": value}

    def extract_listings(self, *, limit: int) -> list[dict[str, Any]]:
        value = self.evaluate(_EXTRACT_LISTINGS_JS.replace("__LIMIT__", str(max(1, limit))))
        if not isinstance(value, list):
            raise BrowserError(f"listing extraction returned {type(value).__name__}, expected list")
        return [item for item in value if isinstance(item, dict)]

    def screenshot(self) -> bytes:
        try:
            resp = self._client.call("Page.captureScreenshot", {"format": "png", "fromSurface": True}, timeout=10)
        except cdp.CDPError as e:
            raise BrowserError(str(e)) from e
        data = (((resp.get("result") or {}).get("data")))
        if not isinstance(data, str):
            raise BrowserError("Chrome did not return screenshot data")
        return base64.b64decode(data)

    def evaluate(self, expression: str) -> Any:
        wrapped = f"(() => {{ return ({expression}); }})()"
        try:
            resp = self._client.call(
                "Runtime.evaluate",
                {"expression": wrapped, "returnByValue": True, "awaitPromise": True},
                timeout=10,
            )
        except cdp.CDPError as e:
            raise BrowserError(str(e)) from e
        result = (resp.get("result") or {}).get("result") or {}
        if result.get("subtype") == "error":
            raise BrowserError(str(result.get("description") or result.get("value") or "JavaScript error"))
        if "value" in result:
            return result["value"]
        if "unserializableValue" in result:
            return result["unserializableValue"]
        return None

    @property
    def _client(self) -> cdp.CDPClient:
        if self.client is None:
            raise BrowserError("browser session is not connected")
        return self.client


def _session(*, debug_url: str, launch: bool) -> _BrowserSession:
    return _BrowserSession(debug_url, launch)


_EXTRACT_LISTINGS_JS = r"""
(() => {
  const limit = __LIMIT__;
  const seen = new Set();
  const money = /(?:LKR|Rs\.?|₨|\$|USD|FREE|Free|\b\d[\d,.]*\s*(?:LKR|Rs\.?)\b)/i;
  const clean = (s) => String(s || '').replace(/\s+/g, ' ').trim();
  const linesFor = (el) => String((el && el.innerText) || '').split(/\n+/).map(clean).filter(Boolean);
  const priceNumber = (s) => {
    if (/^\s*(free|lkr\s*0|rs\.?\s*0)\b/i.test(String(s || ''))) return 0;
    const m = String(s || '').replace(/,/g, '').match(/(?:LKR|Rs\.?)\s*(\d+(?:\.\d+)?)/i) || String(s || '').replace(/,/g, '').match(/(\d+(?:\.\d+)?)/);
    return m ? Number(m[1]) : null;
  };
  const itemId = (href) => {
    const m = String(href || '').match(/\/marketplace\/item\/(\d+)/);
    return m ? m[1] : null;
  };
  const bestCard = (anchor) => {
    let best = anchor;
    let node = anchor;
    for (let i = 0; i < 8 && node; i += 1, node = node.parentElement) {
      const lines = linesFor(node);
      if (lines.length >= 2 && lines.length <= 30) best = node;
    }
    return best;
  };
  const anchors = Array.from(document.querySelectorAll('a[href*="/marketplace/item/"]'));
  const out = [];
  for (const anchor of anchors) {
    const url = new URL(anchor.href, location.href).href.split('?')[0];
    const id = itemId(url);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const card = bestCard(anchor);
    const label = clean(anchor.getAttribute('aria-label'));
    const lines = Array.from(new Set(linesFor(card).filter((line) => !/^Sponsored$/i.test(line))));
    const labelPrice = (label.match(/(?:LKR|Rs\.?)\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|Free/i) || [null])[0];
    const priceLine = labelPrice || lines.find((line) => money.test(line)) || null;
    const titleFromLabel = label ? clean(label.split(/,\s*(?:LKR|Rs\.?|Free\b)/i)[0]) : null;
    const titleLine = titleFromLabel || lines.find((line) => line !== priceLine && !/^Marketplace$/i.test(line)) || clean(anchor.textContent) || null;
    const labelParts = label.split(',').map(clean).filter(Boolean);
    const city = labelParts.length >= 3 ? labelParts[labelParts.length - 2] : null;
    const img = card.querySelector('img');
    out.push({
      id,
      url,
      title: titleLine,
      price_formatted: priceLine,
      price: priceNumber(priceLine),
      city,
      creation_time: null,
      primary_photo: img ? img.src : null,
      text: lines.length ? lines.join('\n') : label,
      source: 'browser',
      visible_index: out.length,
    });
    if (out.length >= limit) break;
  }
  return out;
})()
"""
