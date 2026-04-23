"""Flatten Facebook's nested GraphQL response into clean records."""
from __future__ import annotations

import json
from typing import Any


def parse_search(resp: dict[str, Any]) -> dict[str, Any]:
    """Return {'listings': [...], 'cursor': str|None, 'has_next_page': bool}."""
    feed = resp["data"]["marketplace_search"]["feed_units"]
    edges = feed.get("edges", [])
    page = feed.get("page_info", {}) or {}
    listings: list[dict[str, Any]] = []
    last_cursor: str | None = None

    for edge in edges:
        node = edge.get("node") or {}
        if node.get("__typename") != "MarketplaceFeedListingStoryObject":
            # Skip ads, story interjections, "no more results", etc.
            continue
        L = node.get("listing") or {}
        listing_id = L.get("id")
        if not listing_id:
            continue
        cursor = edge.get("cursor")
        if cursor:
            last_cursor = cursor
        listings.append(_flatten_listing(L, story_key=node.get("story_key")))

    # Cursor sometimes lives on page_info, sometimes on last edge — fall back.
    cursor = page.get("end_cursor") or last_cursor
    return {
        "listings": listings,
        "cursor": cursor,
        "has_next_page": bool(page.get("has_next_page", False)),
    }


def _flatten_listing(L: dict[str, Any], *, story_key: str | None = None) -> dict[str, Any]:
    price = L.get("listing_price") or {}
    seller = L.get("marketplace_listing_seller") or {}
    loc = (L.get("location") or {}).get("reverse_geocode") or {}
    photo_uri = (
        ((L.get("primary_listing_photo") or {}).get("image") or {}).get("uri")
    )
    creation = (L.get("if_gk_just_listed_tag_on_search_feed") or {}).get(
        "creation_time"
    )

    return {
        "id": L.get("id"),
        "story_key": story_key,
        "title": L.get("marketplace_listing_title"),
        "price": _to_float(price.get("amount")),
        "price_formatted": price.get("formatted_amount"),
        "currency_offset_amount": price.get("amount_with_offset_in_currency"),
        "city": loc.get("city"),
        "state": loc.get("state"),
        "city_id": ((loc.get("city_page") or {}).get("id")),
        "city_display": ((loc.get("city_page") or {}).get("display_name")),
        "seller_id": seller.get("id"),
        "seller_name": seller.get("name"),
        "category_id": L.get("marketplace_listing_category_id"),
        "is_sold": L.get("is_sold"),
        "is_pending": L.get("is_pending"),
        "is_live": L.get("is_live"),
        "is_hidden": L.get("is_hidden"),
        "delivery_types": L.get("delivery_types"),
        "creation_time": creation,
        "primary_photo": photo_uri,
        "url": f"https://www.facebook.com/marketplace/item/{L.get('id')}/" if L.get("id") else None,
    }


def parse_listing(resp: dict[str, Any], media_resp: dict[str, Any] | None = None) -> dict[str, Any]:
    """Flatten MarketplacePDPContainerQuery + optional media viewer response.

    Response shape:
      data.viewer.marketplace_product_details_page.{marketplace_listing_renderable_target, target}

    `marketplace_listing_renderable_target` carries the canonical title, formatted
    price, condition, and reverse-geocoded location. `target` carries everything
    else (description, creation_time, delivery_types, seller, etc.). Photos live
    in a separate query (MarketplacePDPC2CMediaViewerWithImagesQuery).
    """
    pdp = (
        ((resp.get("data") or {}).get("viewer") or {})
        .get("marketplace_product_details_page")
    ) or {}
    rt = pdp.get("marketplace_listing_renderable_target") or {}
    t = pdp.get("target") or {}
    # Fallback for older response shapes
    if not (rt or t):
        t = pdp
        rt = pdp

    seller = (t.get("marketplace_listing_seller") or {})
    if not seller:
        actors = ((t.get("story") or {}).get("actors")) or []
        if actors:
            seller = {"id": actors[0].get("id"), "name": actors[0].get("name")}

    rt_price = rt.get("formatted_price") or {}
    t_price = t.get("listing_price") or rt.get("listing_price") or {}
    loc = (rt.get("location") or {}).get("reverse_geocode") or {}
    coords = rt.get("location") or {}
    location_text = (t.get("location_text") or {}).get("text")

    listing_id = rt.get("id") or t.get("id")

    photos: list[str] = []
    if media_resp:
        media_t = (
            ((media_resp.get("data") or {}).get("viewer") or {})
            .get("marketplace_product_details_page", {})
            .get("target")
        ) or {}
        for p in media_t.get("listing_photos") or []:
            img = (p or {}).get("image") or {}
            if img.get("uri"):
                photos.append(img["uri"])

    return {
        "id": listing_id,
        "title": rt.get("marketplace_listing_title") or rt.get("base_marketplace_listing_title"),
        "description": _description(t),
        "price": _to_float(t_price.get("amount")),
        "price_formatted": rt_price.get("text"),
        "currency": t_price.get("currency"),
        "condition": rt.get("condition"),
        "city": loc.get("city"),
        "state": loc.get("state"),
        "location_text": location_text,
        "latitude": coords.get("latitude"),
        "longitude": coords.get("longitude"),
        "seller_id": seller.get("id"),
        "seller_name": seller.get("name"),
        "seller_join_date": seller.get("join_date"),
        "creation_time": t.get("creation_time"),
        "is_sold": t.get("is_sold"),
        "is_pending": t.get("is_pending"),
        "is_live": t.get("is_live"),
        "is_hidden": t.get("is_hidden"),
        "delivery_types": t.get("delivery_types"),
        "category_id": t.get("marketplace_listing_category_id"),
        "messaging_enabled": t.get("messaging_enabled") or t.get("messagingEnabled"),
        "photos": photos,
        "share_uri": t.get("share_uri"),
        "url": f"https://www.facebook.com/marketplace/item/{listing_id}/" if listing_id else None,
    }


def parse_suggestions(resp: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = (
        resp.get("data", {})
        .get("marketplace_search_suggestions", {})
        .get("edges", [])
    )
    out = []
    for n in nodes:
        node = n.get("node") or {}
        out.append(
            {
                "text": node.get("text"),
                "type": node.get("__typename"),
                "category_id": node.get("category_id"),
            }
        )
    return out


def _description(listing: dict[str, Any]) -> str | None:
    desc = listing.get("redacted_description") or listing.get("marketplace_listing_description")
    if isinstance(desc, dict):
        return desc.get("text") or desc.get("plaintext_for_search")
    return desc


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
