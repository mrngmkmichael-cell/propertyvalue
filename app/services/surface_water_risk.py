"""Risk of Flooding from Surface Water (RoFSW) - a distinct flood
mechanism from flood_zones.py's river/sea Flood Zone 2/3 (and from the
real-time active-warnings data in flood.py). Surface water floods when
rainwater can't drain away fast enough and pools or flows over the
ground - it can affect a property rated Flood Zone 1 (low river/sea
risk) that still floods regularly in heavy rain, which is exactly the
gap this fills.

Queried live via the Environment Agency's own ArcGIS FeatureServer -
found after the WMS GetFeatureInfo trick used elsewhere in this project
(coal_mining.py, historically noise.py/radon.py) returned "no features
were found" for every real, verified-flood-prone UK location tested
against this dataset's WMS layer, and the OGC API Features endpoint
used by flood_zones.py isn't available for this dataset either. This
FeatureServer is the Environment Agency's own hosted item (owner
"Environment_Agency" on ArcGIS Online) and genuinely supports Query -
confirmed against real polygon data, not just documentation.

Three separate extent layers, one per annual-probability threshold -
checked from most to least frequent, the same "highest severity wins"
approach as flood_zones.py's Zone 2/3 check. No polygon at any
threshold = Very Low risk, same "unmapped means the lowest band"
convention used there too.
"""
import asyncio

import httpx

from app.services import _cache

BASE_URL = (
    "https://services1.arcgis.com/JZM7qJpmv7vJ0Hzx/arcgis/rest/services/"
    "Risk_of_Flooding_from_Surface_Water_Extents/FeatureServer"
)
CACHE_TTL_S = 86400 * 30  # static planning data between EA revisions

# (layer id, risk label, annual probability)
_LAYERS = [
    (0, "High risk", "1 in 30 (3.3%) or greater"),
    (1, "Medium risk", "1 in 100 (1%) to 1 in 30"),
    (2, "Low risk", "1 in 1000 (0.1%) to 1 in 100"),
]
VERY_LOW_LABEL = "Very low risk"
VERY_LOW_PROBABILITY = "Less than 1 in 1000 (0.1%)"


async def risk_for(lat: float, lon: float) -> dict | None:
    key = _cache.coord_key("surface_water_risk", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch(lat, lon)
    if result is not None:
        _cache.set(key, result)
    return result


async def _query_layer(client: httpx.AsyncClient, layer: int, lat: float, lon: float) -> bool:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "OBJECTID",
        "returnGeometry": "false",
        "f": "json",
    }
    response = await client.get(f"{BASE_URL}/{layer}/query", params=params, timeout=10)
    response.raise_for_status()
    return bool(response.json().get("features"))


async def _fetch(lat: float, lon: float) -> dict | None:
    try:
        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(
                *(_query_layer(client, layer, lat, lon) for layer, _, _ in _LAYERS)
            )
    except httpx.HTTPError:
        return None

    for (layer, label, probability), present in zip(_LAYERS, results):
        if present:
            return {"label": label, "probability": probability}
    return {"label": VERY_LOW_LABEL, "probability": VERY_LOW_PROBABILITY}
