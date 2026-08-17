"""Historic (closed, pre-1994-regulation) landfill sites near a
property, from the Environment Agency's Historic Landfill Sites
dataset - queried live via the same OGC API Features + manual
point-in-polygon approach as flood_zones.py (point-in-polygon logic
duplicated here rather than shared, matching that module's own
precedent).

This is about ground contamination/gas risk from old tipping, not
flood risk - a property built on or near a former landfill can face
methane/leachate issues and mortgage/insurance complications even
decades after the site closed. Being near one isn't necessarily
disqualifying, but it's the kind of thing a buyer should know to ask
about (site investigation reports, gas protection measures, etc).
"""
import httpx

from app.services import _cache

FEATURES_URL = (
    "https://environment.data.gov.uk/spatialdata/historic-landfill/ogc/features/v1/"
    "collections/Historic_Landfill_Sites/items"
)
BBOX_DEGREES = 0.01  # ~0.7km half-width - generous enough to catch "nearby, not on" sites
NEARBY_THRESHOLD_M = 500
CACHE_TTL_S = 86400 * 30  # static historic dataset, revised infrequently


def _point_in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y):
            x_intersect = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_intersect:
                inside = not inside
        j = i
    return inside


def _point_in_polygon(x: float, y: float, rings: list) -> bool:
    if not rings or not _point_in_ring(x, y, rings[0]):
        return False
    return not any(_point_in_ring(x, y, hole) for hole in rings[1:])


def _point_in_geometry(x: float, y: float, geometry: dict) -> bool:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "Polygon":
        return _point_in_polygon(x, y, coords)
    if gtype == "MultiPolygon":
        return any(_point_in_polygon(x, y, poly) for poly in coords)
    return False


def _nearest_vertex_distance_m(lat: float, lon: float, geometry: dict) -> float:
    import math

    def haversine(lat1, lon1, lat2, lon2):
        r = 6371000
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(a))

    gtype = geometry.get("type")
    polygons = geometry.get("coordinates", [])
    rings = polygons if gtype == "Polygon" else [ring for poly in polygons for ring in poly]
    best = float("inf")
    for ring in rings:
        for x, y in ring:
            best = min(best, haversine(lat, lon, y, x))
    return best


async def check_near(lat: float, lon: float) -> dict:
    key = _cache.coord_key("historic_landfill", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch(lat, lon)
    _cache.set(key, result)
    return result


async def _fetch(lat: float, lon: float) -> dict:
    d = BBOX_DEGREES
    params = {
        "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}",
        "limit": 100,
        "f": "json",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(FEATURES_URL, params=params)
    response.raise_for_status()

    features = response.json().get("features", [])
    on_site_name = None
    nearest_name, nearest_dist = None, float("inf")

    for feat in features:
        geometry = feat.get("geometry", {})
        props = feat.get("properties", {})
        name = props.get("site_name") or "Unnamed site"
        if _point_in_geometry(lon, lat, geometry):
            on_site_name = name
            break
        dist = _nearest_vertex_distance_m(lat, lon, geometry)
        if dist < nearest_dist:
            nearest_dist, nearest_name = dist, name

    if on_site_name:
        return {"status": "on_site", "site_name": on_site_name}
    if nearest_name and nearest_dist <= NEARBY_THRESHOLD_M:
        return {"status": "nearby", "site_name": nearest_name, "distance_m": round(nearest_dist)}
    return {"status": "clear"}
