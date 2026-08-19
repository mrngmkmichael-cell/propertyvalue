"""Planning-constraint and environmental designations - is this point
inside a nationally protected/designated area? Most queried live via
ArcGIS FeatureServer point-in-polygon "intersects" queries against
Natural England's and Historic England's official open data services
- no key required, no need to mirror these large national polygon
datasets ourselves. Tree Preservation Orders and Article 4 Directions
(see _PLANNING_DATA_GOV_UK_DATASETS below) instead come from
planning.data.gov.uk's own /entity.json point-query API - a different
government aggregator, but the same "real query against an official
source, not a locked placeholder" principle, and its point-query
endpoint happens to support requesting several datasets in a single
call, so those two are fetched together rather than one query each.

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

PLANNING_DATA_GOV_UK_URL = "https://www.planning.data.gov.uk/entity.json"

# (key, label, group, planning.data.gov.uk dataset id)
_PLANNING_DATA_GOV_UK_DATASETS = [
    ("tpo", "Tree Preservation Order", "planning", "tree-preservation-zone"),
    ("article_4", "Article 4 Direction (removes some permitted development rights)",
     "planning", "article-4-direction-area"),
]

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


async def _query_planning_data_gov_uk(client: httpx.AsyncClient, lat: float, lon: float) -> dict[str, dict]:
    """One combined point-query for both datasets - the API accepts
    multiple `dataset` params and does the point-in-polygon filtering
    server-side, same trust boundary as the ArcGIS `intersects` layers
    above (not re-verified client-side).

    This endpoint is genuinely slow - measured 13-18s for a first-time
    (cold) query at a given coordinate regardless of dataset or result
    count, vs. ~0s for an exact repeat (their own edge cache). A
    generous timeout is used rather than a short one that would make
    this fail most of the time: the property page's own gather already
    tolerates an 8-13s cold load across ~28 other calls, cached for an
    hour afterwards, so this raises that ceiling rather than changing
    the shape of the tradeoff."""
    params = [("latitude", lat), ("longitude", lon), ("limit", 100), ("field", "dataset"), ("field", "name")]
    for _, _, _, dataset_id in _PLANNING_DATA_GOV_UK_DATASETS:
        params.append(("dataset", dataset_id))

    try:
        response = await client.get(PLANNING_DATA_GOV_UK_URL, params=params, timeout=20)
        response.raise_for_status()
    except httpx.HTTPError:
        return {key: {"present": None} for key, *_ in _PLANNING_DATA_GOV_UK_DATASETS}

    entities = response.json().get("entities", [])
    names_by_dataset: dict[str, set] = {}
    for e in entities:
        names_by_dataset.setdefault(e.get("dataset"), set()).add(e.get("name") or "Unnamed")

    out = {}
    for key, _, _, dataset_id in _PLANNING_DATA_GOV_UK_DATASETS:
        names = names_by_dataset.get(dataset_id)
        out[key] = {"present": True, "names": sorted(names)} if names else {"present": False}
    return out


async def check_all(lat: float, lon: float) -> dict:
    key = _cache.coord_key("designations", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_query_layer(client, url, field, lat, lon) for _, _, _, url, field in _LAYERS),
            _query_planning_data_gov_uk(client, lat, lon),
            return_exceptions=True,
        )

    *arcgis_results, planning_data_gov_uk_result = results
    if isinstance(planning_data_gov_uk_result, Exception):
        planning_data_gov_uk_result = {key: {"present": None} for key, *_ in _PLANNING_DATA_GOV_UK_DATASETS}

    out = {}
    for (key_name, label, group, _, _), result in zip(_LAYERS, arcgis_results):
        if isinstance(result, Exception):
            result = {"present": None}
        out[key_name] = {"label": label, "group": group, **result}
    for key_name, label, group, _ in _PLANNING_DATA_GOV_UK_DATASETS:
        out[key_name] = {"label": label, "group": group, **planning_data_gov_uk_result[key_name]}

    _cache.set(key, out)
    return out
