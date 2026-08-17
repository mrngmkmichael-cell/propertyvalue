"""Approximate garden/frontage compass orientation, derived from the
property's OSM building footprint and the nearest named road - a
cheap, genuinely useful proxy for "which way does the garden face"
(a real thing UK buyers ask about), since there's no free UK dataset
for actual sunlight-hours/overshadowing - that needs LIDAR-based
shadow modelling, well beyond a live per-request lookup.

Method: the direction from the building's centroid to the nearest
point on the nearest named road is taken as the FRONT-facing
direction (the side that addresses the street); the opposite compass
point is the REAR/garden-facing direction. This assumes a typical UK
street-fronting layout - it doesn't verify the building's actual
facade orientation, and doesn't account for neighbouring buildings or
trees casting shade. Returns None if no building footprint or nearby
named road is found (e.g. a converted flat block set back in its own
grounds, or a rural building without a nearby named road).
"""
import math

from app.services import _cache
from app.services.amenities import _haversine_m, _query_overpass

BUILDING_RADIUS_M = 120  # postcode centroids (all this app has, even with a house number given -
                          # see main.py's lat/lon, always postcode-level) can sit some way from any
                          # single building, especially in areas where OSM's building-footprint
                          # coverage is sparse - a tight radius left many real searches with no
                          # building at all
ROAD_RADIUS_M = 150
CACHE_TTL_S = 86400 * 30  # buildings/roads don't move

COMPASS_POINTS = [
    "North", "North-East", "East", "South-East",
    "South", "South-West", "West", "North-West",
]


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _compass_label(bearing: float) -> str:
    return COMPASS_POINTS[round(bearing / 45) % 8]


def _point_in_ring(lat: float, lon: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        yi, xi = ring[i]["lat"], ring[i]["lon"]
        yj, xj = ring[j]["lat"], ring[j]["lon"]
        if (yi > lat) != (yj > lat):
            x_intersect = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_intersect:
                inside = not inside
        j = i
    return inside


def _centroid(ring: list) -> tuple[float, float]:
    pts = ring[:-1] if ring and ring[0] == ring[-1] else ring  # drop the closing duplicate node if present
    return sum(p["lat"] for p in pts) / len(pts), sum(p["lon"] for p in pts) / len(pts)


async def orientation_for(lat: float, lon: float) -> dict | None:
    key = _cache.coord_key("orientation", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_orientation(lat, lon)
    if result is not None:
        _cache.set(key, result)
    return result


async def _fetch_orientation(lat: float, lon: float) -> dict | None:
    query = (
        f"[out:json][timeout:15];"
        f'way["building"](around:{BUILDING_RADIUS_M},{lat},{lon});out geom;'
        f'way["highway"]["name"](around:{ROAD_RADIUS_M},{lat},{lon});out geom;'
    )
    elements = await _query_overpass(query)  # left to raise - the caller's gather(return_exceptions=True)
                                              # distinguishes "the API call failed" from "no data found",
                                              # same as every other Overpass-backed service in this app

    buildings = [el for el in elements if el.get("tags", {}).get("building") and el.get("geometry")]
    roads = [el for el in elements if el.get("tags", {}).get("highway") and el.get("geometry")]
    if not buildings or not roads:
        return None

    # Prefer whichever building footprint actually contains the point;
    # fall back to the nearest centroid if none strictly contains it
    # (the point can sit right on a shared wall/boundary edge).
    containing = [b for b in buildings if _point_in_ring(lat, lon, b["geometry"])]
    target = containing[0] if containing else min(
        buildings, key=lambda b: _haversine_m(lat, lon, *_centroid(b["geometry"]))
    )
    centroid_lat, centroid_lon = _centroid(target["geometry"])

    nearest_road_name, nearest_road_dist = None, float("inf")
    nearest_point = None
    for road in roads:
        name = road["tags"]["name"]
        for pt in road["geometry"]:
            d = _haversine_m(centroid_lat, centroid_lon, pt["lat"], pt["lon"])
            if d < nearest_road_dist:
                nearest_road_dist, nearest_road_name = d, name
                nearest_point = (pt["lat"], pt["lon"])

    if nearest_point is None:
        return None

    front_bearing = _bearing(centroid_lat, centroid_lon, *nearest_point)
    rear_bearing = (front_bearing + 180) % 360

    return {
        "front_facing": _compass_label(front_bearing),
        "rear_facing": _compass_label(rear_bearing),
        "nearest_road": nearest_road_name,
    }
