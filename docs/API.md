# Facebook Marketplace internal GraphQL — reverse-engineered

> Snapshot from a HAR captured 2026-04-23 against `www.facebook.com` (Firefox
> 151, logged-in account). Facebook rotates `doc_id`s on most deploys — refresh
> from a fresh HAR when fields stop populating.

## Endpoint

`POST https://www.facebook.com/api/graphql/`

`Content-Type: application/x-www-form-urlencoded`. Response is JSON (or NDJSON
for some streamed paginated queries — first object is the canonical payload).

## Auth model

- **Cookies** carry user identity: `c_user`, `xs`, `datr`, `fr`, `sb`,
  `presence`, `wd`, `dpr`. `c_user` and `xs` are the critical ones.
- **`fb_dtsg`** (form field) is the CSRF token. Rotates ~weekly. Required.
- **`lsd`** (form field + `X-FB-LSD` header) is the per-session token. Required.
- **`jazoest`** is a numeric checksum of `fb_dtsg`. Required.
- **`__user`** / **`av`** are the actor user id (matches `c_user`).

## Required form fields

The minimum set FB will accept:

| Field | Source | Notes |
|---|---|---|
| `av` | actor user id | same as `c_user` |
| `__user` | actor user id | same as `c_user` |
| `__a` | `1` | constant |
| `__req` | `1` | request sequence in session — any digit ok |
| `__rev` | `1037953373` (HAR-pinned) | client revision; FB tolerates a stale value |
| `__hs` | `20566.HYP:comet_pkg.2.1...0` | client revision package; tolerates stale |
| `__hsi` | `7631844899988393266` (HAR-pinned) | per-session id |
| `__ccg` | `GOOD` | network quality hint |
| `__comet_req` | `15` | comet bundle id; **required for PDP** |
| `__crn` | route name (see below) | comet route hint; **required for PDP** |
| `__spin_b` | `trunk` | constant |
| `__spin_r` | matches `__rev` | |
| `__spin_t` | unix timestamp | refreshed per request |
| `dpr` | `1` | device pixel ratio |
| `lsd` | from HAR | |
| `jazoest` | from HAR | |
| `fb_dtsg` | from HAR | |
| `fb_api_caller_class` | `RelayModern` | constant |
| `fb_api_req_friendly_name` | query name | mirrored in `X-FB-Friendly-Name` header |
| `doc_id` | per-query (see catalog) | |
| `variables` | JSON-encoded | shape per query |
| `server_timestamps` | `true` | constant |

Headers that matter: `X-FB-Friendly-Name`, `X-FB-LSD`, `X-ASBD-ID: 359341`,
`Origin: https://www.facebook.com`, `Referer` matching the surface
(`/marketplace/...`), `Sec-Fetch-Site: same-origin`, `User-Agent` matching
HAR (mismatched UA + cookie can trigger checkpoints).

## doc_id catalog

| Friendly name | doc_id (2026-04-23) | Purpose |
|---|---|---|
| `MarketplaceCometBrowseFeedLightContainerQuery` | 26378026188555471 | Marketplace home feed |
| `CometMarketplaceSearchRootQuery` | 32811453205106563 | Search bootstrap |
| `CometMarketplaceSearchContentContainerQuery` | 26504043479217830 | First page of search results |
| `CometMarketplaceSearchContentPaginationQuery` | 26361335123517718 | Subsequent pages (cursor-based) |
| `MarketplacePDPContainerQuery` | 35404930299120454 | Listing detail page |
| `MarketplacePDPC2CMediaViewerWithImagesQuery` | 10059604367394414 | Listing photos (separate from PDP) |
| `MarketplaceSuggestionDataSourceQuery` | 9807803949296946 | Search autocomplete |
| `MarketplaceRecentDataSourceQuery` | 9681594908555299 | User's recent searches |
| `CometMarketplaceLeftRailNavigationContainerQuery` | 24640314145552071 | Category navigation |

## `__crn` route hints

The PDP query rejects requests with HTTP 500 if `__crn` is missing. Working
values observed:

| Query | `__crn` |
|---|---|
| Marketplace home / category nav | `comet.fbweb.CometMarketplaceMultiCategoryRoute` |
| Search / pagination / suggest / **PDP** | `comet.fbweb.CometMarketplaceSearchRoute` |

PDP works with `CometMarketplaceSearchRoute` even when reached directly —
likely because the `referer` header (`/marketplace/item/<id>/`) carries the
route signal and `__crn` is just expected to look plausible.

## Variable shapes

### Search (`CometMarketplaceSearchContentContainerQuery`)

```json
{
  "buyLocation": {"latitude": 6.84083, "longitude": 80.0139},
  "contextual_data": null,
  "count": 24,
  "cursor": null,
  "params": {
    "bqf": {"callsite": "COMMERCE_MKTPLACE_WWW", "query": "piano"},
    "browse_request_params": {
      "commerce_enable_local_pickup": true,
      "commerce_enable_shipping": true,
      "commerce_search_and_rp_available": true,
      "commerce_search_and_rp_category_id": [],
      "commerce_search_and_rp_condition": null,
      "commerce_search_and_rp_ctime_days": null,
      "filter_location_latitude": 6.84083,
      "filter_location_longitude": 80.0139,
      "filter_price_lower_bound": 0,
      "filter_price_upper_bound": 214748364700,
      "filter_radius_km": 65
    },
    "custom_request_params": {
      "browse_context": null,
      "contextual_filters": [],
      "referral_code": null,
      "referral_ui_component": null,
      "saved_search_strid": null,
      "search_vertical": "C2C",
      "seo_url": null,
      "serp_landing_settings": {"virtual_category_id": ""},
      "surface": "SEARCH",
      "virtual_contextual_filters": []
    }
  },
  "savedSearchID": null,
  "savedSearchQuery": "piano",
  "scale": 2,
  "searchPopularSearchesParams": {"location_id": "112661478746814", "query": "piano"},
  "shouldIncludePopularSearches": false,
  "topicPageParams": {"location_id": "112661478746814", "url": null}
}
```

