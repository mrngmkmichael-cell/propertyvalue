"""Coal Mining Reporting Area check - whether a property sits inside
the Coal Authority's known extent of coal mining activity, the same
area a solicitor's CON29M coal mining search flags as requiring a
report. From the National Coal Mining Database, published free under
OGL by the Coal Authority (now the Mining Remediation Authority) and
hosted via British Geological Survey infrastructure.

No live FeatureServer for this dataset exposes a public Query or
identify capability - checked both the BGS-hosted mirror and the
Authority's own newer ArcGIS Online service, and both are
Map/tile-only ("TilesOnly" in the newer one's capabilities). The one
operation this service does expose for a point lookup is WMS
GetFeatureInfo, queried here against a minimal (near-zero) pixel
window around the coordinate rather than the dedicated point-query
APIs used elsewhere in this project.
"""
import re

import httpx

from app.services import _cache

WMS_URL = (
    "https://map.bgs.ac.uk/arcgis/services/CoalAuthority/"
    "coalauthority_coal_mining_reporting_areas/MapServer/WMSServer"
)
LAYER = "Coal.Mining.Reporting.Area"
CACHE_TTL_S = 86400 * 30  # coalfield reporting area boundaries are revised rarely

_NAME_RE = re.compile(r'NAME="([^"]*)"')


async def check_near(lat: float, lon: float) -> dict | None:
    key = _cache.coord_key("coal_mining", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch(lat, lon)
    if result is not None:
        _cache.set(key, result)
    return result


async def _fetch(lat: float, lon: float) -> dict | None:
    d = 0.0005  # a minimal non-zero window - this is a point lookup, not an area render
    params = {
        "SERVICE": "WMS", "VERSION": "1.1.1", "REQUEST": "GetFeatureInfo",
        "LAYERS": LAYER, "QUERY_LAYERS": LAYER, "SRS": "EPSG:4326",
        "BBOX": f"{lon - d},{lat - d},{lon + d},{lat + d}",
        "WIDTH": 3, "HEIGHT": 3, "X": 1, "Y": 1,
        "INFO_FORMAT": "application/json", "FEATURE_COUNT": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(WMS_URL, params=params)
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    # Despite the requested INFO_FORMAT, this service always returns
    # its own Esri XML shape (<FIELDS NAME="..." .../>) rather than
    # real JSON - a light regex pull of the NAME attribute is simpler
    # and just as reliable as parsing it as XML.
    match = _NAME_RE.search(response.text)
    return {"present": bool(match), "area_name": match.group(1) if match else None}
