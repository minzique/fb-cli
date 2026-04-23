"""Saved-search monitors with new-listing diffing.

Each watch is a JSON file at ~/.fb-cli/watches/<name>.json with the search
config + a list of seen listing IDs. `check` runs the search, returns only
the listings whose ID hasn't been seen, then appends them.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from fb_cli.auth import AUTH_DIR

WATCH_DIR = AUTH_DIR / "watches"


def _ensure() -> None:
    WATCH_DIR.mkdir(parents=True, exist_ok=True)


def _path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return WATCH_DIR / f"{safe}.json"


def add(name: str, query: str, **filters: Any) -> Path:
    _ensure()
    p = _path(name)
    data = {
        "name": name,
        "query": query,
        "filters": filters,
        "created_at": int(time.time()),
        "last_checked_at": None,
        "seen_ids": [],
    }
    p.write_text(json.dumps(data, indent=2))
    return p


def remove(name: str) -> bool:
    p = _path(name)
    if p.exists():
        p.unlink()
        return True
    return False


def get(name: str) -> dict[str, Any]:
    p = _path(name)
    if not p.exists():
        raise FileNotFoundError(f"watch '{name}' not found at {p}")
    return json.loads(p.read_text())


def list_all() -> list[dict[str, Any]]:
    if not WATCH_DIR.exists():
        return []
    out = []
    for p in sorted(WATCH_DIR.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return out


def mark_seen(name: str, ids: list[str]) -> None:
    p = _path(name)
    data = json.loads(p.read_text())
    seen = set(data.get("seen_ids", []))
    seen.update(ids)
    data["seen_ids"] = sorted(seen)
    data["last_checked_at"] = int(time.time())
    p.write_text(json.dumps(data, indent=2))


def diff_new(name: str, listings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    data = get(name)
    seen = set(str(i) for i in data.get("seen_ids", []))
    return [L for L in listings if str(L.get("id")) not in seen]
