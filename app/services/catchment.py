"""School catchment-area boundaries - is this point inside a
published catchment/priority-admission zone for a specific school?

There is no national UK catchment-area dataset, and largely can't be
one: most English school admissions (especially secondaries, mostly
academies now) don't have a fixed catchment at all - places are
allocated by priority criteria (looked-after children, siblings,
faith criteria) with home-to-school distance only used as a
tie-breaker within each priority band, so the effective "catchment"
shifts every year with how many people applied. Commercial sites that
show a catchment area are showing a *modelled estimate* from historic
admission distances (itself not published anywhere reusable - even
dedicated open-data projects investigating this concluded no council
publishes real distance-offered data, and had to statistically model
it), usually behind a paywall - not a real boundary.

This module only covers the minority of (mostly Scottish, plus a
handful of English) local authorities that buck that trend and
publish real fixed catchment-area polygons as open GIS data - queried
live via ArcGIS FeatureServer/MapServer point-in-polygon "intersects"
queries, the same pattern as designations.py. Everywhere else, this
correctly reports no result rather than guessing - matching the
project's standing rule against faking data no free source actually
has.
"""
import asyncio

import httpx

from app.services import _cache

CACHE_TTL_S = 86400 * 30  # catchment boundaries are reissued at most annually

# (local authority, phase, query URL incl. layer index, school-name field)
_SOURCES = [
    ("Sheffield", "Primary",
     "https://sheffieldcitycouncil.cloud.esriuk.com/server/rest/services/AGOL/OpenData/FeatureServer/1", "catchment"),
    ("Sheffield", "Secondary",
     "https://sheffieldcitycouncil.cloud.esriuk.com/server/rest/services/AGOL/OpenData/FeatureServer/3", "catchment"),
    ("Hampshire", "Primary/Junior",
     "https://services-eu1.arcgis.com/JZryykSnmiY7YI6X/arcgis/rest/services/School_Catchments_7_10/FeatureServer/0",
     "School"),
    ("Hampshire", "Secondary",
     "https://services-eu1.arcgis.com/JZryykSnmiY7YI6X/arcgis/rest/services/School_Catchments_11_16/FeatureServer/0",
     "School"),
    ("City of York", "Primary",
     "https://maps.york.gov.uk/arcgis/rest/services/Public/EducationLearning/MapServer/3", "SchName"),
    ("City of York", "Secondary",
     "https://maps.york.gov.uk/arcgis/rest/services/Public/EducationLearning/MapServer/4", "Schname"),
    ("City of Edinburgh", "Secondary (non-denominational)",
     "https://edinburghcouncilmaps.info/arcgis/rest/services/AdminBoundaries/MiscBoundaries/MapServer/6", "SCHOOL"),
    ("City of Edinburgh", "Secondary (Roman Catholic)",
     "https://edinburghcouncilmaps.info/arcgis/rest/services/AdminBoundaries/MiscBoundaries/MapServer/7",
     "SCH_NAME"),
    ("Aberdeen City", "Primary (non-denominational)",
     "https://services5.arcgis.com/0sktPVp3t1LvXc9z/arcgis/rest/services/Primary_School_Catchments/FeatureServer/58",
     "NAME"),
    ("North Lanarkshire", "Primary (non-denominational)",
     "https://services-eu1.arcgis.com/9edRUxcMgH07BEka/arcgis/rest/services/"
     "Non_Denominational_Primary_Catchments_View/FeatureServer/0", "name"),
    ("North Lanarkshire", "Primary (denominational)",
     "https://services-eu1.arcgis.com/9edRUxcMgH07BEka/arcgis/rest/services/"
     "Denominational_Primary_Catchments_View/FeatureServer/0", "name"),
    ("Glasgow City", "Primary (non-denominational)",
     "https://utility.arcgis.com/usrsvcs/servers/2bfa782d5da84302bf15219e19a05112/rest/services/"
     "OPEN_DATA/Schools_Catchments_Open/MapServer/6", "NAME"),
    ("Glasgow City", "Secondary (non-denominational)",
     "https://utility.arcgis.com/usrsvcs/servers/2bfa782d5da84302bf15219e19a05112/rest/services/"
     "OPEN_DATA/Schools_Catchments_Open/MapServer/7", "NAME"),
    ("Glasgow City", "Primary (Roman Catholic)",
     "https://utility.arcgis.com/usrsvcs/servers/2bfa782d5da84302bf15219e19a05112/rest/services/"
     "OPEN_DATA/Schools_Catchments_Open/MapServer/8", "NAME"),
    ("Glasgow City", "Secondary (Roman Catholic)",
     "https://utility.arcgis.com/usrsvcs/servers/2bfa782d5da84302bf15219e19a05112/rest/services/"
     "OPEN_DATA/Schools_Catchments_Open/MapServer/9", "NAME"),
    ("Glasgow City", "Primary (Gaelic)",
     "https://utility.arcgis.com/usrsvcs/servers/2bfa782d5da84302bf15219e19a05112/rest/services/"
     "OPEN_DATA/Schools_Catchments_Open/MapServer/5", "NAME"),
    ("Dundee City", "Primary (non-denominational)",
     "https://services.arcgis.com/GlZ1P6ksdiXNYhvC/arcgis/rest/services/"
     "School_Catchments_2022_View/FeatureServer/0", "School"),
    ("Dundee City", "Secondary (Roman Catholic)",
     "https://services.arcgis.com/GlZ1P6ksdiXNYhvC/arcgis/rest/services/"
     "School_Catchments_2022_View/FeatureServer/1", "School"),
    ("Dundee City", "Secondary (non-denominational)",
     "https://services.arcgis.com/GlZ1P6ksdiXNYhvC/arcgis/rest/services/"
     "School_Catchments_2022_View/FeatureServer/2", "School"),
    ("Dundee City", "Primary (Roman Catholic)",
     "https://services.arcgis.com/GlZ1P6ksdiXNYhvC/arcgis/rest/services/"
     "School_Catchments_2022_View/FeatureServer/3", "School"),
]


