"""Local amenities (restaurants, supermarkets, hospitals, pharmacies,
pubs, schools) and the nearest train/tube station, from OpenStreetMap's
free Overpass API (no key required).

Schools are proximity only, NOT catchment areas - there's no reliable
free UK-wide catchment API (patchy, inconsistent per-council data at
best), so we deliberately don't claim to show one.
"""
import math
import re

import httpx

from app.services import _cache

# Two independent public Overpass instances - the primary is known to
# reject some hosting-provider IP ranges outright, so we fall back to
# a mirror rather than surfacing that as an outage.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# Short per-attempt timeout so a slow/blocked endpoint fails over to
# the mirror quickly instead of dragging the whole page load out.
OVERPASS_TIMEOUT_S = 8
CACHE_TTL_S = 3600  # OSM POI data doesn't change fast enough to need per-request freshness

# (label, overpass tag filter, search radius in metres)
AMENITY_QUERIES = [
    ("restaurant", '["amenity"="restaurant"]', 1000),
    ("supermarket", '["shop"="supermarket"]', 1000),
    ("pharmacy", '["amenity"="pharmacy"]', 1000),
    ("pub", '["amenity"="pub"]', 1000),
    ("hospital", '["amenity"="hospital"]', 3000),
    ("school", '["amenity"="school"]', 1500),
]
STATION_RADIUS_M = 3000


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _element_latlon(el: dict):
    if "lat" in el:
        return el["lat"], el["lon"]
    center = el.get("center")
    return (center["lat"], center["lon"]) if center else (None, None)


async def _query_overpass(query: str) -> list[dict]:
    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            async with httpx.AsyncClient(timeout=OVERPASS_TIMEOUT_S) as client:
                response = await client.post(
                    endpoint,
                    data={"data": query},
                    headers={"User-Agent": "curl/8.7.1"},
                )
            response.raise_for_status()
            return response.json().get("elements", [])
        except httpx.HTTPError as exc:
            last_error = exc
    raise last_error


def _school_type(tags: dict) -> str:
    school = tags.get("school", "")
    if school:
        return school.replace("_", " ").strip().capitalize()
    levels = set((tags.get("isced:level") or "").split(";"))
    if levels & {"2", "3"}:
        return "Secondary"
    if "1" in levels:
        return "Primary"
    if "0" in levels:
        return "Nursery"
    return ""


async def nearby_amenities_and_station(lat: float, lon: float) -> dict:
    key = _cache.coord_key("amenities", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_amenities_and_station(lat, lon)
    _cache.set(key, result)
    return result


async def _fetch_amenities_and_station(lat: float, lon: float) -> dict:
    clauses = "".join(
        f'nwr{tag}(around:{radius},{lat},{lon});' for _, tag, radius in AMENITY_QUERIES
    )
    clauses += (
        f'nwr["railway"~"station|halt"][!"disused:railway"](around:{STATION_RADIUS_M},{lat},{lon});'
        f'nwr["station"="subway"][!"disused:railway"](around:{STATION_RADIUS_M},{lat},{lon});'
    )
    query = f"[out:json][timeout:20];({clauses});out center tags;"
    elements = await _query_overpass(query)

    categories = {label: [] for label, _, _ in AMENITY_QUERIES}
    stations = []

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name", "Unnamed")
        el_lat, el_lon = _element_latlon(el)
        if el_lat is None:
            continue
        distance_m = round(_haversine_m(lat, lon, el_lat, el_lon))

        amenity = tags.get("amenity")
        shop = tags.get("shop")
        is_station = bool(
            re.match(r"station|halt", tags.get("railway", "")) or tags.get("station") == "subway"
        )

        if is_station:
            stations.append({
                "id": el["id"],
                "type": el["type"],
                "name": name,
                "network": tags.get("network", ""),
                "distance_m": distance_m,
            })
        elif amenity == "restaurant":
            categories["restaurant"].append({"name": name, "distance_m": distance_m})
        elif shop == "supermarket":
            categories["supermarket"].append({"name": name, "distance_m": distance_m})
        elif amenity == "pharmacy":
            categories["pharmacy"].append({"name": name, "distance_m": distance_m})
        elif amenity == "pub":
            categories["pub"].append({"name": name, "distance_m": distance_m})
        elif amenity == "hospital":
            categories["hospital"].append({"name": name, "distance_m": distance_m})
        elif amenity == "school":
            categories["school"].append({
                "name": name,
                "distance_m": distance_m,
                "type": _school_type(tags),
            })

    for items in categories.values():
        items.sort(key=lambda i: i["distance_m"])

    nearest_station = None
    if stations:
        stations.sort(key=lambda s: s["distance_m"])
        nearest_station = stations[0]
        try:
            nearest_station["lines"] = await _station_lines(nearest_station["type"], nearest_station["id"])
        except httpx.HTTPError:
            nearest_station["lines"] = []

    return {"categories": categories, "station": nearest_station}


async def _station_lines(el_type: str, el_id: int) -> list[str]:
    """Line names serving a station, via public_transport=stop_area
    relations referencing it (well-tagged for London Underground;
    often unavailable for National Rail stations - that's fine, we
    just show fewer details rather than guessing."""
    type_letter = {"node": "n", "way": "w", "relation": "r"}[el_type]
    query = (
        f"[out:json][timeout:20];"
        f"{el_type}({el_id});"
        f'rel(b{type_letter})["public_transport"="stop_area"];'
        f"out tags;"
    )
    elements = await _query_overpass(query)

    lines = []
    for el in elements:
        name = el.get("tags", {}).get("name", "")
        match = re.search(r"\(([^)]+)\)\s*$", name)
        if match:
            lines.append(match.group(1))
    return lines
