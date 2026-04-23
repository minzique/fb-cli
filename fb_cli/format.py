"""Output helpers — JSONL by default, pretty table on demand."""
from __future__ import annotations

import json
import shutil
from typing import Any, Iterable


def jsonl(records: Iterable[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(r, ensure_ascii=False) for r in records)


def table(records: list[dict[str, Any]], cols: list[tuple[str, str, int]]) -> str:
    """cols: list of (header, key, max_width). Width 0 = no cap."""
    term = shutil.get_terminal_size((140, 24))
    rows: list[list[str]] = []
    for r in records:
        row = []
        for header, key, w in cols:
            v = r.get(key)
            s = "" if v is None else str(v)
            if w > 0 and len(s) > w:
                s = s[: w - 1] + "…"
            row.append(s)
        rows.append(row)

    widths = []
    for i, (header, _, max_w) in enumerate(cols):
        col_max = max([len(header)] + [len(r[i]) for r in rows]) if rows else len(header)
        widths.append(min(col_max, max_w) if max_w > 0 else col_max)

    # Shrink to fit terminal
    total = sum(widths) + 3 * (len(widths) - 1)
    if total > term.columns and len(widths) > 1:
        overflow = total - term.columns
        # shave from the widest column
        widest = widths.index(max(widths))
        widths[widest] = max(8, widths[widest] - overflow)

    def fmt(values: list[str]) -> str:
        out = []
        for v, w in zip(values, widths):
            if len(v) > w:
                v = v[: w - 1] + "…"
            out.append(v.ljust(w))
        return "   ".join(out).rstrip()

    headers = [h for h, _, _ in cols]
    sep = "   ".join("─" * w for w in widths)
    lines = [fmt(headers), sep]
    lines.extend(fmt(r) for r in rows)
    return "\n".join(lines)


SEARCH_COLS: list[tuple[str, str, int]] = [
    ("PRICE", "price_formatted", 14),
    ("CITY", "city", 18),
    ("AGO", "_ago", 6),
    ("TITLE", "title", 60),
    ("URL", "url", 0),
]


def format_search(listings: list[dict[str, Any]], *, fmt: str = "table") -> str:
    if fmt == "json":
        return json.dumps(listings, indent=2, ensure_ascii=False)
    if fmt == "jsonl":
        return jsonl(listings)
    # decorate with _ago for table
    import time

    now = int(time.time())
    deco = []
    for L in listings:
        c = L.get("creation_time")
        ago = "?"
        if isinstance(c, (int, float)):
            delta = max(0, now - int(c))
            if delta < 3600:
                ago = f"{delta // 60}m"
            elif delta < 86400:
                ago = f"{delta // 3600}h"
            else:
                ago = f"{delta // 86400}d"
        deco.append({**L, "_ago": ago})
    return table(deco, SEARCH_COLS)
