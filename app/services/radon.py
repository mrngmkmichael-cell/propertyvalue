"""Radon gas risk from the British Geological Survey's Indicative
Atlas of Radon, queried live via their ArcGIS "identify" point-query
endpoint - no key required, no bulk grid to mirror ourselves.

CLASS_MAX is BGS/UKHSA's standard 1-6 radon potential banding: the
percentage of homes in that 1km grid square estimated to be at or
above the UK Action Level (200 Bq/m3).
"""
import httpx

from app.services import _cache

IDENTIFY_URL = "https://map.bgs.ac.uk/arcgis/rest/services/GeoIndex_Onshore/radon/MapServer/identify"
CACHE_TTL_S = 86400 * 30  # the underlying atlas is static between BGS revisions

CLASS_LABELS = {
    "1": "Low (under 1% of homes above the Action Level)",
    "2": "Low-moderate (1-3%)",
    "3": "Moderate (3-5%)",
    "4": "Moderate-high (5-10%)",
    "5": "High (10-30%)",
    "6": "Very high (30% or more)",
}


async def risk_near(lat: float, lon: float) -> dict | None:
    key = _cache.coord_key("radon", lat, lon)
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
        "layers": "all",
        "tolerance": "2",
        "mapExtent": f"{lon - 0.01},{lat - 0.01},{lon + 0.01},{lat + 0.01}",
        "imageDisplay": "400,400,96",
        "returnGeometry": "false",
        "f": "json",
    }
    # Left to raise - the caller's gather(return_exceptions=True)
    # distinguishes "the API call failed" from "it succeeded and found
    # nothing" (the `if not results` case below, a genuine result).
    # Catching and returning None for both here made a live BGS outage
    # indistinguishable from "no radon risk here", and risk_near below
    # would then cache that wrong answer for 30 days.
    response = await httpx.AsyncClient(timeout=10).get(IDENTIFY_URL, params=params)
    response.raise_for_status()

    results = response.json().get("results", [])
    if not results:
        return None

    attrs = results[0].get("attributes", {})
    radon_class = str(attrs.get("CLASS_MAX", "")).strip()
    if not radon_class:
        return None

    return {
        "class": radon_class,
        "label": CLASS_LABELS.get(radon_class, f"Class {radon_class}"),
    }
