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

from app.services import _cache

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
]
STATION_RADIUS_M = 3000
BUS_STOP_RADIUS_M = 800


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
    query = f"[out:json][timeout:20];({clauses});out center tags;"
    elements = await _query_overpass(query)

    categories = {label: [] for label, _, _ in AMENITY_QUERIES}
    stations = {"rail": [], "tube": [], "bus": []}

    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("ref") or "Unnamed"
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
            })
        elif amenity == "restaurant":
            categories["restaurant"].append({"name": name, "distance_m": distance_m})
        elif shop == "supermarket":
            categories["supermarket"].append({"name": name, "distance_m": distance_m})
        elif amenity == "pharmacy":
            categories["pharmacy"].append({"name": name, "distance_m": distance_m})
        elif amenity == "pub":
            categories["pub"].append({"name": name, "distance_m": distance_m})
        elif amenity == "hospital":
            categories["hospital"].append({"name": name, "distance_m": distance_m})

    for items in categories.values():
        items.sort(key=lambda i: i["distance_m"])

    nearest_by_mode = {}
    for mode, candidates in stations.items():
        if not candidates:
            continue
        candidates.sort(key=lambda s: s["distance_m"])
        nearest_by_mode[mode] = candidates[0]

    # Line lookups are a second Overpass round-trip each - run the
    # (at most two: rail, tube) concurrently rather than one after
    # another.
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

    return {"categories": categories, "stations": nearest_by_mode}


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
