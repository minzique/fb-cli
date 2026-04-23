# fb-cli

Unofficial Facebook Marketplace CLI. Speaks Facebook's internal GraphQL
protocol directly using session cookies imported from a HAR file.

Stdlib-only Python. No browser at runtime. No third-party deps.

## Why

Facebook has no public Marketplace API. Their web client makes everything as
`POST /api/graphql/` with rotating `doc_id`s and a long list of routing
fields. This wraps that traffic in a clean CLI you can script against.

## Status

| Surface | Works |
|---|---|
| `search` (query + price + radius + condition + days + category + paginate) | ✅ |
| `listing` (full PDP + photos + seller + condition + coordinates) | ✅ |
| `suggest` (autocomplete) | ✅ |
| `watch` (saved searches with new-listing diff) | ✅ |
| `sort` (price, recency, distance) | ⚠️ wire format not yet captured |
| Messaging / write ops | ❌ not implemented (and probably won't be) |

## Install

```bash
git clone https://github.com/<you>/fb-cli ~/Developer/fb-cli
ln -s ~/Developer/fb-cli/bin/fb-cli ~/.local/bin/fb-cli
```

That's it. Requires Python ≥3.10.

## Auth (one-time)

Facebook has no API keys for Marketplace. You authenticate as yourself by
exporting a HAR file from a logged-in browser session, then importing it.

1. Open Firefox or Chrome → log in to facebook.com → open Marketplace and
   browse one search and one listing (so the HAR captures the right cookies
   + doc_ids).
2. Open DevTools → Network tab → right-click any request → **Save All as
   HAR**.
3. Import:

```bash
fb-cli auth import-har ~/Downloads/www.facebook.com_*.har
fb-cli auth status
```

Auth lands in `~/.fb-cli/auth.json` (mode 600). It contains your `c_user`,
`xs`, `fb_dtsg`, `lsd`, etc. Treat it like a password.

The `fb_dtsg` token rotates every few days. If requests start returning
"Login required" or `noncoercible_variable_value`, re-export the HAR.

## Usage

### Search

```bash
fb-cli search "digital piano" --max 80000 --radius 50 --limit 20
fb-cli search keyboard --min 10000 --max 50000 --days 7 --condition USED_GOOD
fb-cli search "yamaha p45" --format jsonl > listings.jsonl
fb-cli search piano --pages 3 --limit 60          # paginate
```

Defaults to a table view. Use `--format jsonl` for agent consumption,
`--format json` for human inspection of one page.

### Listing detail

```bash
fb-cli listing 741366038967680
```

Returns full description, photos, condition, location, seller, delivery
options, etc.

### Watches (saved searches with new-listing diff)

```bash
fb-cli watch add piano --query "digital piano" --max 100000 --radius 100
fb-cli watch add yamaha-88 --query "yamaha keyboard 88" --max 80000

fb-cli watch list
fb-cli watch check                  # checks all, shows only NEW listings since last check
fb-cli watch check piano            # one watch
fb-cli watch check --silent-noop    # nothing if no new — good for cron

fb-cli watch rm yamaha-88
```

State lives in `~/.fb-cli/watches/<name>.json`. The `seen_ids` array is what
gives you "show only new since last check".

### Cron / launchd

```bash
*/30 * * * * /Users/you/.local/bin/fb-cli watch check --silent-noop --format jsonl >> ~/.fb-cli/inbox.jsonl
```

## Output (search/watch)

JSONL records carry:

```json
{"id":"741366038967680","title":"Piano Lester (Used )","price":28500.0,
 "price_formatted":"LKR28,500","city":"Athurugiriya","creation_time":1776583985,
 "seller_id":"100000952026992","seller_name":"Aruna Marasinghe",
 "delivery_types":["IN_PERSON"],"primary_photo":"https://...",
 "url":"https://www.facebook.com/marketplace/item/741366038967680/"}
```

Listing detail adds: `description`, `condition` (PC_NEW / PC_USED_*),
`latitude`, `longitude`, `photos[]` (full-size), `messaging_enabled`.

## How it works

See [`docs/API.md`](docs/API.md) for a complete reverse-engineering writeup
of the FB Marketplace GraphQL surface, the required form fields, the
known `doc_id` registry, and how to recapture rotated IDs.

## Limitations

- **Cookie auth means you act as you.** Aggressive scraping = account flag.
  The CLI does no automatic rate limiting; be reasonable.
- **`doc_id` rotation.** FB rotates these on most deploys. When something
  starts returning empty or 500, re-export a HAR and run
  `python -m fb_cli.tools.diff_doc_ids <new.har>` (TODO) to refresh
  `fb_cli/queries.py`.
- **Read-only.** No messaging, no listing creation, no purchase. By design.
- **Single-account.** No tenant isolation.

## License

MIT — see [LICENSE](LICENSE).
