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

# Saved watches with new-listing diff (for "tell me when something new shows up")
fb-cli watch add piano --query "digital piano" --max 100000 --radius 100
fb-cli watch list
fb-cli watch check piano        # shows only NEW listings since last check
fb-cli watch check               # all watches
fb-cli watch rm piano

# Auth check (re-import if anything starts failing with "Login required" or HTTP 500)
fb-cli auth status
```

## Output

- **Search/watch** default to a pretty table. Add `--format jsonl` for
  programmatic parsing.
- **Listing** defaults to JSON.

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

## Auth & limits

- Auth file: `~/.fb-cli/auth.json` (mode 600, contains real session cookies)
- `fb_dtsg` rotates every few days. If you get `Login required` or HTTP 500
  consistently, ask the user for a fresh HAR (Firefox/Chrome devtools →
  Network → Save All as HAR → `fb-cli auth import-har <file>`)
- This is the user's real Facebook account. **Don't message sellers**, don't
  attempt to create listings, don't do anything write-y. Read-only.
- Rate limit yourself: pause a few seconds between bulk PDP fetches, don't
  iterate over hundreds of listings in one session.

## Internals

- Source: `~/Developer/fb-cli/`
- Reverse-engineered API doc: `~/Developer/fb-cli/docs/API.md`
- State: `~/.fb-cli/auth.json`, `~/.fb-cli/watches/<name>.json`
- Stdlib-only Python; no install needed beyond the symlinked `fb-cli` binary.
