"""Local amenities (restaurants, supermarkets, hospitals, pharmacies,
pubs) and the nearest train/tube station, from OpenStreetMap's free
Overpass API (no key required).

Schools are handled separately (app/services/schools_db.py), from
DfE/Ofsted data rather than OSM - see that module for why.
"""
import asyncio
import math
import re

import httpx

from app.services import _cache, rail_journey

# Independent public Overpass instances, raced concurrently (see
# _query_overpass). The primary is known to reject some
# hosting-provider IP ranges outright, and the shared public
# instances occasionally go down together under load - observed
# live, more than once, more than two of them degraded at the same
# time (504s, unresponsive, or a "200 OK" with a broken/empty
# database - see _is_healthy_response). Five independent operators
# makes a fully correlated outage unlikely.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
# Per-attempt timeout - since all mirrors are raced concurrently now
# (not tried one at a time), this bounds total worst-case latency
# rather than accumulating across mirrors.
OVERPASS_TIMEOUT_S = 8
CACHE_TTL_S = 3600  # OSM POI data doesn't change fast enough to need per-request freshness

# (label, overpass tag filter, search radius in metres)
AMENITY_QUERIES = [
    ("restaurant", '["amenity"="restaurant"]', 1000),
    ("supermarket", '["shop"="supermarket"]', 1000),
    ("pharmacy", '["amenity"="pharmacy"]', 1000),
    ("pub", '["amenity"="pub"]', 1000),
    ("hospital", '["amenity"="hospital"]', 3000),
    ("parking", '["amenity"="parking"]', 800),
    ("ev_charging", '["amenity"="charging_station"]', 2000),
    ("gp", '["amenity"="doctors"]', 2000),
    ("dentist", '["amenity"="dentist"]', 2000),
    ("green_space", '["leisure"~"park|recreation_ground|nature_reserve"]', 1500),
]
STATION_RADIUS_M = 3000
BUS_STOP_RADIUS_M = 800
STATION_LIST_LIMIT = 5
ROAD_LOOKUP_RADIUS_M = 800  # matches the parking search radius - just enough to name any unnamed car park


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _element_latlon(el: dict):
    if "lat" in el:
        return el["lat"], el["lon"]
    center = el.get("center")
    return (center["lat"], center["lon"]) if center else (None, None)


def _nearest_road_name(lat: float, lon: float, roads: list[dict]) -> str | None:
    """Most OSM car parks have no name of their own - rather than
    show a bare "Unnamed", fall back to whichever named road passes
    closest to the car park's own location. Checks distance to every
    node along each road's geometry rather than a proper line-segment
    projection - road nodes are dense enough in practice (well under
    the radius searched here) that this is accurate enough without
    the extra complexity."""
    best_name, best_dist = None, float("inf")
    for road in roads:
        for pt in road["geometry"]:
            d = _haversine_m(lat, lon, pt["lat"], pt["lon"])
            if d < best_dist:
                best_dist, best_name = d, road["name"]
    return best_name


def _is_healthy_response(data: dict) -> bool:
    # A mirror with a broken/uninitialized database can return a
    # syntactically valid 200 OK with an empty result set - no HTTP
    # error, so the usual try/except doesn't catch it. Its timestamp
    # gives it away: a healthy instance reports a real ISO date
    # ("2026-08-14T23:26:46Z"); a broken one returned "116437".
    timestamp = data.get("osm3s", {}).get("timestamp_osm_base", "")
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}", timestamp))


async def _try_endpoint(client: httpx.AsyncClient, endpoint: str, query: str) -> list[dict]:
    response = await client.post(endpoint, data={"data": query}, headers={"User-Agent": "curl/8.7.1"})
    response.raise_for_status()
    data = response.json()
    if not _is_healthy_response(data):
        raise RuntimeError(f"{endpoint} returned an unhealthy response")
    return data.get("elements", [])


async def _query_overpass(query: str) -> list[dict]:
    # Race all mirrors concurrently rather than trying them one at a
    # time - a sequential fallback means a bad run of dead/slow
    # mirrors adds their timeouts one after another (observed: up to
    # ~26s cold). Racing bounds worst-case latency to roughly the
    # single timeout window instead of the sum of every mirror tried.
    async with httpx.AsyncClient(timeout=OVERPASS_TIMEOUT_S) as client:
        tasks = [asyncio.ensure_future(_try_endpoint(client, ep, query)) for ep in OVERPASS_ENDPOINTS]
        last_error = None
        try:
            for coro in asyncio.as_completed(tasks):
                try:
                    return await coro
                except (httpx.HTTPError, RuntimeError) as exc:
                    last_error = exc
            raise last_error
        finally:
            for t in tasks:
                t.cancel()


