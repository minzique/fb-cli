"""Known GraphQL doc_ids and variable builders.

Captured from a HAR recorded 2026-04-23. Facebook rotates these on deploys —
if a query starts returning 200 with empty body, recapture and update.
"""
from __future__ import annotations

from typing import Any

# doc_id catalog. Format: friendly_name -> doc_id (string).
DOC_IDS: dict[str, str] = {
    "CometMarketplaceSearchRootQuery": "32811453205106563",
    "CometMarketplaceSearchContentContainerQuery": "26504043479217830",
    "CometMarketplaceSearchContentPaginationQuery": "26361335123517718",
    "MarketplaceCometBrowseFeedLightContainerQuery": "26378026188555471",
    "MarketplacePDPContainerQuery": "35404930299120454",
    "MarketplacePDPC2CMediaViewerWithImagesQuery": "10059604367394414",
    "MarketplaceSuggestionDataSourceQuery": "9807803949296946",
    "MarketplaceRecentDataSourceQuery": "9681594908555299",
    "CometMarketplaceLeftRailNavigationContainerQuery": "24640314145552071",
}

# Reasonable cap that FB accepts for filter_price_upper_bound (signed int max-ish in cents).
PRICE_UPPER_DEFAULT = 214748364700


def build_search_variables(
    query: str,
    latitude: float,
    longitude: float,
    *,
    radius_km: int = 65,
    min_price: float | None = None,
    max_price: float | None = None,
    category_ids: list[str] | None = None,
    condition: list[str] | None = None,
    days_since_listed: int | None = None,
    count: int = 24,
    cursor: str | None = None,
    location_id: str | None = None,
    sort: str | None = None,
) -> dict[str, Any]:
    """Build variables for SearchContentContainer / Pagination.

    `condition`: list of NEW, USED_LIKE_NEW, USED_GOOD, USED_FAIR.
    `sort`: not yet wired (FB uses a separate filter encoding we haven't reverse-
    engineered cleanly). Pass-through is a no-op for now and a TODO.
    """
    contextual_filters: list[dict[str, Any]] = []
    # sort is intentionally not pushed into contextual_filters until the exact
    # encoding is captured from a HAR with sorted results — FB rejects a wrong
    # shape with `noncoercible_variable_value`. Tracked in docs/API.md.

    browse_request_params: dict[str, Any] = {
        "commerce_enable_local_pickup": True,
        "commerce_enable_shipping": True,
        "commerce_search_and_rp_available": True,
        "commerce_search_and_rp_category_id": category_ids or [],
        "commerce_search_and_rp_condition": condition,
        "commerce_search_and_rp_ctime_days": days_since_listed,
        "filter_location_latitude": latitude,
        "filter_location_longitude": longitude,
        "filter_price_lower_bound": int((min_price or 0) * 100),
        "filter_price_upper_bound": (
            int(max_price * 100) if max_price else PRICE_UPPER_DEFAULT
        ),
        "filter_radius_km": radius_km,
    }

    params: dict[str, Any] = {
        "bqf": {"callsite": "COMMERCE_MKTPLACE_WWW", "query": query},
        "browse_request_params": browse_request_params,
        "custom_request_params": {
            "browse_context": None,
            "contextual_filters": contextual_filters,
            "referral_code": None,
            "referral_ui_component": None,
            "saved_search_strid": None,
            "search_vertical": "C2C",
            "seo_url": None,
            "serp_landing_settings": {"virtual_category_id": ""},
            "surface": "SEARCH",
            "virtual_contextual_filters": [],
        },
    }

    pop_params = {"location_id": location_id or "", "query": query}
    topic_params = {"location_id": location_id or "", "url": None}

    return {
        "buyLocation": {"latitude": latitude, "longitude": longitude},
        "contextual_data": None,
        "count": count,
        "cursor": cursor,
        "params": params,
        "savedSearchID": None,
        "savedSearchQuery": query,
        "scale": 2,
        "searchPopularSearchesParams": pop_params,
        "shouldIncludePopularSearches": False,
        "topicPageParams": topic_params,
    }


def build_pagination_variables(
    base_search_vars: dict[str, Any], cursor: str, count: int = 24
) -> dict[str, Any]:
    return {
        "count": count,
        "cursor": cursor,
        "params": base_search_vars["params"],
        "scale": 2,
    }


def build_listing_variables(listing_id: str) -> dict[str, Any]:
    """Variables for MarketplacePDPContainerQuery."""
    return {
        "enableJobEmployerActionBar": False,
        "enableJobSeekerActionBar": False,
        "feedbackSource": 56,
        "feedLocation": "MARKETPLACE_MEGAMALL",
        "referralCode": "null",
        "referralSurfaceString": "search",
        "scale": 2,
        "targetId": str(listing_id),
        "useDefaultActor": False,
        # relay providers — FB ignores stale ones but rejects missing ones for
        # this query, so we mirror the captured set verbatim.
        "__relay_internal__pv__ShouldUpdateMarketplaceBoostListingBoostedStatusrelayprovider": False,
        "__relay_internal__pv__CometUFISingleLineUFIrelayprovider": True,
        "__relay_internal__pv__CometUFIShareActionMigrationrelayprovider": True,
        "__relay_internal__pv__CometUFIReactionsEnableShortNamerelayprovider": False,
        "__relay_internal__pv__CometUFICommentAutoTranslationTyperelayprovider": "ORIGINAL",
        "__relay_internal__pv__CometUFICommentAvatarStickerAnimatedImagerelayprovider": False,
        "__relay_internal__pv__CometUFICommentActionLinksRewriteEnabledrelayprovider": False,
        "__relay_internal__pv__IsWorkUserrelayprovider": False,
        "__relay_internal__pv__GHLShouldChangeSponsoredDataFieldNamerelayprovider": True,
        "__relay_internal__pv__GHLShouldChangeAdIdFieldNamerelayprovider": True,
        "__relay_internal__pv__CometUFI_dedicated_comment_routable_dialog_gkrelayprovider": True,
    }


def build_suggest_variables(query: str, count: int = 10) -> dict[str, Any]:
    return {"query": query, "count": count}
