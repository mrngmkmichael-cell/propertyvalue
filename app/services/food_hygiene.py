"""Food hygiene ratings near a property, from the Food Standards
Agency's free Food Hygiene Rating Scheme API - no key required.

Note: FSA's API takes maxDistanceLimit in MILES and returns Distance
in miles too, unlike every other distance-based service in this
project (which work in metres and convert for display) - converted
to metres here on the way in/out so the rest of the app doesn't need
to know about this one API's quirk.
"""
import httpx

from app.services import _cache

API_BASE = "https://api.ratings.food.gov.uk/Establishments"
SEARCH_RADIUS_MILES = 0.5
CACHE_TTL_S = 86400 * 3  # ratings are reassessed periodically, not real-time
RESULT_LIMIT = 15

MILES_TO_METRES = 1609.344


async def nearby_ratings(lat: float, lon: float) -> list[dict]:
    key = _cache.coord_key("food_hygiene", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_nearby(lat, lon)
    _cache.set(key, result)
    return result


async def _fetch_nearby(lat: float, lon: float) -> list[dict]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "maxDistanceLimit": SEARCH_RADIUS_MILES,
        "sortOptionKey": "distance",
        "pageSize": RESULT_LIMIT,
    }
    headers = {"x-api-version": "2", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(API_BASE, params=params, headers=headers)
        response.raise_for_status()
    except httpx.HTTPError:
        return []

    establishments = response.json().get("establishments", [])
    results = []
    for e in establishments:
        rating = (e.get("RatingValue") or "").strip()
        if not rating or not rating.isdigit():
            continue  # skip "Exempt"/"AwaitingInspection"/"AwaitingPublication" - not a usable score
        try:
            distance_m = round(float(e.get("Distance", 0)) * MILES_TO_METRES)
        except (TypeError, ValueError):
            distance_m = None
        results.append({
            "name": e.get("BusinessName", "Unnamed"),
            "type": e.get("BusinessType", ""),
            "rating": int(rating),
            "rating_date": (e.get("RatingDate") or "")[:10],
            "distance_m": distance_m,
        })
    return results