# "Stirling" was a source until 1 Oct 2026. Its layer is Dundee City's
# school locations, as points (extent 56.46 to 56.50 N, 2.85 to 3.04 W;
# name "Schools", geometry esriGeometryPoint), so a point-in-polygon query
# against it could never answer for anywhere, and the report listed
# Stirling as covered. Removed rather than re-pointed: no Stirling
# catchment layer has been found.

# Where each authority's layers reach, as each layer reported its own
# extent in WGS84 on 1 Oct 2026 (returnExtentOnly), widened by 0.02
# degrees, about 2 km, so a boundary redrawn outwards is not missed.
# (lat_min, lat_max, lon_min, lon_max). Until then every report asked all
# 21 layers, so every address in the country waited on the slowest
# council server it had nothing to do with: EX4 4QJ in Exeter spent 11.2
# s of a 13.2 s cold report here while 4.5 s was the next slowest. A
# point outside every box now asks nothing and answers at once.
_PAD = 0.02
_EXTENTS = {
    "Sheffield": (53.3045, 53.5031, -1.8015, -1.3246),
    "Hampshire": (50.7060, 51.3839, -1.9573, -0.7293),
    "City of York": (53.8388, 54.0755, -1.3152, -0.8754),
    "City of Edinburgh": (55.8082, 56.0068, -3.5309, -3.0736),
    "Aberdeen City": (57.0762, 57.2406, -2.3453, -2.0409),
    "North Lanarkshire": (55.7303, 56.0364, -4.2010, -3.7057),
    "Glasgow City": (55.7813, 55.9810, -4.4003, -4.0704),
    "Dundee City": (56.4430, 56.5048, -3.0987, -2.8317),
}


def _authority_reaches(authority: str, lat: float, lon: float) -> bool:
    lat_min, lat_max, lon_min, lon_max = _EXTENTS[authority]
    return (lat_min - _PAD <= lat <= lat_max + _PAD) and (lon_min - _PAD <= lon <= lon_max + _PAD)


def covered_authorities() -> list[str]:
    """Deduplicated, ordered list of local authorities in _SOURCES -
    shown to users when a search falls outside all of them, so "not
    covered" reads as "your council doesn't publish this" rather than
    looking like a broken feature."""
    seen = []
    for authority, _, _, _ in _SOURCES:
        if authority not in seen:
            seen.append(authority)
    return seen


async def _query_source(client: httpx.AsyncClient, url: str, name_field: str, lat: float, lon: float) -> list[dict]:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": name_field,
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "json",
    }
    response = await client.get(f"{url}/query", params=params, timeout=10)
    response.raise_for_status()
    features = response.json().get("features", [])
    results = []
    seen = set()
    for f in features:
        name = f["attributes"].get(name_field)
        if not name or name in seen:
            continue
        seen.add(name)
        results.append({"school_name": name, "rings": f.get("geometry", {}).get("rings")})
    return sorted(results, key=lambda r: r["school_name"])


async def catchments_for(lat: float, lon: float) -> list[dict]:
    """Each match includes "rings" - the catchment polygon's own
    coordinates (GeoJSON-style [lon, lat] pairs, ready to hand
    straight to Leaflet's L.geoJSON) - a real boundary shape, not just
    a name, for the minority of authorities in _SOURCES that publish
    one. "rings" is None on the rare feature that has attributes but
    no geometry returned."""
    key = _cache.coord_key("catchment_v2", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached

    sources = [source for source in _SOURCES if _authority_reaches(source[0], lat, lon)]
    if not sources:
        _cache.set(key, [])
        return []

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_query_source(client, url, field, lat, lon) for _, _, url, field in sources),
            return_exceptions=True,
        )

    # One council's ArcGIS endpoint being down is routine and handled
    # per-source below (skip it, keep the other ~20 authorities'
    # results) - but if EVERY source failed at once, that's a real
    # outage (e.g. a shared network issue), not "no council here
    # publishes a catchment", so raise rather than cache an empty
    # result as if it were a genuine "not covered" answer.
    if results and all(isinstance(r, Exception) for r in results):
        raise results[0]

    matches = []
    for (authority, phase, _, _), result in zip(sources, results):
        if isinstance(result, Exception) or not result:
            continue
        for row in result:
            matches.append({"authority": authority, "phase": phase, "school_name": row["school_name"], "rings": row["rings"]})

    _cache.set(key, matches)
    return matches
