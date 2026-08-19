"""Storm overflow / sewage discharge history near a property, from the
Environment Agency's own official Event Duration Monitoring annual
returns dataset - queried live via its public ArcGIS Feature Service,
not the environment.data.gov.uk file-download portal (which turned
out to be unreliable - frequent 502/504s and download timeouts). Same
underlying regulatory data, OGL-licensed, just accessed through a
more reliable channel: a proper spatial query instead of a bulk file.

Each outfall has one row per year it reported, so a query returns
several rows per outfall - grouped down to one (the latest year) per
outfall here, then sorted by distance.
"""
import math

import httpx

from app.services import _cache

QUERY_URL = (
    "https://services1.arcgis.com/JZM7qJpmv7vJ0Hzx/arcgis/rest/services/"
    "edm_annual_returns_all_years_public/FeatureServer/0/query"
)
CACHE_TTL_S = 86400 * 7  # annual regulatory data - a week's staleness is fine
SEARCH_RADIUS_M = 1500
RESULT_LIMIT = 3

OUT_FIELDS = ",".join([
    "unique_id",
    "site_name_wasc_op_name",
    "water_company_name",
    "annual_return_year",
    "counted_spills_12_24hr_calculated",
    "total_spill_duration_hrs_calculated",
    "receiving_water_environment_common_name_ea_condat",
])


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def nearby_outfalls(lat: float, lon: float) -> list[dict]:
    key = _cache.coord_key("sewage_discharge", lat, lon)
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
        "outFields": OUT_FIELDS,
        "orderByFields": "annual_return_year DESC",
        "resultRecordCount": "200",
        "f": "json",
    }
    try:
        response = await httpx.AsyncClient(timeout=15).get(QUERY_URL, params=params)
        response.raise_for_status()
    except httpx.HTTPError:
        return []

    data = response.json()
    if "error" in data:
        return []

    # One row per outfall per reporting year - keep only the most
    # recent year's row per outfall (rows already sorted DESC by year).
    # Grouped by rounded coordinates rather than unique_id: unique_id
    # is inconsistently populated (null on some years' rows for the
    # very same physical outfall), which would otherwise split one
    # real outfall into duplicate entries.
    by_outfall = {}
    for feature in data.get("features", []):
        attrs = feature["attributes"]
        geom = feature.get("geometry")
        if not geom:
            continue
        outfall_key = (round(geom["x"], 5), round(geom["y"], 5))
        if outfall_key in by_outfall:
            continue
        by_outfall[outfall_key] = {
            "name": attrs.get("site_name_wasc_op_name") or "Unnamed outfall",
            "water_company": attrs.get("water_company_name"),
            "year": attrs.get("annual_return_year"),
            "spill_count": attrs.get("counted_spills_12_24hr_calculated"),
            "duration_hrs": attrs.get("total_spill_duration_hrs_calculated"),
            "receiving_water": (attrs.get("receiving_water_environment_common_name_ea_condat") or "").title(),
            "distance_m": round(_haversine_m(lat, lon, geom["y"], geom["x"])),
        }

    outfalls = sorted(by_outfall.values(), key=lambda o: o["distance_m"])
    return outfalls[:RESULT_LIMIT]
