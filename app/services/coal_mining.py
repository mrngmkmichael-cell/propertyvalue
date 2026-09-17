"""Coal Mining Reporting Area check - whether a property sits inside
the known extent of coal mining activity, the same area a solicitor's
CON29M coal mining search flags as requiring a report. Published free
under the Open Government Licence by the Mining Remediation Authority
(formerly the Coal Authority) as Coalfield Consultation Areas, the
dataset its own record says "is used to determine whether a coal
mining report is required for property transactions".

17 Sep 2026: the BGS-hosted WMS this used to query
(map.bgs.ac.uk/arcgis/services/CoalAuthority/...) began returning 404,
so the check answered "could not be checked" for every address, and
nothing said so, because scripts/check_sources.py had been refusing to
run since 7 Sep over a stale source list. Both are fixed together.

The Authority's own ArcGIS Online organisation (Qn4lKcPDVHNivEyr,
portal name "Mining Remediation Authority") publishes the layer with a
real Query capability, so this is a point-in-polygon lookup rather than
the old pixel probe. Verified against 15 coalfield towns and 10 places
outside one, and cross-checked against the Authority's separate
development high and low risk layers, which agreed at every point.

No coalfield name is shown. The polygons in this service carry none,
and the names in the Authority's published GeoPackage are internal
labels rather than publication strings: the whole Yorkshire coalfield
is labelled NOTTS, Leicestershire is labelled WARWICK, Greater
Manchester is LANCS, and one label is simply SCOTLAND. Printing those
beside a home would read as a mistake to anyone who lives there, and
inventing better ones would be modelling a label onto real geometry.
What a buyer acts on is whether a coal mining search is needed, and
"in a Coal Mining Reporting Area" carries that on its own.
"""
import json

import httpx

from app.services import _cache

QUERY_URL = (
    "https://services-eu1.arcgis.com/Qn4lKcPDVHNivEyr/arcgis/rest/services/"
    "Coalfield_Consultation_Areas/FeatureServer/0/query"
)
CACHE_TTL_S = 86400 * 30  # coalfield reporting area boundaries are revised rarely


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
    params = {
        "geometry": json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,  # the layer itself is EPSG:27700; the service reprojects
        "spatialRel": "esriSpatialRelIntersects",
        "returnCountOnly": "true",
        "f": "json",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(QUERY_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None

    # ArcGIS answers a bad query with HTTP 200 and an error object, which
    # must never be read as "no coal here": that is the difference between
    # not in a reporting area and not checked at all.
    if not isinstance(payload, dict) or "error" in payload:
        return None
    count = payload.get("count")
    if not isinstance(count, int):
        return None
    # area_name is kept, always None, because the report and the PDF only
    # add a name in brackets when there is one. See the note above.
    return {"present": count > 0, "area_name": None}
