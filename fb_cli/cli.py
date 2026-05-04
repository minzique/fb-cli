"""fb-cli — command-line entry point."""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from fb_cli import (
    __version__,
    auth as auth_mod,
    browser_auth,
    chrome_launcher,
    client,
    cookie_refresh,
    parser as parse_mod,
    queries,
    watch,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="fb-cli",
        description="Unofficial Facebook Marketplace CLI (HAR-imported cookie auth).",
    )
    p.add_argument("--version", action="version", version=f"fb-cli {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    # auth
    pa = sub.add_parser("auth", help="manage Facebook auth state")
    pa_sub = pa.add_subparsers(dest="auth_cmd", required=True)
    pa_imp = pa_sub.add_parser("import-har", help="extract cookies + tokens from a facebook.com HAR")
    pa_imp.add_argument("har", help="path to .har file")
    pa_browser = pa_sub.add_parser(
        "import-browser",
        help="refresh cookies + tokens from a debug Chrome (auto-launches one)",
    )
    pa_browser.add_argument("--debug-url", default=browser_auth.DEFAULT_DEBUG_URL)
    pa_browser.add_argument("--timeout", type=int, default=15)
    pa_browser.add_argument(
        "--search-query",
        default="keyboard",
        help="Marketplace query to briefly load if a fresh GraphQL request is needed",
    )
    pa_browser.add_argument(
        "--no-navigate",
        action="store_true",
        help="do not reload/navigate the Facebook tab while capturing tokens",
    )
    pa_browser.add_argument(
        "--no-launch",
        action="store_true",
        help="do not auto-start the managed Chrome if no debug Chrome is reachable",
    )
    pa_browser.add_argument(
        "--copy-profile",
        action="store_true",
        help="on first launch, copy cookies/login from the user's default Chrome profile",
    )

    pa_refresh = pa_sub.add_parser(
        "refresh",
        help="refresh fb_dtsg/lsd from /marketplace/ HTML (no browser, uses existing cookies)",
    )
    pa_refresh.add_argument("--timeout", type=int, default=20)

    pa_sub.add_parser("status", help="show auth state freshness + buy_location")
    pa_sub.add_parser("show", help="dump full auth file (sensitive — for debugging)")
    pa_sub.add_parser(
        "doctor",
        help="diagnose auth state and recommend the cheapest recovery action",
    )

    pa_chrome = pa_sub.add_parser(
        "chrome",
        help="manage the long-lived debug Chrome that fb-cli owns",
    )
    pa_chrome_sub = pa_chrome.add_subparsers(dest="chrome_cmd", required=True)
    pa_chrome_start = pa_chrome_sub.add_parser("start", help="start the managed debug Chrome")
    pa_chrome_start.add_argument("--port", type=int, default=chrome_launcher.DEFAULT_PORT)
    pa_chrome_start.add_argument(
        "--copy-profile",
        action="store_true",
        help="seed the profile from the user's default Chrome (cookies + login)",
    )
    pa_chrome_start.add_argument(
        "--headless",
        action="store_true",
        help="run Chrome headlessly (only useful once already logged in)",
    )
    pa_chrome_sub.add_parser("stop", help="stop the managed debug Chrome")
    pa_chrome_sub.add_parser("status", help="show managed Chrome status")
    pa_chrome_login = pa_chrome_sub.add_parser(
        "login",
        help="open Facebook login page in the managed Chrome (one-time setup)",
    )
    pa_chrome_login.add_argument("--port", type=int, default=chrome_launcher.DEFAULT_PORT)

    # search
    ps = sub.add_parser("search", help="search marketplace")
    ps.add_argument("query")
    ps.add_argument("--lat", type=float, help="search center latitude (default: from auth)")
    ps.add_argument("--lng", type=float, help="search center longitude (default: from auth)")
    ps.add_argument("--radius", type=int, default=65, help="radius in km (default: 65)")
    ps.add_argument("--min", dest="min_price", type=float, help="min price")
    ps.add_argument("--max", dest="max_price", type=float, help="max price")
    ps.add_argument("--days", type=int, help="listed within last N days (1, 7, 30)")
    ps.add_argument(
        "--condition",
        action="append",
        choices=["NEW", "USED_LIKE_NEW", "USED_GOOD", "USED_FAIR"],
        help="filter by condition (repeat for multiple)",
    )
    ps.add_argument("--category", action="append", help="category id (repeat for multiple)")
    ps.add_argument(
        "--sort",
        choices=[
            "BEST_MATCH",
            "CREATION_TIME_DESCEND",
            "PRICE_ASCEND",
            "PRICE_DESCEND",
            "DISTANCE_ASCEND",
        ],
        help="result sort order",
    )
    ps.add_argument("--limit", type=int, default=24, help="max results to return")
    ps.add_argument("--pages", type=int, default=1, help="number of pages to fetch")
    ps.add_argument("--format", choices=["table", "json", "jsonl"], default="table")

    # listing
    pl = sub.add_parser("listing", help="get full listing details")
    pl.add_argument("id", help="marketplace listing id")
    pl.add_argument("--format", choices=["table", "json", "jsonl"], default="json")

    # suggest
    psu = sub.add_parser("suggest", help="autocomplete suggestions")
    psu.add_argument("query")
    psu.add_argument("--count", type=int, default=10)

    # watch
    pw = sub.add_parser("watch", help="saved-search monitors")
    pw_sub = pw.add_subparsers(dest="watch_cmd", required=True)
    pw_add = pw_sub.add_parser("add", help="create a new watch")
    pw_add.add_argument("name")
    pw_add.add_argument("--query", required=True)
    pw_add.add_argument("--lat", type=float)
    pw_add.add_argument("--lng", type=float)
    pw_add.add_argument("--radius", type=int, default=65)
    pw_add.add_argument("--min", dest="min_price", type=float)
    pw_add.add_argument("--max", dest="max_price", type=float)
    pw_add.add_argument("--days", type=int)
    pw_add.add_argument(
        "--condition",
        action="append",
        choices=["NEW", "USED_LIKE_NEW", "USED_GOOD", "USED_FAIR"],
    )
    pw_add.add_argument("--category", action="append")
    pw_add.add_argument("--sort")
    pw_sub.add_parser("list", help="list saved watches")
    pw_show = pw_sub.add_parser("show", help="show one watch's config + seen count")
    pw_show.add_argument("name")
    pw_chk = pw_sub.add_parser("check", help="re-run watches and print only NEW listings")
    pw_chk.add_argument("name", nargs="?", help="check this one (default: all)")
    pw_chk.add_argument("--format", choices=["table", "json", "jsonl"], default="table")
    pw_chk.add_argument(
        "--silent-noop", action="store_true",
        help="if no new listings, exit 0 with no output (good for cron)",
    )
    pw_rm = pw_sub.add_parser("rm", help="delete a watch")
    pw_rm.add_argument("name")

    return p


# --- handlers --------------------------------------------------------------


def _resolve_loc(auth: dict[str, Any], lat: float | None, lng: float | None) -> tuple[float, float, str | None]:
    bl = auth.get("buy_location") or {}
    lat = lat if lat is not None else bl.get("latitude")
    lng = lng if lng is not None else bl.get("longitude")
    if lat is None or lng is None:
        raise SystemExit(
            "no location set — pass --lat/--lng or re-record HAR while browsing marketplace with a location set"
        )
    return float(lat), float(lng), bl.get("location_id")


def cmd_auth_import(args: argparse.Namespace) -> int:
    a = auth_mod.import_har(args.har)
    p = auth_mod.save_auth(a)
    print(f"saved {p}")
    print(f"  user_id={a['user_id']}")
    print(f"  buy_location={a.get('buy_location')}")
    print(f"  cookies={sorted(a['cookies'])}")
    return 0


def cmd_auth_import_browser(args: argparse.Namespace) -> int:
    try:
        existing = auth_mod.load_auth()
    except FileNotFoundError:
        existing = None
    a = browser_auth.import_from_browser(
        debug_url=args.debug_url,
        timeout=args.timeout,
        search_query=args.search_query,
        navigate=not args.no_navigate,
        existing_auth=existing,
        launch=not args.no_launch,
        copy_profile=args.copy_profile,
    )
    p = auth_mod.save_auth(a)
    print(f"saved {p}")
    print(f"  user_id={a['user_id']}")
    print(f"  buy_location={a.get('buy_location')}")
    print(f"  cookies={sorted(a['cookies'])}")
    print("  source=Chrome DevTools")
    return 0


def cmd_auth_refresh(args: argparse.Namespace) -> int:
    try:
        existing = auth_mod.load_auth()
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 5
    try:
        refreshed = cookie_refresh.refresh(existing, timeout=args.timeout)
    except cookie_refresh.CookieRefreshError as e:
        print(f"refresh failed: {e}", file=sys.stderr)
        print(
            "  next: `fb-cli auth import-browser` (auto-launches the managed Chrome)",
            file=sys.stderr,
        )
        return 6
    p = auth_mod.save_auth(refreshed)
    print(f"refreshed {p}")
    print(f"  fb_dtsg={refreshed['fb_dtsg'][:12]}…  lsd={refreshed['lsd'][:8]}…")
    print(f"  rev={refreshed.get('rev')}  hsi={refreshed.get('hsi')}")
    print("  source=cookie-refresh (no browser)")
    return 0


def cmd_auth_doctor(_args: argparse.Namespace) -> int:
    try:
        existing = auth_mod.load_auth()
        have_auth = True
    except FileNotFoundError:
        existing = {}
        have_auth = False

    s: dict[str, Any] = {
        "have_auth": have_auth,
        "auth_file": str(auth_mod.AUTH_FILE),
        "cookies_present": sorted((existing.get("cookies") or {}).keys()) if have_auth else [],
        "age_days": auth_mod.status()["age_days"] if have_auth else None,
        "managed_chrome": chrome_launcher.status(),
    }
    print(json.dumps(s, indent=2))

    print("\n--- recommended action ---", file=sys.stderr)
    if not have_auth:
        print(
            "no auth on disk. Run:\n"
            "  1) fb-cli auth chrome login   # one-time, log in with 'Save login info'\n"
            "  2) fb-cli auth import-browser",
            file=sys.stderr,
        )
        return 1
    cookies = existing.get("cookies") or {}
    if not all(cookies.get(c) for c in ("c_user", "xs", "datr")):
        print(
            "missing long-lived cookies. Run:\n"
            "  fb-cli auth chrome login && fb-cli auth import-browser",
            file=sys.stderr,
        )
        return 1
    # Try a cheap refresh as a liveness check.
    try:
        cookie_refresh.refresh(existing, timeout=15)
    except cookie_refresh.CookieRefreshError as e:
        print(
            f"cookies appear dead ({e}). Run:\n"
            "  fb-cli auth chrome login && fb-cli auth import-browser",
            file=sys.stderr,
        )
        return 1
    print("auth OK — cookies are valid and tokens just refreshed.", file=sys.stderr)
    return 0


def cmd_auth_chrome_start(args: argparse.Namespace) -> int:
    try:
        s = chrome_launcher.start(
            port=args.port,
            copy_profile=args.copy_profile,
            headless=args.headless,
        )
    except chrome_launcher.ChromeLauncherError as e:
        print(f"chrome start failed: {e}", file=sys.stderr)
        return 6
    print(json.dumps(s, indent=2))
    return 0


def cmd_auth_chrome_stop(_args: argparse.Namespace) -> int:
    killed = chrome_launcher.stop()
    print("stopped" if killed else "not running")
    return 0


def cmd_auth_chrome_status(_args: argparse.Namespace) -> int:
    print(json.dumps(chrome_launcher.status(), indent=2))
    return 0


def cmd_auth_chrome_login(args: argparse.Namespace) -> int:
    """Boot the managed Chrome on the FB login page so the user can sign in."""
    try:
        s = chrome_launcher.start(
            port=args.port,
            copy_profile=False,
            landing_url="https://www.facebook.com/login/?next=https%3A%2F%2Fwww.facebook.com%2Fmarketplace%2F",
        )
    except chrome_launcher.ChromeLauncherError as e:
        print(f"chrome start failed: {e}", file=sys.stderr)
        return 6
    print(json.dumps(s, indent=2))
    print(
        "\nLog in with 'Save login info' checked, then run:\n"
        "  fb-cli auth import-browser\n"
        "  fb-cli auth status",
        file=sys.stderr,
    )
    return 0


def cmd_auth_status(_args: argparse.Namespace) -> int:
    s = auth_mod.status()
    print(json.dumps(s, indent=2))
    if s["age_days"] > 7:
        print(
            "\n⚠️  auth is >7 days old — if requests start failing, run `fb-cli auth refresh` first "
            "(cheap), then `fb-cli auth import-browser` if refresh fails.",
            file=sys.stderr,
        )
    return 0


def cmd_auth_show(_args: argparse.Namespace) -> int:
    print(json.dumps(auth_mod.load_auth(), indent=2))
    return 0


def _do_search(
    auth: dict[str, Any],
    query: str,
    lat: float,
    lng: float,
    *,
    radius: int,
    min_price: float | None,
    max_price: float | None,
    days: int | None,
    condition: list[str] | None,
    category: list[str] | None,
    sort: str | None,
    limit: int,
    pages: int,
    location_id: str | None,
) -> list[dict[str, Any]]:
    base_vars = queries.build_search_variables(
        query=query,
        latitude=lat,
        longitude=lng,
        radius_km=radius,
        min_price=min_price,
        max_price=max_price,
        category_ids=category,
        condition=condition,
        days_since_listed=days,
        count=min(limit, 24),
        location_id=location_id,
        sort=sort,
    )
    referer = f"https://www.facebook.com/marketplace/learn/search/?query={query}"

    resp = client.graphql(
        auth,
        "CometMarketplaceSearchContentContainerQuery",
        base_vars,
        referer=referer,
    )
    parsed = parse_mod.parse_search(resp)
    listings = parsed["listings"]
    cursor = parsed["cursor"]

    fetched_pages = 1
    while (
        len(listings) < limit
        and parsed["has_next_page"]
        and cursor
        and fetched_pages < pages
    ):
        page_vars = queries.build_pagination_variables(base_vars, cursor=cursor, count=24)
        resp = client.graphql(
            auth,
            "CometMarketplaceSearchContentPaginationQuery",
            page_vars,
            referer=referer,
        )
        parsed = parse_mod.parse_search(resp)
        listings.extend(parsed["listings"])
        cursor = parsed["cursor"]
        fetched_pages += 1
        # be polite
        time.sleep(0.5)

    return listings[:limit]


def cmd_search(args: argparse.Namespace) -> int:
    auth = auth_mod.load_auth()
    lat, lng, location_id = _resolve_loc(auth, args.lat, args.lng)
    listings = _do_search(
        auth,
        args.query,
        lat,
        lng,
        radius=args.radius,
        min_price=args.min_price,
        max_price=args.max_price,
        days=args.days,
        condition=args.condition,
        category=args.category,
        sort=args.sort,
        limit=args.limit,
        pages=args.pages,
        location_id=location_id,
    )
    from fb_cli.format import format_search

    print(format_search(listings, fmt=args.format))
    return 0


def cmd_listing(args: argparse.Namespace) -> int:
    auth = auth_mod.load_auth()
    referer = f"https://www.facebook.com/marketplace/item/{args.id}/"
    resp = client.graphql(
        auth,
        "MarketplacePDPContainerQuery",
        queries.build_listing_variables(args.id),
        referer=referer,
    )
    media_resp = None
    try:
        media_resp = client.graphql(
            auth,
            "MarketplacePDPC2CMediaViewerWithImagesQuery",
            {"targetId": str(args.id)},
            referer=referer,
        )
    except client.FBError:
        # photos optional — swallow so single bad listing doesn't break the call
        pass
    parsed = parse_mod.parse_listing(resp, media_resp=media_resp)
    if args.format == "json":
        print(json.dumps(parsed, indent=2, ensure_ascii=False))
    elif args.format == "jsonl":
        print(json.dumps(parsed, ensure_ascii=False))
    else:
        for k, v in parsed.items():
            print(f"{k}: {v}")
    return 0


def cmd_suggest(args: argparse.Namespace) -> int:
    auth = auth_mod.load_auth()
    resp = client.graphql(
        auth,
        "MarketplaceSuggestionDataSourceQuery",
        queries.build_suggest_variables(args.query, args.count),
    )
    suggestions = parse_mod.parse_suggestions(resp)
    print(json.dumps(suggestions, indent=2, ensure_ascii=False))
    return 0


def cmd_watch_add(args: argparse.Namespace) -> int:
    filters = {
        "lat": args.lat,
        "lng": args.lng,
        "radius": args.radius,
        "min_price": args.min_price,
        "max_price": args.max_price,
        "days": args.days,
        "condition": args.condition,
        "category": args.category,
        "sort": args.sort,
    }
    p = watch.add(args.name, args.query, **filters)
    print(f"saved watch -> {p}")
    return 0


def cmd_watch_list(_args: argparse.Namespace) -> int:
    watches = watch.list_all()
    if not watches:
        print("(no watches)")
        return 0
    for w in watches:
        last = w.get("last_checked_at")
        last_s = "never" if not last else _ago(last)
        print(
            f"{w['name']:20s}  q={w['query']!r}  seen={len(w.get('seen_ids', []))}  last={last_s}"
        )
    return 0


def cmd_watch_show(args: argparse.Namespace) -> int:
    w = watch.get(args.name)
    print(json.dumps(w, indent=2, ensure_ascii=False))
    return 0


def cmd_watch_check(args: argparse.Namespace) -> int:
    auth = auth_mod.load_auth()
    targets = [watch.get(args.name)] if args.name else watch.list_all()
    if not targets:
        print("(no watches)")
        return 0
    any_new = False
    from fb_cli.format import format_search

    for w in targets:
        f = w.get("filters", {})
        lat, lng, location_id = _resolve_loc(auth, f.get("lat"), f.get("lng"))
        listings = _do_search(
            auth,
            w["query"],
            lat,
            lng,
            radius=f.get("radius") or 65,
            min_price=f.get("min_price"),
            max_price=f.get("max_price"),
            days=f.get("days"),
            condition=f.get("condition"),
            category=f.get("category"),
            sort=f.get("sort") or "CREATION_TIME_DESCEND",
            limit=48,
            pages=2,
            location_id=location_id,
        )
        new = watch.diff_new(w["name"], listings)
        if not new:
            if not args.silent_noop:
                print(f"# {w['name']}: 0 new")
            continue
        any_new = True
        print(f"# {w['name']}: {len(new)} new")
        print(format_search(new, fmt=args.format))
        watch.mark_seen(w["name"], [str(L["id"]) for L in new])

    return 0 if (any_new or not args.silent_noop) else 0


def cmd_watch_rm(args: argparse.Namespace) -> int:
    if watch.remove(args.name):
        print(f"removed {args.name}")
        return 0
    print(f"watch {args.name} not found", file=sys.stderr)
    return 1


def _ago(ts: int) -> str:
    delta = max(0, int(time.time()) - int(ts))
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


# --- dispatch --------------------------------------------------------------


HANDLERS = {
    ("auth", "import-har"): cmd_auth_import,
    ("auth", "import-browser"): cmd_auth_import_browser,
    ("auth", "refresh"): cmd_auth_refresh,
    ("auth", "status"): cmd_auth_status,
    ("auth", "show"): cmd_auth_show,
    ("auth", "doctor"): cmd_auth_doctor,
    ("auth", "chrome"): None,  # dispatched via chrome_cmd below
    ("search", None): cmd_search,
    ("listing", None): cmd_listing,
    ("suggest", None): cmd_suggest,
    ("watch", "add"): cmd_watch_add,
    ("watch", "list"): cmd_watch_list,
    ("watch", "show"): cmd_watch_show,
    ("watch", "check"): cmd_watch_check,
    ("watch", "rm"): cmd_watch_rm,
}

CHROME_HANDLERS = {
    "start": cmd_auth_chrome_start,
    "stop": cmd_auth_chrome_stop,
    "status": cmd_auth_chrome_status,
    "login": cmd_auth_chrome_login,
}


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    sub = getattr(args, "auth_cmd", None) or getattr(args, "watch_cmd", None)
    if args.cmd == "auth" and sub == "chrome":
        chrome_cmd = getattr(args, "chrome_cmd", None)
        handler = CHROME_HANDLERS.get(chrome_cmd or "")
        if handler is None:
            print(f"unknown auth chrome subcommand: {chrome_cmd}", file=sys.stderr)
            return 2
        try:
            return handler(args)
        except chrome_launcher.ChromeLauncherError as e:
            print(f"chrome error: {e}", file=sys.stderr)
            return 6
    handler = HANDLERS.get((args.cmd, sub)) or HANDLERS.get((args.cmd, None))
    if handler is None:
        print(f"unknown command: {args.cmd} {sub}", file=sys.stderr)
        return 2
    try:
        return handler(args)
    except client.AuthExpiredError as e:
        print(f"auth expired: {e}\nrun: fb-cli auth import-har <fresh.har>", file=sys.stderr)
        return 3
    except client.FBError as e:
        print(f"fb error: {e}", file=sys.stderr)
        return 4
    except browser_auth.BrowserAuthError as e:
        print(f"browser auth error: {e}", file=sys.stderr)
        return 6
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 5
