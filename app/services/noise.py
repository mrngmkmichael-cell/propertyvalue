"""Road and rail noise levels from DEFRA's strategic noise maps
(Round 4, 2022), queried live via the Environment Agency's WMS
GetFeatureInfo point-query API - no key required, and no need to
download/parse the underlying noise-grid rasters ourselves.

Lden = day-evening-night average noise level in dB(A), the standard
annoyance indicator used across these maps: a 24-hour average with
penalties added for evening and night-time noise.
"""
import asyncio

import httpx

from app.services import _cache

WMS_BASE = "https://environment.data.gov.uk/geoservices/datasets"
ROAD_DATASET_ID = "562c9d56-7c2d-4d42-83bb-578d6e97a517"
RAIL_DATASET_ID = "3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947"
ROAD_LAYER = "Road_Noise_Lden_England_Round_4_All"
RAIL_LAYER = "Rail_Noise_Lden_England_Round_4_All"
CACHE_TTL_S = 86400  # static until the next mapping round, every ~5 years

# Grid cell half-width for the query box, in degrees - small enough to
# stay within a single ~10m raster cell.
BOX_DEGREES = 0.0005


def _band_label(db: float) -> str:
    if db < 55:
        return "Low"
    if db < 65:
        return "Moderate"
    if db < 75:
        return "High"
    return "Very high"


async def _query_layer(client: httpx.AsyncClient, dataset_id: str, layer: str, lat: float, lon: float) -> float | None:
    d = BOX_DEGREES
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetFeatureInfo",
        "LAYERS": layer,
        "QUERY_LAYERS": layer,
        "CRS": "CRS:84",
        "BBOX": f"{lon - d},{lat - d},{lon + d},{lat + d}",
        "WIDTH": "3",
        "HEIGHT": "3",
        "I": "1",
        "J": "1",
        "INFO_FORMAT": "application/json",
    }
    try:
        response = await client.get(f"{WMS_BASE}/{dataset_id}/wms", params=params, timeout=10)
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    features = response.json().get("features", [])
    if not features:
        return None
    value = features[0].get("properties", {}).get("GRAY_INDEX")
    if not isinstance(value, (int, float)) or value <= 0:
        # 0 (or missing) is the raster's nodata sentinel within the
        # dataset's extent - real levels never read exactly 0, and
        # the published data has a 35-40dB minimum threshold anyway.
        return None
    return round(value)


async def noise_near(lat: float, lon: float) -> dict:
    key = _cache.coord_key("noise", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached

    async with httpx.AsyncClient() as client:
        road_db, rail_db = await asyncio.gather(
            _query_layer(client, ROAD_DATASET_ID, ROAD_LAYER, lat, lon),
            _query_layer(client, RAIL_DATASET_ID, RAIL_LAYER, lat, lon),
        )

    result = {
        "road_db": road_db,
        "road_label": _band_label(road_db) if road_db is not None else None,
        "rail_db": rail_db,
        "rail_label": _band_label(rail_db) if rail_db is not None else None,
    }
    _cache.set(key, result)
    return result
