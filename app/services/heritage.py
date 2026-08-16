"""Nearby listed buildings from Historic England's National Heritage
List for England (NHLE), queried live via their ArcGIS FeatureServer
- no key required.

This shows what's listed *nearby*, not whether this specific address
is listed - we don't do exact building-footprint-to-address matching,
so asserting "this property is listed" would be false precision we
can't actually back up.
"""
import math

import httpx

from app.services import _cache

QUERY_URL = (
    "https://services-eu1.arcgis.com/ZOdPfBS3aqqDYPUQ/arcgis/rest/services/"
    "National_Heritage_List_for_England_NHLE_v02_VIEW/FeatureServer/0/query"
)
SEARCH_RADIUS_M = 500
CACHE_TTL_S = 86400 * 7  # listings change rarely; NHLE says "updated daily" but our use is proximity-only
RESULT_LIMIT = 5


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def nearby_listed_buildings(lat: float, lon: float) -> list[dict]:
    key = _cache.coord_key("heritage", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_nearby(lat, lon)
    _cache.set(key, result)
    return result


async def _fetch_nearby(lat: float, lon: float) -> list[dict]:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "distance": str(SEARCH_RADIUS_M),
        "units": "esriSRUnit_Meter",
        "outFields": "Name,Grade,hyperlink",
        "returnGeometry": "true",
        "f": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(QUERY_URL, params=params)
        response.raise_for_status()
    except httpx.HTTPError:
        return []

    buildings = []
    for feat in response.json().get("features", []):
        points = feat.get("geometry", {}).get("points", [])
        if not points:
            continue
        b_lon, b_lat = points[0]
        buildings.append({
            "name": (feat["attributes"].get("Name") or "Unnamed").title(),
            "grade": feat["attributes"].get("Grade", ""),
            "url": feat["attributes"].get("hyperlink", ""),
            "distance_m": round(_haversine_m(lat, lon, b_lat, b_lon)),
        })

    buildings.sort(key=lambda b: b["distance_m"])
    return buildings[:RESULT_LIMIT]
