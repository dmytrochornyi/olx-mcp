"""MCP server exposing OLX.pl search tools (no account needed)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Literal

from mcp.server.mcpserver import Image, MCPServer

from . import photos as photo_dl
from .api import OlxApi, OlxError
from .browser import BrowserSession

logging.getLogger("httpx").setLevel(logging.WARNING)

browser = BrowserSession()
api = OlxApi(browser)


@asynccontextmanager
async def lifespan(_server):
    try:
        yield {}
    finally:
        await browser.close()


mcp = MCPServer(
    "olx",
    instructions=(
        "Search OLX.pl (Polish classifieds). No login needed. "
        "Typical flow: olx_find_location -> olx_suggest_categories -> olx_search -> olx_get_offer. "
        "Each offer has an `info` block; when info.needs_photo_review is true (missing, short or "
        "generic description), call olx_offer_photos and describe what the photos actually show "
        "(item, visible condition, damage, completeness, mismatches with the title). "
        "A real Chromium window opens for searching (OLX blocks headless browsers); "
        "photos are fetched directly from the image CDN."
    ),
    lifespan=lifespan,
)


def _err(e: Exception) -> dict:
    return {"error": str(e)}


@mcp.tool()
async def olx_find_location(text: str) -> list[dict] | dict:
    """Find OLX location ids for a city/town name (Polish names, e.g. 'Kraków', 'Wrocław').
    Returns city_id / region_id / district_id to use in olx_search."""
    try:
        return await api.find_location(text)
    except OlxError as e:
        return _err(e)


@mcp.tool()
async def olx_suggest_categories(query: str, limit: int = 15) -> list[dict] | dict:
    """Which OLX categories contain offers for this query, with offer counts.
    Use category_id in olx_search to narrow results (e.g. iPhone phones vs. iPhone cases)."""
    try:
        return await api.suggest_categories(query, limit)
    except OlxError as e:
        return _err(e)


@mcp.tool()
async def olx_category_filters(category_id: int) -> dict:
    """Category-specific filters (condition, brand, model, size, ...) and their allowed values.
    Pass them to olx_search as extra_filters, e.g. {"filter_enum_state": ["used"]}."""
    try:
        return await api.category_filters(category_id)
    except OlxError as e:
        return _err(e)


@mcp.tool()
async def olx_resolve_url(url: str) -> dict:
    """Convert an olx.pl listing URL/path (e.g. https://www.olx.pl/elektronika/telefony/krakow/)
    into search params (category_id, city_id, region_id...)."""
    try:
        return await api.resolve_url_path(url)
    except OlxError as e:
        return _err(e)


@mcp.tool()
async def olx_search(
    query: str | None = None,
    category_id: int | None = None,
    city_id: int | None = None,
    region_id: int | None = None,
    district_id: int | None = None,
    distance_km: int | None = None,
    price_min: float | None = None,
    price_max: float | None = None,
    sort: Literal["relevance", "newest", "price_asc", "price_desc"] = "relevance",
    offset: int = 0,
    limit: int = 20,
    extra_filters: dict[str, Any] | None = None,
) -> dict:
    """Search OLX.pl offers. Prices in PLN. distance_km works together with city_id.
    limit max 50; page with offset. extra_filters: category filters from olx_category_filters,
    e.g. {"filter_enum_state": ["used"]} or {"owner_type": "private"}.
    Each offer has info.needs_photo_review + reasons when its text is weak."""
    try:
        return await api.search(
            query=query,
            category_id=category_id,
            city_id=city_id,
            region_id=region_id,
            district_id=district_id,
            distance_km=distance_km,
            price_min=price_min,
            price_max=price_max,
            sort=sort,
            offset=offset,
            limit=limit,
            extra_filters=extra_filters,
        )
    except OlxError as e:
        return _err(e)


@mcp.tool()
async def olx_get_offer(offer: str) -> dict:
    """Full details of one offer: description, parameters, photo URLs, seller info, map position.
    `offer` is the offer URL, or an id returned by olx_search in this session."""
    try:
        return await api.get_offer(offer)
    except OlxError as e:
        return _err(e)


@mcp.tool(structured_output=False)
async def olx_offer_photos(offer: str, max_photos: int = 4, size: int = 640, start: int = 0) -> list:
    """Return the offer's photos as images so you can SEE the item. Use it when the description
    is missing/weak (info.needs_photo_review) or to verify condition, damage, completeness or
    model. `offer`: URL or id from olx_search. size: 300-1500 px (bigger = more detail, more tokens).
    max_photos 1-8; use start to page through the rest."""
    try:
        meta, urls = await api.photo_urls(offer)
    except OlxError as e:
        return [f"error: {e}"]
    max_photos = max(1, min(max_photos, 8))
    size = max(300, min(size, 1500))
    chosen = urls[start : start + max_photos]
    if not chosen:
        return [f"Offer {meta.get('url') or offer} has no photos (total {len(urls)}, start={start})."]
    results = await photo_dl.download(chosen, size)
    out: list = [
        f"Offer: {meta.get('url') or offer}\n"
        f"Photos {start + 1}-{start + len(chosen)} of {len(urls)}"
        + (f"\nTitle: {meta['title']}\nPrice: {meta.get('price')}" if meta.get("title") else "")
        + (f"\nDescription: {meta['description'][:500] or '(none)'}" if "description" in meta else "")
    ]
    for i, (url, data, fmt) in enumerate(results, start=start + 1):
        if data is None:
            out.append(f"Photo {i}: {fmt} ({url})")
        else:
            out.append(f"Photo {i}:")
            out.append(Image(data=data, format=fmt))
    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
