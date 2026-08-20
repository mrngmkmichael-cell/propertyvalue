"""Clay shrink-swell subsidence risk from the British Geological
Survey's GeoClimate dataset, queried live via their ArcGIS "identify"
point-query endpoint - no key required, no bulk grid to mirror
ourselves. Same free-service pattern as radon.py.

Important distinction: BGS's precise *current* shrink-swell hazard
rating (GeoSure) is a commercial, paid product - not something a
free-tier site can offer. GeoClimate is a different, genuinely open
(OGL) BGS dataset: it rates how *likely climate change is to worsen*
shrink-swell susceptibility by a given horizon year, not the current
baseline hazard level. That's a real, useful, and arguably more
forward-relevant signal for a buyer looking at a 25-30 year mortgage
- but it must be labelled accurately as a climate-change trend
indicator, not confused with a present-day hazard score.
"""
import httpx

from app.services import _cache

IDENTIFY_URL = "https://map.bgs.ac.uk/arcgis/rest/services/GeoIndex_Onshore/geoclimate_basic/MapServer/identify"
CACHE_TTL_S = 86400 * 30  # the underlying atlas is static between BGS revisions

# Layer 0 = ShrinkSwell 2030 Basic v1, layer 1 = ShrinkSwell 2050 Basic v1
LAYERS = "0,1"

CLASS_LABELS = {
    "Improbable": "Improbable",
    "Possible": "Possible",
    "Probable": "Probable",
}

CLASS_ORDER = {"Improbable": 0, "Possible": 1, "Probable": 2}


async def risk_near(lat: float, lon: float) -> dict | None:
    key = _cache.coord_key("clay_risk", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_risk(lat, lon)
    _cache.set(key, result)
    return result


async def _fetch_risk(lat: float, lon: float) -> dict | None:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "sr": "4326",
        "layers": f"visible:{LAYERS}",
        "tolerance": "2",
        "mapExtent": f"{lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}",
        "imageDisplay": "400,400,96",
        "returnGeometry": "false",
        "f": "json",
    }
    # Left to raise - see radon.py's identical comment. Catching this
    # and returning None made a live BGS outage indistinguishable from
    # "no shrink-swell data here", and risk_near below would cache
    # that wrong answer for 30 days.
    response = await httpx.AsyncClient(timeout=10).get(IDENTIFY_URL, params=params)
    response.raise_for_status()

    by_horizon = {}
    for res in response.json().get("results", []):
        attrs = res.get("attributes", {})
        cls = attrs.get("CLASS")
        if not cls or cls not in CLASS_ORDER:
            continue
        if "2030" in res.get("layerName", ""):
            by_horizon["2030"] = cls
        elif "2050" in res.get("layerName", ""):
            by_horizon["2050"] = cls

    if not by_horizon:
        return None

    return {
        "class_2030": by_horizon.get("2030"),
        "class_2050": by_horizon.get("2050"),
        "label_2030": CLASS_LABELS.get(by_horizon.get("2030")),
        "label_2050": CLASS_LABELS.get(by_horizon.get("2050")),
    }
