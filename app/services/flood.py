"""Active flood warnings/alerts from the Environment Agency's
real-time flood-monitoring API (no key required).

Note: this is near-real-time warning data, not a long-term flood
risk score for the area - there isn't a free JSON API for that
long-term assessment, so we're clear in the UI about what this is.

Why this fetches the whole country rather than querying per postcode:
profiling the property page showed this single call as its long pole,
7.75s on a cold cache against ~2s for everything else. The EA endpoint
is slow regardless of filter - the per-location spatial query took
2-8s to return an empty list. The national list costs the same one
request and, outside a flood event, is about 1 KiB. So it is fetched
once, held for ten minutes, filtered by distance in memory, and
refreshed in the background when stale. A visitor never waits on the
EA, and the EA sees one request per ten minutes instead of one per
property lookup.
"""
import asyncio
import logging
import math
import time

import httpx

from app.services import _cache

API_BASE = "https://environment.data.gov.uk/flood-monitoring"
SEARCH_RADIUS_KM = 10
CACHE_TTL_S = 600  # active warnings can genuinely change, so kept short

# https://environment.data.gov.uk/flood-monitoring/doc/reference
SEVERITY_LABELS = {
    1: "Severe flood warning",
    2: "Flood warning",
    3: "Flood alert",
    4: "Warning no longer in force",
}

_NATIONAL_KEY = ("flood_warnings_national",)
_AREA_TTL_S = 86400  # flood area centroids are static geography
_refresh_task: asyncio.Task | None = None


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def _area_coords(client: httpx.AsyncClient, area_id: str):
    """Centroid of a flood area, for the rare item that doesn't embed
    its own lat/long. Cached for a day; geography doesn't move."""
    key = ("flood_area", area_id)
    cached = _cache.get(key, _AREA_TTL_S)
    if cached is not None:
        return cached
    try:
        resp = await client.get(f"{API_BASE}/id/floodAreas/{area_id}")
        resp.raise_for_status()
        item = resp.json().get("items", {})
        coords = (item.get("lat"), item.get("long"))
    except (httpx.HTTPError, ValueError):
        coords = (None, None)
    _cache.set(key, coords)
    return coords


async def _fetch_national() -> list[dict]:
    """Every active warning in England, with a resolvable location."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(f"{API_BASE}/id/floods")
        response.raise_for_status()
        items = response.json().get("items", [])

        warnings = []
        for item in items:
            area = item.get("floodArea") or {}
            lat, lon = area.get("lat"), area.get("long")
            if lat is None or lon is None:
                area_id = item.get("floodAreaID") or area.get("notation")
                if area_id:
                    lat, lon = await _area_coords(client, area_id)
            if lat is None or lon is None:
                # Can't place it, so can't honestly attach it to a
                # property. Logged rather than guessed.
                logging.info("flood warning without coordinates skipped: %s",
                             item.get("description", "")[:60])
                continue
            level = item.get("severityLevel")
            warnings.append({
                "lat": float(lat),
                "lon": float(lon),
                "description": item.get("description", ""),
                "severity": SEVERITY_LABELS.get(level, item.get("severity", "")),
                "severity_level": level,
                "date": (item.get("timeRaised") or "")[:10],
            })
    return warnings


async def _refresh_national() -> list[dict]:
    global _refresh_task
    try:
        data = await _fetch_national()
        _cache.set(_NATIONAL_KEY, data)
        return data
    except (httpx.HTTPError, ValueError) as exc:
        logging.warning("flood warnings refresh failed: %s", exc)
        raise
    finally:
        _refresh_task = None


async def _national_warnings() -> list[dict] | None:
    """Stale-while-revalidate: serve whatever is cached immediately and,
    if it has expired, refresh once in the background for the next
    caller. Only the very first request after a cold start waits."""
    global _refresh_task
    fresh = _cache.get(_NATIONAL_KEY, CACHE_TTL_S)
    if fresh is not None:
        return fresh

    stale_entry = _cache._store.get(_NATIONAL_KEY)
    if stale_entry is not None:
        if _refresh_task is None:
            _refresh_task = asyncio.create_task(_refresh_national())
        return stale_entry[1]

    # Cold start: nothing to serve yet, so this one caller waits.
    if _refresh_task is None:
        _refresh_task = asyncio.create_task(_refresh_national())
    try:
        return await _refresh_task
    except (httpx.HTTPError, ValueError):
        return None


async def warnings_near(lat: float, lon: float) -> list[dict]:
    key = _cache.coord_key("flood", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached

    national = await _national_warnings()
    if national is None:
        # EA unreachable on a cold start. Empty is the safe fallback -
        # the card says "no active warnings" and the long-term flood
        # zone (a separate service) still renders.
        return []

    nearby = [
        {k: v for k, v in w.items() if k not in ("lat", "lon")}
        for w in national
        if _haversine_km(lat, lon, w["lat"], w["lon"]) <= SEARCH_RADIUS_KM
    ]
    # Most severe (lowest level number) first.
    nearby.sort(key=lambda w: (w["severity_level"] is None, w["severity_level"]))
    _cache.set(key, nearby)
    return nearby
