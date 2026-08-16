"""Crime stats from the Police.uk API (no key required).
Summarized by category for the latest available month, within
roughly a 1-mile radius of the given coordinates (fixed by the API).
"""
from collections import Counter

import httpx

from app.services import _cache

API_BASE = "https://data.police.uk/api"
CACHE_TTL_S = 86400  # Police.uk data only updates monthly


async def summary_near(lat: float, lon: float) -> dict:
    key = _cache.coord_key("crime", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_summary(lat, lon)
    _cache.set(key, result)
    return result


async def _fetch_summary(lat: float, lon: float) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{API_BASE}/crimes-street/all-crime",
            params={"lat": lat, "lng": lon},
        )
    if response.status_code == 404:
        return {"total": 0, "month": None, "by_category": []}
    response.raise_for_status()
    records = response.json()

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
