"""Planning-constraint and environmental designations - is this point
inside a nationally protected/designated area? All queried live via
ArcGIS FeatureServer point-in-polygon "intersects" queries against
Natural England's and Historic England's official open data services
- no key required, no need to mirror these large national polygon
datasets ourselves.

This mirrors the checklist categories a paid tool like Propbar shows
behind a paywall, except every result here is a real query against
the same government source they'd be using, not a locked placeholder.

Not covered: Brownfield Land. Unlike everything below, this isn't
published as a single national GIS layer - each of England's ~300
local planning authorities self-publishes its own Brownfield Land
Register in a common schema, with no central live-queryable service
(the aggregations that exist are third-party mirrors of uncertain
freshness). Matching the project's standing rule against faking or
guessing at data, this one's left out rather than approximated from
an unreliable source - same reasoning as the declined Council Tax
band and full Ofsted history features.
"""
import asyncio

import httpx

from app.services import _cache

CACHE_TTL_S = 86400 * 30  # designated-area boundaries change rarely

NE_BASE = "https://services.arcgis.com/JJzESW51TqeY9uat/arcgis/rest/services"
HE_BASE = (
    "https://services-eu1.arcgis.com/ZOdPfBS3aqqDYPUQ/arcgis/rest/services/"
    "National_Heritage_List_for_England_NHLE_v02_VIEW/FeatureServer"
)

# (key, label, group, query URL, name field)
_LAYERS = [
    ("sssi", "Site of Special Scientific Interest", "environmental",
     f"{NE_BASE}/SSSI_England/FeatureServer/0", "NAME"),
    ("sac", "Special Area of Conservation", "environmental",
     f"{NE_BASE}/Special_Areas_of_Conservation_England/FeatureServer/0", "SAC_NAME"),
    ("spa", "Special Protection Area", "environmental",
     f"{NE_BASE}/Special_Protection_Areas_England/FeatureServer/0", "SPA_NAME"),
    ("ramsar", "Ramsar Wetland", "environmental",
     f"{NE_BASE}/Ramsar_England/FeatureServer/0", "NAME"),
    ("priority_habitat", "Priority Habitat", "environmental",
     f"{NE_BASE}/Priority_Habitats_Inventory_England/FeatureServer/0", "MainHabs"),
    ("ancient_woodland", "Ancient Woodland", "environmental",
     f"{NE_BASE}/Ancient_Woodland_Revised_England/FeatureServer/0", "THEMENAME"),
    ("national_park", "National Park", "environmental",
     f"{NE_BASE}/National_Parks_England/FeatureServer/0", "NAME"),
    ("aonb", "National Landscape (AONB)", "environmental",
     f"{NE_BASE}/Areas_of_Outstanding_Natural_Beauty_England/FeatureServer/0", "NAME"),
    ("built_up_area", "Built-up Area", "planning",
     "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/main_ONS_BUA_2024_EW_V2/FeatureServer/0",
     "BUA24NM"),
    ("scheduled_monument", "Scheduled Monument", "planning", f"{HE_BASE}/6", "Name"),
    ("battlefield", "Registered Battlefield", "planning", f"{HE_BASE}/8", "Name"),
    ("protected_wreck", "Protected Wreck Site", "planning", f"{HE_BASE}/9", "Name"),
    ("world_heritage_site", "World Heritage Site", "planning", f"{HE_BASE}/10", "Name"),
]


async def _query_layer(client: httpx.AsyncClient, url: str, name_field: str, lat: float, lon: float) -> dict:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": name_field,
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        response = await client.get(f"{url}/query", params=params, timeout=10)
        response.raise_for_status()
    except httpx.HTTPError:
        return {"present": None}
    features = response.json().get("features", [])
    if not features:
        return {"present": False}
    names = sorted({
        f["attributes"].get(name_field) for f in features if f["attributes"].get(name_field)
    })
    return {"present": True, "names": names}


async def check_all(lat: float, lon: float) -> dict:
    key = _cache.coord_key("designations", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_query_layer(client, url, field, lat, lon) for _, _, _, url, field in _LAYERS),
            return_exceptions=True,
        )

    out = {}
    for (key_name, label, group, _, _), result in zip(_LAYERS, results):
        if isinstance(result, Exception):
            result = {"present": None}
        out[key_name] = {"label": label, "group": group, **result}

    _cache.set(key, out)
    return out