**Filters:**
- `filter_price_lower_bound` / `filter_price_upper_bound` — **integer cents**.
  `100.00` USD = `10000`. Default upper = `214748364700` (≈ INT_MAX cents).
- `filter_radius_km` — integer km from `(filter_location_latitude, filter_location_longitude)`.
- `commerce_search_and_rp_condition` — array of `NEW`, `USED_LIKE_NEW`, `USED_GOOD`, `USED_FAIR`, or `null` for all.
- `commerce_search_and_rp_ctime_days` — listed within last N days. `1`, `7`, `30`, or `null` for all time.
- `commerce_search_and_rp_category_id` — array of category id strings.

**Sort** is **not** in `browse_request_params`. It's expected to live in
`custom_request_params.contextual_filters` but the exact shape is not
yet captured. (TODO: capture a HAR with a sort applied via the FB UI.)

### Pagination (`CometMarketplaceSearchContentPaginationQuery`)

```json
{
  "count": 24,
  "cursor": "<opaque cursor from previous response>",
  "params": { /* same as search */ },
  "scale": 2
}
```

The `cursor` comes from `data.marketplace_search.feed_units.page_info.end_cursor`
or, on some responses, the last edge's `cursor`. We try `page_info` first and
fall back to the last edge.

### PDP (`MarketplacePDPContainerQuery`)

```json
{
  "targetId": "741366038967680",
  "feedLocation": "MARKETPLACE_MEGAMALL",
  "feedbackSource": 56,
  "referralCode": "null",
  "referralSurfaceString": "search",
  "scale": 2,
  "useDefaultActor": false,
  "enableJobEmployerActionBar": false,
  "enableJobSeekerActionBar": false,
  "__relay_internal__pv__*": "..."
}
```

The `__relay_internal__pv__*` fields are Relay provider toggles. FB rejects
the query with `noncoercible_variable_value` if any are missing. Mirror the
captured set verbatim — they don't change response content materially.

### PDP photos (`MarketplacePDPC2CMediaViewerWithImagesQuery`)

```json
{"targetId": "741366038967680"}
```

Returns `data.viewer.marketplace_product_details_page.target.listing_photos[]`,
each with `image.uri` (full resolution).

## Response shapes (the parts we read)

### Search

```
data.marketplace_search.feed_units.edges[].node {
  __typename: "MarketplaceFeedListingStoryObject" | "MarketplaceFeedAdStory" | ...
  story_key
  listing {
    id
    marketplace_listing_title
    listing_price { amount, formatted_amount, amount_with_offset_in_currency }
    location.reverse_geocode { city, state, city_page { display_name, id } }
    primary_listing_photo.image.uri
    if_gk_just_listed_tag_on_search_feed.creation_time   (epoch seconds)
    marketplace_listing_seller { id, name }
    marketplace_listing_category_id
    delivery_types[]
    is_sold, is_pending, is_live, is_hidden
  }
}
data.marketplace_search.feed_units.page_info { end_cursor, has_next_page }
```

Skip nodes whose `__typename != "MarketplaceFeedListingStoryObject"` — those
are ads / interjections / "no more results" cards.

### PDP

```
data.viewer.marketplace_product_details_page {
  marketplace_listing_renderable_target {
    id, marketplace_listing_title, base_marketplace_listing_title,
    formatted_price.text, listing_price.amount, condition,
    location { latitude, longitude, reverse_geocode { city, state, city_page {...} } }
  }
  target {
    id, redacted_description.text, creation_time, location_text.text,
    delivery_types[], marketplace_listing_seller { id, name },
    is_sold, is_pending, is_live, is_hidden, share_uri,
    marketplace_listing_category_id, messaging_enabled, messagingEnabled,
    story.url, story.actors[]
  }
}
```

`condition` values seen: `PC_NEW`, `PC_USED_LIKE_NEW`, `PC_USED_GOOD`,
`PC_USED_FAIR`.

## Error modes

| Symptom | Likely cause | Fix |
|---|---|---|
| HTTP 200, body `{}` or `{"data":null,"errors":[...]}` | bad `doc_id` (rotated) | recapture HAR, refresh `queries.DOC_IDS` |
| `noncoercible_variable_value` | wrong type / missing relay provider | diff your `variables` against a captured request |
| HTTP 500 `<html>Error</html>` | missing `__crn` or `__comet_req`, or stale `fb_dtsg` | first try the route hints in this doc; if that doesn't help, re-import HAR |
| `Login required` in `errors[]` | `c_user`/`xs` invalid or expired | re-login + recapture HAR |
| HTTP 200, body `{}` 249 bytes | missing required form fields (`__a`, `__req`, etc.) | mirror the full required field list |

## Recapturing doc_ids

When something stops working:

1. Open Firefox → log in to facebook.com → DevTools → Network tab.
2. Reproduce the action (search, open PDP, etc.).
3. Save All as HAR.
4. Re-import: `fb-cli auth import-har <new.har>` (refreshes `fb_dtsg` + `lsd`).
5. Update `fb_cli/queries.py:DOC_IDS` with new ids from the HAR.

A small `tools/diff_doc_ids.py` that auto-extracts and writes the diff is
TODO.
