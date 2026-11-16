"""Client for OLX.pl public (unauthenticated) JSON endpoints used by the website."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

from .browser import BrowserSession

BASE = "/api/v1"

SORT_OPTIONS = {
    "newest": "created_at:desc",
    "price_asc": "filter_float_price:asc",
    "price_desc": "filter_float_price:desc",
    "relevance": None,
}

MIN_INTERVAL = 1.0  # seconds between requests: be polite to OLX


class OlxError(RuntimeError):
    pass


class OlxApi:
    def __init__(self, browser: BrowserSession) -> None:
        self._browser = browser
        self._lock = asyncio.Lock()
        self._urls: dict[int, str] = {}
        self._photos: dict[int, list[str]] = {}
        self._last = 0.0

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        async with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                try:
                    status, text = await self._browser.fetch_json(
                        BASE + path, [(k, str(v)) for k, v in (params or {}).items()]
                    )
                except Exception as e:
                    raise OlxError(f"Browser error: {str(e).splitlines()[0]}") from e
            finally:
                self._last = time.monotonic()
        try:
            data = json.loads(text)
        except ValueError:
            raise OlxError(f"OLX returned non-JSON (HTTP {status}); the site may be blocking or down")
        if status >= 400 or (isinstance(data, dict) and data.get("error")):
            raise OlxError(f"OLX error (HTTP {status}): {json.dumps(data.get('error', data))[:300]}")
        return data

    # ---------- locations ----------

    async def find_location(self, text: str) -> list[dict]:
        data = await self._get("/geo-encoder/location-autocomplete/", {"query": text})
        out = []
        for item in data.get("data", []):
            city = item.get("city") or {}
            region = item.get("region") or {}
            district = item.get("district") or {}
            out.append(
                {
                    "city_id": city.get("id"),
                    "city": city.get("name"),
                    "district_id": district.get("id"),
                    "district": district.get("name"),
                    "region_id": region.get("id"),
                    "region": region.get("name"),
                    "county": (item.get("county") or {}).get("name"),
                }
            )
        return out

    # ---------- categories ----------

    async def suggest_categories(self, query: str, limit: int = 15) -> list[dict]:
        facets = json.dumps(
            [{"field": "category_without_exclusions", "fetchLabel": True, "fetchUrl": True, "limit": limit}]
        )
        data = await self._get("/offers/metadata/search/", {"query": query, "facets": facets})
        cats = (data.get("data", {}).get("facets") or {}).get("category_without_exclusions", [])
        return [
            {
                "category_id": c["id"],
                "name": c.get("label"),
                "path": (c.get("url") or "").split("/q-")[0].strip("/"),
                "offers": c.get("count"),
            }
            for c in cats
        ]

    async def resolve_url_path(self, path: str) -> dict:
        """Turn an olx.pl URL path like 'elektronika/telefony/krakow' into query params."""
        path = path.split("olx.pl", 1)[-1].split("?", 1)[0].strip("/")
        parts = [p for p in path.split("/") if p and not p.startswith("q-") and p != "d"]
        data = await self._get(f"/friendly-links/query-params/{','.join(parts)}/")
        return data.get("data", {})

    async def category_filters(self, category_id: int) -> dict[str, Any]:
        """Filters available for a category (e.g. condition, brand, size)."""
        data = await self._get("/offers/metadata/filters/", {"category_id": category_id})
        out: dict[str, Any] = {}
        for key, variants in (data.get("data") or {}).items():
            for v in variants:
                cats = [c for o in v.get("options", []) for c in o.get("categories", [])]
                if cats and category_id not in cats:
                    continue
                out[key] = {
                    "label": v.get("label"),
                    "type": v.get("type"),
                    "values": [
                        {"value": x.get("value"), "label": x.get("label")}
                        for x in (v.get("values") or [])
                    ][:60],
                }
                break
        return out

    # ---------- offers ----------

    async def search(
        self,
        query: str | None = None,
        category_id: int | None = None,
        city_id: int | None = None,
        region_id: int | None = None,
        district_id: int | None = None,
        distance_km: int | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        sort: str = "relevance",
        offset: int = 0,
        limit: int = 20,
        extra_filters: dict[str, Any] | None = None,
    ) -> dict:
        params: dict[str, Any] = {"offset": offset, "limit": max(1, min(limit, 50))}
        if query:
            params["query"] = query
        for k, v in {
            "category_id": category_id,
            "city_id": city_id,
            "region_id": region_id,
            "district_id": district_id,
            "distance": distance_km,
            "filter_float_price:from": price_min,
            "filter_float_price:to": price_max,
        }.items():
            if v is not None:
                params[k] = v
        if sort not in SORT_OPTIONS:
            raise OlxError(f"sort must be one of {list(SORT_OPTIONS)}")
        if SORT_OPTIONS[sort]:
            params["sort_by"] = SORT_OPTIONS[sort]
        for k, v in (extra_filters or {}).items():
            if isinstance(v, list):
                for i, item in enumerate(v):
                    params[f"{k}[{i}]"] = item
            else:
                params[k] = v
        data = await self._get("/offers/", params)
        meta = data.get("metadata", {})
        raw = data.get("data", [])
        offers = [summarize_offer(o) for o in raw]
        for o in raw:
            if o.get("id") and o.get("url"):
                self._urls[o["id"]] = o["url"]
                self._photos[o["id"]] = [ph["link"] for ph in o.get("photos", []) if ph.get("link")]
        return {
            "total": meta.get("visible_total_count", meta.get("total_elements")),
            "offset": offset,
            "returned": len(offers),
            "offers": offers,
        }

    async def get_offer(self, offer: int | str) -> dict:
        """Open the offer page (like a normal visitor) and read the data embedded in it."""
        s = str(offer).strip()
        if s.isdigit():
            url = self._urls.get(int(s))
            if not url:
                raise OlxError("Unknown offer id: pass the offer URL, or search first so the id is known.")
        elif "olx.pl/" in s:
            url = s if s.startswith("http") else "https://" + s.lstrip("/")
        else:
            raise OlxError("Pass an olx.pl offer URL or an id from search results.")
        async with self._lock:
            wait = MIN_INTERVAL - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                status, html = await self._browser.page_html(url)
            except Exception as e:
                raise OlxError(f"Browser error: {str(e).splitlines()[0]}") from e
            finally:
                self._last = time.monotonic()
        if status == 404 or status == 410:
            raise OlxError("Offer not found (removed or expired).")
        m = STATE_RE.search(html)
        if status >= 400 or not m:
            raise OlxError(f"Could not read offer page (HTTP {status}).")
        state = json.loads(m.group(1))
        if isinstance(state, str):
            state = json.loads(state)
        ad = (state.get("ad") or {}).get("ad")
        if not ad:
            raise OlxError("Offer page had no offer data (maybe removed).")
        return detail_from_page(ad)


    async def photo_urls(self, offer: int | str) -> tuple[dict, list[str]]:
        """(short info about the offer, photo urls). Uses search cache when possible."""
        s = str(offer).strip()
        if s.isdigit() and int(s) in self._photos:
            return {"id": int(s), "url": self._urls.get(int(s))}, self._photos[int(s)]
        d = await self.get_offer(offer)
        return {k: d[k] for k in ("id", "title", "url", "price", "description", "info")}, d["photos"]


STATE_RE = re.compile(r'window\.__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*"|\{.*?\});\s*(?:window|</script>)', re.S)


# ---------- formatting helpers ----------


def _price(o: dict) -> dict | None:
    for p in o.get("params", []):
        if p.get("key") == "price":
            v = p.get("value") or {}
            return {
                "amount": v.get("value"),
                "currency": v.get("currency"),
                "label": v.get("label"),
                "negotiable": v.get("negotiable"),
            }
    return None


def _location(o: dict) -> str:
    loc = o.get("location") or {}
    parts = [
        (loc.get("city") or {}).get("name"),
        (loc.get("district") or {}).get("name"),
        (loc.get("region") or {}).get("name"),
    ]
    return ", ".join(p for p in parts if p)


GENERIC_PHRASES = (
    "sprzedam", "na sprzedaż", "stan dobry", "stan bardzo dobry", "polecam", "zapraszam",
    "więcej info", "wiecej info", "pytania na priv", "pytać", "w opisie", "jak na zdjęciach",
    "jak na zdjeciach", "widać na zdjęciach", "zdjęcia", "kontakt",
)


def _clean(text: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def assess_description(title: str | None, description: str | None, n_params: int, n_photos: int) -> dict:
    """Heuristic: is the text enough, or should someone look at the photos?"""
    desc = _clean(description)
    words = re.findall(r"\w+", desc.lower())
    title_words = set(re.findall(r"\w+", (title or "").lower()))
    informative = [w for w in words if len(w) > 2 and w not in title_words]
    generic = sum(1 for ph in GENERIC_PHRASES if ph in desc.lower())
    reasons, notes = [], []
    if not desc:
        reasons.append("no description")
    elif len(desc) < 80:
        reasons.append(f"very short description ({len(desc)} chars)")
    elif len(informative) < 12:
        reasons.append("description mostly repeats the title")
    if desc and generic >= 2 and len(desc) < 200:
        reasons.append("description is generic boilerplate")
    if re.search(r"zdj[eę]ci|\bfoto|na fotkach", desc.lower()):
        reasons.append("description points to the photos")
    if n_params <= 1:
        notes.append("few item parameters filled in")
    if reasons and n_photos == 0:
        notes.append("no photos either - ask the seller")
    return {
        "description_chars": len(desc),
        "photo_count": n_photos,
        "needs_photo_review": bool(reasons) and n_photos > 0,
        "reasons": reasons,
        "notes": notes,
    }


def summarize_offer(o: dict) -> dict:
    desc = _clean(o.get("description"))
    photos = [ph["link"] for ph in o.get("photos", []) if ph.get("link")]
    n_params = sum(1 for p in o.get("params", []) if p.get("key") != "price")
    return {
        "id": o.get("id"),
        "title": o.get("title"),
        "price": (_price(o) or {}).get("label"),
        "location": _location(o),
        "created": o.get("created_time"),
        "refreshed": o.get("last_refresh_time"),
        "business": o.get("business"),
        "delivery": bool(((o.get("delivery") or {}).get("rock") or {}).get("active")),
        "condition": next(
            (p["value"].get("label") for p in o.get("params", []) if p.get("key") == "state"), None
        ),
        "url": o.get("url"),
        "description_snippet": desc[:160] + ("…" if len(desc) > 160 else ""),
        "info": assess_description(o.get("title"), desc, n_params, len(photos)),
    }


def detail_from_page(a: dict) -> dict:
    """Shape the offer object embedded in an offer page."""
    price = a.get("price") or {}
    reg = price.get("regularPrice") or {}
    loc = a.get("location") or {}
    user = a.get("user") or {}
    contact = a.get("contact") or {}
    return {
        "id": a.get("id"),
        "title": a.get("title"),
        "url": a.get("url"),
        "active": a.get("isActive"),
        "price": price.get("displayValue"),
        "price_detail": {
            "amount": reg.get("value"),
            "currency": reg.get("currencyCode"),
            "negotiable": reg.get("negotiable"),
            "free": price.get("free"),
            "exchange": price.get("exchange"),
        },
        "condition": a.get("itemCondition"),
        "description": _clean(a.get("description")),
        "params": {p.get("name"): p.get("value") for p in a.get("params", [])},
        "category_id": (a.get("category") or {}).get("id"),
        "location": loc.get("pathName"),
        "location_ids": {k: loc.get(k) for k in ("cityId", "districtId", "regionId")},
        "map": {k: (a.get("map") or {}).get(k) for k in ("lat", "lon", "radius")},
        "photos": a.get("photos", []),
        "info": assess_description(
            a.get("title"), a.get("description"), len(a.get("params", [])), len(a.get("photos", []))
        ),
        "delivery": bool(((a.get("delivery") or {}).get("rock") or {}).get("active")),
        "business": a.get("isBusiness"),
        "created": a.get("createdTime"),
        "refreshed": a.get("lastRefreshTime"),
        "valid_to": a.get("validToTime"),
        "seller": {
            "id": user.get("id"),
            "name": user.get("name"),
            "on_olx_since": user.get("created"),
            "last_seen": user.get("lastSeen"),
            "company": user.get("company_name") or None,
            "chat": contact.get("chat"),
            "phone_shown": contact.get("phone"),
        },
    }
