"""Google's own star ratings for nearby restaurants/cafes, via the
Places API (New) Nearby Search endpoint - shown alongside (not
instead of) the free FSA Food Hygiene Rating Scheme data, since the
two answer different questions: hygiene/safety inspection vs. public
opinion of the food/experience.

This is a separate, paid Google API from the Maps JavaScript API used
for the map view - billed per request against the same shared Google
Maps Platform free credit. Optional: skipped entirely if
GOOGLE_PLACES_API_KEY isn't set, same pattern as EPC/Google Maps. The
browser-facing GOOGLE_MAPS_API_KEY is usually locked to an HTTP
referrer restriction and won't work for this server-side call, so
this deliberately reads a separate env var.
"""
import math
import os

import httpx

from app.services import _cache

API_URL = "https://places.googleapis.com/v1/places:searchNearby"
SEARCH_RADIUS_M = 800
CACHE_TTL_S = 86400  # ratings don't move minute to minute; caching also cuts paid API calls
RESULT_LIMIT = 10

FOOD_PLACE_TYPES = ["restaurant", "cafe", "bakery", "meal_takeaway", "bar", "pub"]

FIELD_MASK = (
    "places.displayName,places.rating,places.userRatingCount,"
    "places.location,places.primaryTypeDisplayName"
)


def is_configured() -> bool:
    return bool(os.environ.get("GOOGLE_PLACES_API_KEY"))


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def nearby_food_ratings(lat: float, lon: float) -> list[dict]:
    if not is_configured():
        return []
    key = _cache.coord_key("google_places_food", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_nearby(lat, lon)
    _cache.set(key, result)
    return result


async def _fetch_nearby(lat: float, lon: float) -> list[dict]:
    body = {
        "includedTypes": FOOD_PLACE_TYPES,
        "maxResultCount": RESULT_LIMIT,
        "locationRestriction": {
            "circle": {"center": {"latitude": lat, "longitude": lon}, "radius": SEARCH_RADIUS_M}
        },
        "rankPreference": "DISTANCE",
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": os.environ["GOOGLE_PLACES_API_KEY"],
        "X-Goog-FieldMask": FIELD_MASK,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(API_URL, json=body, headers=headers)
        response.raise_for_status()
    except httpx.HTTPError:
        return []

    results = []
    for p in response.json().get("places", []):
        rating = p.get("rating")
        if rating is None:
            continue
        loc = p.get("location", {})
        p_lat, p_lon = loc.get("latitude"), loc.get("longitude")
        distance_m = round(_haversine_m(lat, lon, p_lat, p_lon)) if p_lat and p_lon else None
        results.append({
            "name": (p.get("displayName") or {}).get("text", "Unnamed"),
            "type": (p.get("primaryTypeDisplayName") or {}).get("text", ""),
            "rating": rating,
            "rating_count": p.get("userRatingCount", 0),
            "distance_m": distance_m,
        })
    results.sort(key=lambda r: r["distance_m"] if r["distance_m"] is not None else float("inf"))
    return results