async def nearby_amenities_and_station(lat: float, lon: float) -> dict:
    key = _cache.coord_key("amenities", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_amenities_and_station(lat, lon)
    _cache.set(key, result)
    return result


def _crs_code(ref: str | None) -> str | None:
    """OSM tags National Rail stations' 3-letter CRS code as
    `ref:crs`, but the tag is free text - only trust it when it
    actually looks like one, rather than passing junk through to the
    LDBWS API."""
    if ref and re.match(r"^[A-Z]{3}$", ref.strip()):
        return ref.strip()
    return None


def _station_mode(tags: dict) -> str:
    network = tags.get("network", "").lower()
    if tags.get("station") == "subway" or "underground" in network or "tube" in network:
        return "tube"
    return "rail"


async def _fetch_amenities_and_station(lat: float, lon: float) -> dict:
    clauses = "".join(
        f'nwr{tag}(around:{radius},{lat},{lon});' for _, tag, radius in AMENITY_QUERIES
    )
    clauses += (
        f'nwr["railway"~"station|halt"][!"disused:railway"](around:{STATION_RADIUS_M},{lat},{lon});'
        f'nwr["station"="subway"][!"disused:railway"](around:{STATION_RADIUS_M},{lat},{lon});'
        f'nwr["highway"="bus_stop"](around:{BUS_STOP_RADIUS_M},{lat},{lon});'
    )
    query = (
        f"[out:json][timeout:20];({clauses});out center tags;"
        f'way["highway"]["name"](around:{ROAD_LOOKUP_RADIUS_M},{lat},{lon});out geom;'
    )
    elements = await _query_overpass(query)

    categories = {label: [] for label, _, _ in AMENITY_QUERIES}
    stations = {"rail": [], "tube": [], "bus": []}
    roads = [
        {"name": el["tags"]["name"], "geometry": el["geometry"]}
        for el in elements
        if "geometry" in el and el.get("tags", {}).get("highway") and el.get("tags", {}).get("name")
    ]
    unnamed_entries = []  # (entry dict, its own lat/lon) - road-name lookup happens after the main loop

    for el in elements:
        if "geometry" in el:
            continue  # a road fetched only for the name-lookup above, not an amenity/station
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("ref") or tags.get("addr:street") or "Unnamed"
        el_lat, el_lon = _element_latlon(el)
        if el_lat is None:
            continue
        distance_m = round(_haversine_m(lat, lon, el_lat, el_lon))

        amenity = tags.get("amenity")
        shop = tags.get("shop")
        is_station = bool(
            re.match(r"station|halt", tags.get("railway", "")) or tags.get("station") == "subway"
        )

        if tags.get("highway") == "bus_stop":
            stations["bus"].append({
                "id": el["id"], "type": el["type"], "name": name,
                "network": "", "distance_m": distance_m,
            })
        elif is_station:
            mode = _station_mode(tags)
            stations[mode].append({
                "id": el["id"], "type": el["type"], "name": name,
                "network": tags.get("network", ""), "distance_m": distance_m,
                "crs": _crs_code(tags.get("ref:crs")) if mode == "rail" else None,
            })
        elif amenity == "restaurant":
            categories["restaurant"].append({"name": name, "distance_m": distance_m, "lat": el_lat, "lon": el_lon})
        elif shop == "supermarket":
            categories["supermarket"].append({"name": name, "distance_m": distance_m, "lat": el_lat, "lon": el_lon})
        elif amenity == "pharmacy":
            categories["pharmacy"].append({"name": name, "distance_m": distance_m, "lat": el_lat, "lon": el_lon})
        elif amenity == "pub":
            categories["pub"].append({"name": name, "distance_m": distance_m, "lat": el_lat, "lon": el_lon})
        elif amenity == "hospital":
            categories["hospital"].append({"name": name, "distance_m": distance_m, "lat": el_lat, "lon": el_lon})
        elif amenity == "parking":
            entry = {
                "name": name, "distance_m": distance_m, "lat": el_lat, "lon": el_lon,
                "fee": tags.get("fee", ""), "type": tags.get("parking", ""),
            }
            categories["parking"].append(entry)
            if name == "Unnamed":
                unnamed_entries.append((entry, el_lat, el_lon, "Parking off {}", "Unnamed car park"))
        elif amenity == "charging_station":
            connector_tags = {
                "socket:type2": "Type 2", "socket:type2_combo": "CCS",
                "socket:chademo": "CHAdeMO", "socket:tesla_standard": "Tesla",
            }
            categories["ev_charging"].append({
                "name": name, "distance_m": distance_m, "lat": el_lat, "lon": el_lon,
                "connectors": [label for key, label in connector_tags.items() if tags.get(key)],
            })
        elif amenity == "doctors":
            categories["gp"].append({"name": name, "distance_m": distance_m, "lat": el_lat, "lon": el_lon})
        elif amenity == "dentist":
            categories["dentist"].append({"name": name, "distance_m": distance_m, "lat": el_lat, "lon": el_lon})
        elif tags.get("leisure") in ("park", "recreation_ground", "nature_reserve"):
            entry = {
                "name": name, "distance_m": distance_m, "lat": el_lat, "lon": el_lon,
                "type": tags.get("leisure"),
            }
            categories["green_space"].append(entry)
            if name == "Unnamed":
                unnamed_entries.append((entry, el_lat, el_lon, "Green space off {}", "Unnamed green space"))

    for entry, p_lat, p_lon, template, fallback in unnamed_entries:
        road_name = _nearest_road_name(p_lat, p_lon, roads)
        entry["name"] = template.format(road_name) if road_name else fallback

    for items in categories.values():
        items.sort(key=lambda i: i["distance_m"])

    for candidates in stations.values():
        candidates.sort(key=lambda s: s["distance_m"])

    nearest_by_mode = {mode: candidates[0] for mode, candidates in stations.items() if candidates}

    # Line lookups are a second Overpass round-trip each - only done
    # for the single nearest rail/tube station (as before), not every
    # station in the expanded list, to keep the extra API calls
    # bounded regardless of how many stations happen to be nearby.
    line_lookup_modes = [m for m in ("rail", "tube") if m in nearest_by_mode]
    if line_lookup_modes:
        line_results = await asyncio.gather(
            *(
                _station_lines(nearest_by_mode[m]["type"], nearest_by_mode[m]["id"])
                for m in line_lookup_modes
            ),
            return_exceptions=True,
        )
        for mode, lines in zip(line_lookup_modes, line_results):
            nearest_by_mode[mode]["lines"] = [] if isinstance(lines, Exception) else lines

    if "rail" in nearest_by_mode and nearest_by_mode["rail"].get("crs"):
        journeys = await rail_journey.fastest_to_cities(nearest_by_mode["rail"]["crs"])
        if journeys is not None:
            # An empty list is meaningful (queried successfully, no
            # direct city service found) and distinct from the key
            # being absent entirely (not configured, or the lookup
            # itself failed) - the template shows different messaging
            # for each case.
            nearest_by_mode["rail"]["city_journeys"] = journeys

    stations_by_mode = {
        "rail": stations["rail"][:STATION_LIST_LIMIT],
        "tube": stations["tube"][:STATION_LIST_LIMIT],
        "bus": stations["bus"][:1],
    }

    return {"categories": categories, "stations": nearest_by_mode, "stations_list": stations_by_mode}


async def _station_lines(el_type: str, el_id: int) -> list[str]:
    """Line names serving a station, via public_transport=stop_area
    relations referencing it (well-tagged for London Underground;
    often unavailable for National Rail stations - that's fine, we
    just show fewer details rather than guessing."""
    type_letter = {"node": "n", "way": "w", "relation": "r"}[el_type]
    query = (
        f"[out:json][timeout:20];"
        f"{el_type}({el_id});"
        f'rel(b{type_letter})["public_transport"="stop_area"];'
        f"out tags;"
    )
    elements = await _query_overpass(query)

    lines = []
    for el in elements:
        name = el.get("tags", {}).get("name", "")
        match = re.search(r"\(([^)]+)\)\s*$", name)
        if match:
            lines.append(match.group(1))
    return lines
