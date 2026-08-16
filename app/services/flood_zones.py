"""Flood Zone 2/3 (rivers and sea) classification from the
Environment Agency's "Flood Map for Planning" dataset - the
long-term planning flood risk zone, distinct from the real-time
active-warnings data in flood.py.

Queried live via the OGC API Features endpoint (no key required).
The WMS GetFeatureInfo point-query trick used elsewhere in this
project (noise.py, radon.py) doesn't return results against this
particular layer for reasons that weren't clear even after direct
testing, so this instead fetches nearby polygons via OGC Features and
does the point-in-polygon test itself - more code, but confirmed
working against known flood-risk locations.

Zone 3 = high probability (>=1% river / >=0.5% sea annual risk).
Zone 2 = medium probability. Anything not covered by a mapped
Zone 2/3 polygon is Zone 1 (low probability) - England's planning
system doesn't publish a separate "Zone 1" polygon layer, it's
just everywhere else.
"""
import httpx

from app.services import _cache

DATASET_ID = "04532375-a198-476e-985e-0579a0a11b47"
FEATURES_URL = (
    f"https://environment.data.gov.uk/geoservices/datasets/{DATASET_ID}/ogc/features/v1/"
    "collections/Flood_Zones_2_3_Rivers_and_Sea/items"
)
BBOX_DEGREES = 0.005  # ~0.35km half-width - some areas have very complex polygon geometry
                       # (a small bbox near Tewkesbury alone returned a 1.4MB response), so
                       # this stays modest to keep response times/timeout risk reasonable
CACHE_TTL_S = 86400 * 30  # static planning data between EA revisions

ZONE_LABELS = {
    3: "Zone 3 (high probability)",
    2: "Zone 2 (medium probability)",
    1: "Zone 1 (low probability)",
}


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


async def zone_for(lat: float, lon: float) -> dict | None:
    key = _cache.coord_key("flood_zone", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_zone(lat, lon)
    if result is not None:
        _cache.set(key, result)
    return result


async def _fetch_zone(lat: float, lon: float) -> dict | None:
    d = BBOX_DEGREES
    params = {
        "bbox": f"{lon - d},{lat - d},{lon + d},{lat + d}",
        "limit": 200,
        "f": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(FEATURES_URL, params=params)
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    features = response.json().get("features", [])
    best_zone = 1
    best_source = None
    for feat in features:
        props = feat.get("properties", {})
        flood_zone = props.get("flood_zone")
        zone_num = 3 if flood_zone == "FZ3" else (2 if flood_zone == "FZ2" else None)
        if zone_num is None or zone_num <= best_zone:
            continue
        geometry = feat.get("geometry", {})
        if _point_in_geometry(lon, lat, geometry):
            best_zone = zone_num
            best_source = props.get("flood_source")
            if best_zone == 3:
                break

    return {
        "zone": best_zone,
        "label": ZONE_LABELS[best_zone],
        "source": best_source,
    }
