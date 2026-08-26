"""Crime stats from the Police.uk API (no key required).
Summarized by category for the latest available month, within
roughly a 1-mile radius of the given coordinates (fixed by the API).
"""
import asyncio
from collections import Counter

import httpx

from app.services import _cache, postcodes

API_BASE = "https://data.police.uk/api"
CACHE_TTL_S = 86400  # Police.uk data only updates monthly


async def summary_for_outcode(outcode: str) -> dict | None:
    """Same crime summary, but centred on the postcode district (e.g.
    'BR6') rather than the exact address - used as a wider-area
    comparison. Not a true local-authority crime rate (no free,
    population-normalized dataset comparable to a point-radius query
    exists) - this is the same ~1 mile radius sample, just centred
    more broadly."""
    centroid = await postcodes.outcode_centroid(outcode)
    if centroid is None:
        return None
    return await summary_near(centroid["latitude"], centroid["longitude"])


async def summary_near(lat: float, lon: float) -> dict:
    key = _cache.coord_key("crime", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_summary(lat, lon)
    _cache.set(key, result)
    return result


# How many months to walk back when the latest month has no records.
# Greater Manchester Police published nothing for May or June 2026 while
# other forces were current, so an empty "latest month" is very often a
# publication gap, not a crime-free square mile. Reported to us by a
# reader whose street showed zero (27 Aug 2026).
_WALKBACK_MONTHS = 6


def _previous_months(latest: str, n: int) -> list[str]:
    """['2026-05', '2026-04', ...] going back n months from latest
    ('2026-06-01' or '2026-06')."""
    year, month = int(latest[:4]), int(latest[5:7])
    out = []
    for _ in range(n):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        out.append(f"{year:04d}-{month:02d}")
    return out


async def _street(client: httpx.AsyncClient, lat: float, lon: float, date: str | None) -> list | None:
    params = {"lat": lat, "lng": lon}
    if date:
        params["date"] = date
    response = await client.get(f"{API_BASE}/crimes-street/all-crime", params=params)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    return response.json()


async def _fetch_summary(lat: float, lon: float) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        records = await _street(client, lat, lon, None)

        if not records:
            # Empty latest month: find the newest month this force
            # actually published, rather than dressing the gap up as
            # zero crime.
            try:
                lu = await client.get(f"{API_BASE}/crime-last-updated")
                latest = (lu.json().get("date") or "")[:7] if lu.status_code == 200 else ""
            except (httpx.HTTPError, ValueError):
                latest = ""
            if latest:
                for month in _previous_months(latest, _WALKBACK_MONTHS):
                    # Spaced out: the API 429s a burst of back-to-back
                    # month queries, and a 429 must read as "try the
                    # next month", never as "no crime".
                    await asyncio.sleep(0.35)
                    try:
                        records = await _street(client, lat, lon, month)
                    except httpx.HTTPError:
                        continue
                    if records:
                        break

    if not records:
        # Nothing in more than half a year: almost certainly a force
        # that isn't publishing here. total None (never 0) so every
        # surface says "no data" instead of claiming a crime-free area.
        return {"total": None, "month": None, "by_category": [], "unpublished": True}

    counts = Counter(rec.get("category", "unknown") for rec in records)
    by_category = [
        {"category": cat.replace("-", " "), "count": n}
        for cat, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]

    return {
        "total": len(records),
        "month": records[0]["month"] if records else None,
        "by_category": by_category,
    }
