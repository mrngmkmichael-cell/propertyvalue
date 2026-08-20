"""Road, rail and airport noise levels from DEFRA's strategic noise
maps (Round 4, 2022), queried live via the Environment Agency's WMS
GetFeatureInfo point-query API - no key required, and no need to
download/parse the underlying noise-grid rasters ourselves.

Lden = day-evening-night average noise level in dB(A), the standard
annoyance indicator used across these maps: a 24-hour average with
penalties added for evening and night-time noise.

Note: a similar-looking "Risk of Flooding from Surface Water" WMS
dataset in the same family was investigated for the flood section
but abandoned - it returned no features at plausible flood-risk
locations and started 403-ing under light repeated testing, unlike
these three which have been reliable.
"""
import asyncio

import httpx

from app.services import _cache

WMS_BASE = "https://environment.data.gov.uk/geoservices/datasets"
ROAD_DATASET_ID = "562c9d56-7c2d-4d42-83bb-578d6e97a517"
RAIL_DATASET_ID = "3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947"
AIRPORT_DATASET_ID = "dac9cba4-abe7-43bd-b8e9-8a83da52edd8"
ROAD_LAYER = "Road_Noise_Lden_England_Round_4_All"
RAIL_LAYER = "Rail_Noise_Lden_England_Round_4_All"
AIRPORT_LAYER = "Airport_Noise_ALL_Lden"
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
    # Left to raise on request - see radon.py's identical comment.
    # noise_near below gathers all three layers with
    # return_exceptions=True and only treats a TOTAL failure (all
    # three layers erroring) as an error; one flaky layer degrades to
    # "no data for that source" rather than losing the other two.
    response = await client.get(f"{WMS_BASE}/{dataset_id}/wms", params=params, timeout=10)
    response.raise_for_status()

    features = response.json().get("features", [])
    if not features:
        return None
    value = features[0].get("properties", {}).get("GRAY_INDEX")
    if not isinstance(value, (int, float)) or not (0 < value < 150):
        # Nodata sentinels vary by dataset: road/rail use 0 within
        # the extent, the airport layer uses ~3.4028235e38 (the
        # standard float32 nodata value). Real levels never read
        # exactly 0 or anywhere near either sentinel, and the
        # published data has a 35-40dB minimum threshold anyway.
        return None
    return round(value)


async def noise_near(lat: float, lon: float) -> dict:
    key = _cache.coord_key("noise", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached

    async with httpx.AsyncClient() as client:
        road_db, rail_db, airport_db = await asyncio.gather(
            _query_layer(client, ROAD_DATASET_ID, ROAD_LAYER, lat, lon),
            _query_layer(client, RAIL_DATASET_ID, RAIL_LAYER, lat, lon),
            _query_layer(client, AIRPORT_DATASET_ID, AIRPORT_LAYER, lat, lon),
            return_exceptions=True,
        )

    # All three layers erroring means the WMS endpoint itself is down,
    # not "no noise sources near this point" - raise so the caller's
    # own gather sees this as a real failure instead of caching a
    # false "quiet" reading for a day. One or two layers failing while
    # the others succeed degrades gracefully to "no data" for just
    # that source instead.
    if isinstance(road_db, Exception) and isinstance(rail_db, Exception) and isinstance(airport_db, Exception):
        raise road_db
    road_db = None if isinstance(road_db, Exception) else road_db
    rail_db = None if isinstance(rail_db, Exception) else rail_db
    airport_db = None if isinstance(airport_db, Exception) else airport_db

    result = {
        "road_db": road_db,
        "road_label": _band_label(road_db) if road_db is not None else None,
        "rail_db": rail_db,
        "rail_label": _band_label(rail_db) if rail_db is not None else None,
        "airport_db": airport_db,
        "airport_label": _band_label(airport_db) if airport_db is not None else None,
    }
    _cache.set(key, result)
    return result
