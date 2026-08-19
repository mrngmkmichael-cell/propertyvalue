"""CQC (Care Quality Commission) ratings for nearby GP practices,
dentists and care homes, from CQC's Syndication API - genuinely no key
required per their own docs ("No authentication is required"), only a
free-text partnerCode query parameter identifying the calling
organisation.

Same idea as food_hygiene.py (an official government quality rating,
shown alongside the matching OSM amenity category) - CQC is the
government inspector for these three categories the way the FSA is for
restaurants.

CONFIRMED NON-FUNCTIONAL IN PRODUCTION (checked 2026-08-19): api.cqc.org.uk
returns a clean 403 Forbidden to a well-formed request
(GET /locations?postcode=M1&perPage=50&partnerCode=propertyvalue) from
this project's live Render deployment, not just from the dev
environment - confirmed via a temporary debug log of the raw response.
Same 403 seen from three independent network paths (local dev, a
separate fetch path, and Render production), consistent with a
CDN/WAF block on datacenter IP ranges generally, not an auth problem
(CQC's own docs say none is needed) and not a bad query parameter.
Left in place rather than removed because it fails soft exactly like a
normal outage (empty results, nothing shown, no crash) - if CQC ever
relaxes this block, or issues an allowlisted partnerCode on request to
syndicationapi@cqc.org.uk, this starts working with no code changes.
"""
import asyncio
import math

import httpx

from app.services import _cache

API_BASE = "https://api.cqc.org.uk/public/v1"
PARTNER_CODE = "propertyvalue"
CACHE_TTL_S = 86400 * 7  # CQC ratings are reassessed periodically, not real-time
SEARCH_RADIUS_M = 2000  # matches the gp/dentist Overpass search radius in amenities.py
RESULT_LIMIT = 5
LIST_PAGE_SIZE = 50  # how many candidates to pull before filtering/ranking by distance


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def nearby_ratings(lat: float, lon: float, postcode: str) -> dict:
    """Returns {"gp": [...], "dentist": [...], "care_home": [...]},
    each a list of {name, rating, distance_m} sorted nearest-first.
    Any category that fails or returns nothing is an empty list, not
    an error - this is a "nice to have" enrichment, not a service the
    rest of the page depends on."""
    key = _cache.coord_key("cqc_ratings", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch(lat, lon, postcode)
    _cache.set(key, result)
    return result


async def _fetch(lat: float, lon: float, postcode: str) -> dict:
    outcode = postcode.split(" ")[0] if postcode else ""
    if not outcode:
        return {"gp": [], "dentist": [], "care_home": []}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            list_response = await client.get(
                f"{API_BASE}/locations",
                params={"postcode": outcode, "perPage": LIST_PAGE_SIZE, "partnerCode": PARTNER_CODE},
            )
            list_response.raise_for_status()
            summaries = list_response.json().get("locations", [])
            if not summaries:
                return {"gp": [], "dentist": [], "care_home": []}

            details = await asyncio.gather(
                *(
                    client.get(
                        f"{API_BASE}/locations/{s['locationId']}",
                        params={"partnerCode": PARTNER_CODE},
                    )
                    for s in summaries
                    if s.get("locationId")
                ),
                return_exceptions=True,
            )
    except httpx.HTTPError:
        return {"gp": [], "dentist": [], "care_home": []}

    out = {"gp": [], "dentist": [], "care_home": []}
    for response in details:
        if isinstance(response, Exception) or response.status_code != 200:
            continue
        loc = response.json()
        loc_lat, loc_lon = loc.get("onspdLatitude"), loc.get("onspdLongitude")
        if loc_lat is None or loc_lon is None:
            continue
        distance_m = _haversine_m(lat, lon, loc_lat, loc_lon)
        if distance_m > SEARCH_RADIUS_M:
            continue

        rating = ((loc.get("currentRatings") or {}).get("overall") or {}).get("rating")
        if not rating or rating == "No published rating":
            continue

        entry = {"name": loc.get("name", "Unnamed"), "rating": rating, "distance_m": round(distance_m)}
        type_names = " ".join(t.get("type", "") for t in loc.get("locationTypes", []))
        if loc.get("careHome") == "Y":
            out["care_home"].append(entry)
        elif "Dentist" in type_names or "Dental" in type_names:
            out["dentist"].append(entry)
        elif "GP" in type_names:
            out["gp"].append(entry)

    for category in out.values():
        category.sort(key=lambda e: e["distance_m"])
        del category[RESULT_LIMIT:]
    return out
