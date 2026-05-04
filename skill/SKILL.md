---
name: fb-marketplace
description: Search and monitor Facebook Marketplace listings using Minzi's account. Use when the user wants to find used items for sale (electronics, instruments, furniture, vehicles, etc.), compare prices on the local marketplace, or set up a recurring watch for new listings matching specific criteria.
allowed-tools: Bash, Read, Write
---

# Facebook Marketplace via fb-cli

You can search and monitor Facebook Marketplace as Minzi using the `fb-cli`
binary. Auth is already imported (cookies + `fb_dtsg` from a HAR file).

## When to use this

- "find me a used X on marketplace"
- "what does Y go for in Sri Lanka right now"
- "watch for new piano listings"
- "show me listings under LKR50,000 for Z"
- price research before buying anything secondhand

## Quick reference

```bash
# Search (default radius is the user's home area from imported auth)
fb-cli search "digital piano" --max 100000 --limit 20

# Filters: --min, --max (price), --radius (km), --days (1/7/30),
# --condition NEW|USED_LIKE_NEW|USED_GOOD|USED_FAIR (repeat),
# --category <id>, --pages 3, --format json|jsonl|table

# Listing detail (full description, photos, condition, seller, coords)
fb-cli listing 741366038967680

# Browser fallback when GraphQL misses live UI capabilities / sort / layout
fb-cli browser search "235/60R18" --limit 20 --format jsonl
fb-cli browser scroll --steps 2
fb-cli browser extract --limit 40 --format jsonl
fb-cli browser screenshot ~/.fb-cli/marketplace.png

# Saved watches with new-listing diff (for "tell me when something new shows up")
fb-cli watch add piano --query "digital piano" --max 100000 --radius 100
fb-cli watch list
fb-cli watch check piano        # shows only NEW listings since last check
fb-cli watch check               # all watches
fb-cli watch rm piano

# Auth check / refresh — see the AUTH RECOVERY tree below for failure handling
fb-cli auth status
fb-cli auth doctor          # diagnose + recommended action
fb-cli auth refresh         # cheap: pulls fresh fb_dtsg/lsd via 1 HTTPS GET
fb-cli auth import-browser  # fuller: auto-launches managed Chrome if needed
```

## Output

- **Search/watch/browser search/browser extract** default to a pretty table.
  Add `--format jsonl` for programmatic parsing.
- **Listing** defaults to JSON.
- **Browser screenshot** writes a PNG and prints its path.

JSONL records carry: `id`, `title`, `price`, `price_formatted` (e.g.
"LKR28,500"), `city`, `creation_time` (epoch), `seller_name`, `delivery_types`,
`primary_photo`, `url`. Listing adds `description`, `condition`, `latitude`,
`longitude`, `photos[]`.

Currency is whatever Facebook returns based on the buy_location in
`~/.fb-cli/auth.json` — currently LKR (Sri Lanka). The auth's `buy_location`
sets the default search center.

## How to recommend an item

Don't just dump 30 results. Triage:

1. **First, run the search wide** (`--limit 30 --pages 2`) and dump JSONL.
2. **Filter in your head**: drop items with `LKR0` price (placeholder),
   "weevil" / "damaged" / "broken" in title or description, things obviously
   not what the user asked for.
3. **Cross-check** the top 3-5 candidates with `fb-cli listing <id>` to
   see condition, full description, photos, and how long they've been listed.
4. **Present 3-5 finalists** with: price, title, location, age, why it's
   good, link. Skip the ones that don't make the cut.

For the user's piano hunt specifically: weighted 88-key digital pianos
(Yamaha P-45/P-71/P-85/P-95/P-115, Casio Privia PX-150/160/170, Roland FP-10,
Kawai ES110) >> 61-key unweighted toy keyboards >> free acoustic uprights
(usually a money pit). See user's earlier conversation if relevant.

## GraphQL first, browser fallback second

Use normal `fb-cli search` and `fb-cli listing` first. They return richer
structured fields such as `creation_time`, seller IDs, and coordinates.

Use `fb-cli browser ...` when agents are otherwise handicapped by the captured
GraphQL surface — e.g. sort/filter encodings changed, the web UI shows results
GraphQL missed, or you need a screenshot/visible-page inspection. Browser
records are visible-card best effort and include `source: "browser"`.

## Auth & limits

- Auth file: `~/.fb-cli/auth.json` (mode 600, contains real session cookies)
- Managed Chrome profile: `~/.fb-cli/chrome-profile/`
- This is the user's real Facebook account. **Don't message sellers**, don't
  attempt to create listings, don't do anything write-y. Read-only.
- `fb-cli browser eval ... --unsafe` is diagnostics-only. Do not use it to click
  buttons, type into seller chats, submit forms, buy items, or create listings.
- Rate limit yourself: pause a few seconds between bulk PDP fetches, don't
  iterate over hundreds of listings in one session.

### How fb-cli auth actually works (so you can recover intelligently)

FB's session has two layers:

1. **Long-lived signed cookies** — `xs` (the actual session secret), `c_user`,
   `datr`, `fr`, `sb`. Lifetime ~1 year when the user checked "Save login
   info" on login. This IS Facebook's "remember me".
2. **Page-bound CSRF tokens** — `fb_dtsg` and `lsd`, embedded in the HTML of
   every page. Rotate every few hours. Can be re-derived from any successful
   page load using the cookies above.

fb-cli already auto-retries token refreshes inside `client.graphql`. Most
token-staleness errors heal themselves on the next call. If you see an error
bubble up, follow the recovery tree below — **do not abandon the tool on the
first failure**.

### AUTH RECOVERY tree (run in order, stop at first success)

```
ERROR: "Login required" / FBerror 1357004 / HTTP 500 / cookie refresh failed
  │
  └─ 1. fb-cli auth doctor              # tells you exactly which step is needed
  │
  └─ 2. fb-cli auth refresh             # cheap: HTTPS GET, no browser. Fixes ~95% of issues.
  │     └─ retry your search/listing
  │
  └─ 3. fb-cli auth import-browser      # auto-launches managed Chrome on :9222
  │     └─ retry
  │
  └─ 4. ASK THE USER — cookies are dead, they need to log in once:
        "Run `fb-cli auth chrome login`, sign in to Facebook with
        'Save login info' checked, then I'll retry. (One-time, lasts ~1 year.)"
```

Never silently move on to a different tool when fb-cli reports an auth error.
The recovery is cheap (steps 1-3 are non-interactive). Only step 4 needs the
user, and it's a true one-time-per-year action.

### Managed Chrome (for `import-browser`)

fb-cli owns a dedicated debug Chrome at `~/.fb-cli/chrome-profile/` separate
from the user's normal Chrome.

```bash
fb-cli auth chrome status
fb-cli auth chrome login         # one-time: opens FB login in the managed Chrome
fb-cli auth chrome start         # boots the managed Chrome on :9222
fb-cli auth chrome stop
```

## Internals

- Source: `~/Developer/fb-cli/`
- Reverse-engineered API doc: `~/Developer/fb-cli/docs/API.md`
- State: `~/.fb-cli/auth.json`, `~/.fb-cli/watches/<name>.json`
- Stdlib-only Python; no install needed beyond the symlinked `fb-cli` binary.
