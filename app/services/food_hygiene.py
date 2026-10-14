"""Food hygiene ratings near a property, from the Food Standards
Agency's free Food Hygiene Rating Scheme API - no key required.

Note: FSA's API takes maxDistanceLimit in MILES and returns Distance
in miles too, unlike every other distance-based service in this
project (which work in metres and convert for display) - converted
to metres here on the way in/out so the rest of the app doesn't need
to know about this one API's quirk.
"""
import asyncio

import httpx

from app.services import _cache

API_BASE = "https://api.ratings.food.gov.uk/Establishments"
SEARCH_RADIUS_MILES = 0.5
CACHE_TTL_S = 86400 * 3  # ratings are reassessed periodically, not real-time
RESULT_LIMIT = 15

MILES_TO_METRES = 1609.344
HEADERS = {"x-api-version": "2", "Accept": "application/json"}

# Restaurants, pubs and supermarkets near a point, for the report's
# "Nearby essentials" (14 Oct 2026). They came from OpenStreetMap until
# its Overpass service began refusing this site; the FSA's register
# lists every food business a council inspects, with its location, in
# all four nations. The count is the register's own total within the
# radius, not the length of the short list shown.
PLACE_TYPES = {"restaurant": 1, "supermarket": 7840, "pub": 7843}
PLACE_RADIUS_MILES = 0.62  # 1 km, the radius the OpenStreetMap search used
PLACE_LIST_LIMIT = 10
PLACES_SOURCE = "the Food Standards Agency's register of food businesses"


async def nearby_ratings(lat: float, lon: float) -> list[dict]:
    key = _cache.coord_key("food_hygiene", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_nearby(lat, lon)
    _cache.set(key, result)
    return result


def _tidy_business_name(name: str | None) -> str:
    """Some councils register names in capitals ("TESCO EXPRESS")."""
    name = (name or "").strip() or "Unnamed"
    return name.title() if name.isupper() and len(name) > 3 else name


async def places_near(lat: float, lon: float) -> dict[str, dict]:
    """{"restaurant"|"supermarket"|"pub": {"places": [...], "total": n}},
    nearest first. Left to raise, like _fetch_nearby, so an outage reads
    as "Data unavailable" and is not cached as an empty street."""
    key = _cache.coord_key("fsa_places", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(timeout=10) as client:
        found = await asyncio.gather(*(_places_of_type(client, lat, lon, type_id) for type_id in PLACE_TYPES.values()))
    result = dict(zip(PLACE_TYPES, found))
    _cache.set(key, result)
    return result


async def _places_of_type(client: httpx.AsyncClient, lat: float, lon: float, type_id: int) -> dict:
    params = {
        "latitude": lat, "longitude": lon, "maxDistanceLimit": PLACE_RADIUS_MILES,
        "businessTypeId": type_id, "sortOptionKey": "distance", "pageSize": PLACE_LIST_LIMIT,
    }
    response = await client.get(API_BASE, params=params, headers=HEADERS)
    response.raise_for_status()
    data = response.json()
    places = []
    for e in data.get("establishments", []):
        try:
            distance_m = round(float(e.get("Distance") or 0) * MILES_TO_METRES)
        except (TypeError, ValueError):
            continue
        geo = e.get("geocode") or {}
        try:
            p_lat, p_lon = float(geo.get("latitude")), float(geo.get("longitude"))
        except (TypeError, ValueError):
            p_lat = p_lon = None
        places.append({"name": _tidy_business_name(e.get("BusinessName")), "distance_m": distance_m,
                       "lat": p_lat, "lon": p_lon})
    total = (data.get("meta") or {}).get("totalCount")
    return {"places": places, "total": total if isinstance(total, int) else len(places)}


async def _fetch_nearby(lat: float, lon: float) -> list[dict]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "maxDistanceLimit": SEARCH_RADIUS_MILES,
        "sortOptionKey": "distance",
        "pageSize": RESULT_LIMIT,
    }
    headers = HEADERS
    # Left to raise - see radon.py's identical comment. Catching this
    # and returning [] made a live FSA outage indistinguishable from
    # "nothing rated nearby", and nearby_ratings below would cache
    # that wrong answer for 3 days.
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(API_BASE, params=params, headers=headers)
    response.raise_for_status()

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
