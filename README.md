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
| `browser` fallback (open/search/extract/scroll/screenshot via managed Chrome) | ✅ |
| GraphQL `sort` (price, recency, distance) | ⚠️ wire format not yet captured; use `browser search` as fallback |
| Messaging / write ops | ❌ not implemented (and probably won't be) |

## Install

Recommended for normal users: install it as an isolated CLI app.

```bash
# Once the package is on PyPI:
pipx install fb-marketplace-cli
# or, if you use uv:
uv tool install fb-marketplace-cli
```

That installs the command as `fb-cli`.

Until the PyPI publish is complete, install directly from GitHub:

```bash
uv tool install git+https://github.com/minzique/fb-cli
# or, with pipx:
pipx install git+https://github.com/minzique/fb-cli
```

Requires Python ≥3.10. `pipx`/`uv tool` are preferred over plain `pip install`
because they create a dedicated virtualenv for the CLI and put `fb-cli` on your
PATH without touching your system Python.

Developer install:

```bash
git clone https://github.com/minzique/fb-cli ~/Developer/fb-cli
cd ~/Developer/fb-cli
uv tool install --editable .
```

## Auth (one-time, then automatic for ~1 year)

Facebook has no API keys for Marketplace. You authenticate as yourself once,
then fb-cli keeps the session fresh on its own.

### How FB auth actually works

FB stores two layers of state. The reverse-engineered model:

- **Long-lived signed cookies** (`xs`, `c_user`, `datr`, `fr`, `sb`).
  Lifetime ~1 year when you check **Save login info** at login. This *is*
  Facebook's "remember me" — they just set `xs` with `Max-Age=31536000`. The
  signature is server-side so it can't be forged, but it can be carried
  indefinitely.
- **Page-bound CSRF tokens** (`fb_dtsg`, `lsd`, `jazoest`). Embedded in the
  HTML of every facebook.com page, rotated every few hours, but trivially
  re-derivable from any successful page load using the cookies above.

fb-cli implements both: `auth import-browser` captures the long-lived
cookies, and `auth refresh` (also called automatically inside `client.graphql`
on token errors) fetches `/marketplace/` HTML and scrapes new CSRF tokens out
of it. As long as `xs` is alive, you never need to touch a HAR or a browser.

### One-time setup (recommended)

```bash
fb-cli auth chrome login        # opens FB login in fb-cli's managed Chrome
# → sign in with "Save login info" CHECKED, navigate to /marketplace/
fb-cli auth import-browser      # captures cookies + tokens
fb-cli auth status              # confirm
```

The managed Chrome lives at `~/.fb-cli/chrome-profile/`, separate from your
normal browsing.

### Day-to-day

Nothing. Token-staleness errors are caught and refreshed transparently.

### When something breaks

```bash
fb-cli auth doctor              # diagnose + recommended fix
fb-cli auth refresh             # cheap: 1 HTTPS GET, fixes ~95% of issues
fb-cli auth import-browser      # auto-launches managed Chrome if needed
```

Only when `xs` finally expires (~1 year) do you need to re-run
`fb-cli auth chrome login` and sign in again.

### Alternative: HAR import

Still supported if you'd rather not run the managed Chrome:

```bash
# DevTools → Network → Save All as HAR while browsing facebook.com/marketplace/
fb-cli auth import-har ~/Downloads/www.facebook.com_*.har
```

Auth lands in `~/.fb-cli/auth.json` (mode 600). Treat it like a password.

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

### Browser-backed fallback

Use this when the Facebook web UI can do something the captured GraphQL surface
cannot yet model, or when agents need to inspect what the browser actually
shows.

```bash
fb-cli browser search "235/60R18" --limit 20 --format jsonl
fb-cli browser scroll --steps 2
fb-cli browser extract --limit 40 --format jsonl
fb-cli browser screenshot ~/.fb-cli/marketplace.png
fb-cli browser open "https://www.facebook.com/marketplace/"
```

The browser commands reuse fb-cli's managed Chrome profile at
`~/.fb-cli/chrome-profile/`. They are intended for read/inspect workflows:
search pages, visible listing extraction, scrolling, screenshots, and gated
JavaScript diagnostics (`browser eval ... --unsafe`). They do **not** send seller
messages, create listings, buy items, or perform irreversible Marketplace
actions.

Prefer the normal GraphQL `search`/`listing` commands when they work because
they return richer structured data (`creation_time`, seller IDs, coordinates,
etc.). Fall back to `browser` when GraphQL doc IDs or filter encodings lag the
live website.

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
- **Browser extraction is visible-page best effort.** `fb-cli browser extract`
  reads cards rendered in the current web UI. It is less complete than GraphQL
  details and can change when Facebook redesigns Marketplace.
- **Auth/doc_id rotation.** FB rotates tokens every few days and doc_ids on
  deploys. First try `fb-cli auth import-browser`. If a query still returns
  empty or 500, re-export a HAR and run `python -m fb_cli.tools.diff_doc_ids
  <new.har>` (TODO) to refresh `fb_cli/queries.py`.
- **Read-only.** No messaging, no listing creation, no purchase. By design.
- **Single-account.** No tenant isolation.

## License

MIT — see [LICENSE](LICENSE).
