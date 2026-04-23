"""Auth state: HAR import, store, load.

State file layout (~/.fb-cli/auth.json, mode 600):
{
  "user_id": "100050276541062",
  "user_agent": "Mozilla/5.0 ...",
  "cookies": {"c_user": "...", "xs": "...", "datr": "...", "fr": "...", "sb": "...", ...},
  "fb_dtsg": "...",
  "lsd": "...",
  "jazoest": "...",
  "rev": "1037953373",
  "hsi": "7631844899988393266",
  "hs": "20566.HYP:comet_pkg.2.1...0",
  "buy_location": {"latitude": 6.84083, "longitude": 80.0139, "id": "112661478746814", "city": "Mawathgama"},
  "captured_at": 1776927360,
  "source_har": "/path/to/file.har"
}
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any

AUTH_DIR = Path(os.environ.get("FB_CLI_HOME") or (Path.home() / ".fb-cli"))
AUTH_FILE = AUTH_DIR / "auth.json"

REQUIRED_COOKIES = ("c_user", "xs", "datr")
INTERESTING_COOKIES = ("c_user", "xs", "datr", "fr", "sb", "presence", "wd", "dpr")


def _ensure_dir() -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(AUTH_DIR, 0o700)
    except OSError:
        pass


def load_auth() -> dict[str, Any]:
    if not AUTH_FILE.exists():
        raise FileNotFoundError(
            f"No auth at {AUTH_FILE}. Run: fb-cli auth import-har <file.har>"
        )
    with AUTH_FILE.open() as f:
        return json.load(f)


def save_auth(auth: dict[str, Any]) -> Path:
    _ensure_dir()
    AUTH_FILE.write_text(json.dumps(auth, indent=2, sort_keys=True))
    try:
        os.chmod(AUTH_FILE, 0o600)
    except OSError:
        pass
    return AUTH_FILE


def import_har(har_path: str) -> dict[str, Any]:
    """Parse a HAR file from facebook.com and extract auth state."""
    p = Path(har_path).expanduser()
    if not p.exists():
        raise FileNotFoundError(p)
    with p.open() as f:
        har = json.load(f)
    entries = har.get("log", {}).get("entries", [])

    # Pick the first POST /api/graphql/ entry — it has fb_dtsg + lsd + cookies
    gql = next(
        (
            e
            for e in entries
            if e["request"]["url"].endswith("/api/graphql/")
            and e["request"].get("postData")
        ),
        None,
    )
    if gql is None:
        raise ValueError(
            "HAR has no POST /api/graphql/ entries. Re-record while browsing facebook.com/marketplace."
        )

    headers = {h["name"]: h["value"] for h in gql["request"]["headers"]}
    cookies = {c["name"]: c["value"] for c in gql["request"].get("cookies", [])}

    # Union cookies across entries to get the freshest values
    for e in entries:
        for c in e["request"].get("cookies", []):
            cookies.setdefault(c["name"], c["value"])

    missing = [c for c in REQUIRED_COOKIES if c not in cookies]
    if missing:
        raise ValueError(
            f"HAR is missing required cookies: {missing}. Re-record while logged into facebook.com."
        )

    body = gql["request"]["postData"]["text"]
    form = urllib.parse.parse_qs(body)

    def f1(key: str, default: str = "") -> str:
        v = form.get(key, [default])
        return v[0] if v else default

    auth: dict[str, Any] = {
        "user_id": cookies.get("c_user", f1("__user")),
        "user_agent": headers.get("User-Agent", ""),
        "cookies": {k: cookies[k] for k in cookies if k in INTERESTING_COOKIES or k.startswith("locale")},
        "fb_dtsg": f1("fb_dtsg"),
        "lsd": f1("lsd"),
        "jazoest": f1("jazoest"),
        "rev": f1("__rev"),
        "hsi": f1("__hsi"),
        "hs": f1("__hs", "20566.HYP:comet_pkg.2.1...0"),
        "buy_location": _scan_buy_location(entries),
        "captured_at": int(time.time()),
        "source_har": str(p),
    }
    return auth


def _scan_buy_location(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Pull buyLocation from the first request that has it.

    Marketplace requests embed `buyLocation: {latitude, longitude}`. We also try
    to capture the FB location_id (used by saved searches and topic pages).
    """
    out: dict[str, Any] = {}
    for e in entries:
        body = e.get("request", {}).get("postData", {}).get("text", "")
        if "buyLocation" not in body:
            continue
        form = urllib.parse.parse_qs(body)
        try:
            v = json.loads(form.get("variables", ["{}"])[0])
        except json.JSONDecodeError:
            continue
        bl = v.get("buyLocation")
        if isinstance(bl, dict) and "latitude" in bl:
            out.setdefault("latitude", bl["latitude"])
            out.setdefault("longitude", bl["longitude"])
        # Also look for location_id buried in searchPopularSearchesParams / topicPageParams
        for key in ("searchPopularSearchesParams", "topicPageParams"):
            sub = v.get(key)
            if isinstance(sub, dict) and "location_id" in sub:
                out.setdefault("location_id", sub["location_id"])
    return out


def status() -> dict[str, Any]:
    auth = load_auth()
    age = int(time.time()) - int(auth.get("captured_at", 0))
    days = age / 86400
    return {
        "user_id": auth.get("user_id"),
        "captured_at": auth.get("captured_at"),
        "age_days": round(days, 1),
        "buy_location": auth.get("buy_location"),
        "cookie_keys": sorted(auth.get("cookies", {}).keys()),
        "auth_file": str(AUTH_FILE),
    }
