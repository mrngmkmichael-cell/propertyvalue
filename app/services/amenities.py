"""Local amenities (restaurants, supermarkets, hospitals, pharmacies,
pubs, wind turbines, solar farms) and the nearest train/tube station,
from OpenStreetMap's free Overpass API (no key required).

Schools are handled separately (app/services/schools_db.py), from
DfE/Ofsted data rather than OSM - see that module for why.
"""
import asyncio
import math
import re

import httpx

from app.services import _cache, bus_service, food_hygiene, health_services, nhs_places, rail_journey, routing, stations

# Independent public Overpass instances, tried with hedging (see
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
# Off since 14 Oct 2026, and to stay off. overpass-api.de now refuses
# any request that names this site ("UKPropertyInsight", "UKProperty",
# "PropertyValue") and the "curl/8.7.1" string sent after an earlier
# refusal, while a made-up client name is answered: a block aimed at
# us by the people who run it, not an outage. The site does not
# disguise itself to get round it. Of the other four mirrors, two time
# out, one is whitelist-only and one holds Swiss data only. Every
# report was still asking all of them and waiting up to 8 s on the slow
# two. With this off, the cards that read OpenStreetMap say "Data
# unavailable" at once, until official sources replace them.
OVERPASS_ENABLED = False
# Per-attempt timeout, bounding how long any single mirror can hold
# up the hedge chain.
OVERPASS_TIMEOUT_S = 8
# How long a mirror gets to answer before the next one is also asked.
# The whole point of hedging is that a healthy mirror answers well
# inside this, so the other four are never contacted at all.
# 1.5s rather than 2.5: after the flood fix this is the page's long
# pole, and from Render's IP range the primary is often the slow one, so
# the second mirror is worth asking a second earlier. A healthy mirror
# still answers inside this, so the common case is still one request.
OVERPASS_HEDGE_DELAY_S = 1.5

# Index into OVERPASS_ENDPOINTS of whichever mirror answered last.
# Public Overpass instances flap - one that 502s now is often fine an
# hour later and vice versa - so rather than hammering a known-bad
# primary on every request, start from whatever worked most recently.
# Module-level and best-effort: resets on restart, and a stale value
# costs one hedge delay, not a failure.
_preferred_endpoint_index = 0
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
    # Wider radius than the other categories - a turbine or solar farm
    # is a visual/noise amenity consideration from much further away
    # than, say, a restaurant.
    ("wind_turbine", '["power"="generator"]["generator:source"="wind"]', 5000),
    ("solar_farm", '["power"="plant"]["plant:source"="solar"]', 3000),
]
# The subset of AMENITY_QUERIES the extension's premium report actually
# displays (see main.py's essentials_detail) - gp/dentist/green_space/
# parking/ev_charging/wind_turbine/solar_farm are fetched for the main
# site's own dashboard but never shown there, so a lite fetch skips
# them entirely rather than paying their Overpass cost for nothing.
LITE_AMENITY_LABELS = {"restaurant", "supermarket", "pharmacy", "pub", "hospital"}
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


async def _try_endpoint(
    client: httpx.AsyncClient, endpoint: str, query: str
) -> tuple[str, list[dict]]:
    # Sending "curl/8.7.1" rather than something identifying us is
    # not a preference. Measured against every mirror: overpass-api.de
    # answers 406 and overpass.openstreetmap.fr answers 403 to ANY
    # other User-Agent - a bare product token, one with a contact
    # address, one with a URL, a Mozilla-compatible string, even
    # httpx's own default. They run an allowlist that admits generic
    # clients and refuses named ones, which is backwards from what
    # every usage policy asks for, but it is what they do. If that
    # ever changes, an identifying string belongs here.
    response = await client.post(
        endpoint, data={"data": query}, headers={"User-Agent": "curl/8.7.1"}
    )
    response.raise_for_status()
    data = response.json()
    if not _is_healthy_response(data):
        raise RuntimeError(f"{endpoint} returned an unhealthy response")
    return endpoint, data.get("elements", [])


async def _query_overpass(query: str) -> list[dict]:
    """Ask mirrors one at a time, overlapping only when one goes quiet.

    This used to fire all five concurrently and keep whichever answered
    first. That bounded latency, which was the goal, but it put every
    single lookup onto five separate volunteer-run servers and threw
    four of the answers away - five times the load actually needed, on
    donated infrastructure, with each operator seeing us as a constant
    client. That is how you get blocked, and being blocked by all five
    at once is a far worse outcome than a slow page.

    Hedging keeps the latency guarantee without the cost: ask one, and
    only bring in the next if the current one has not answered within
    OVERPASS_HEDGE_DELAY_S. A healthy mirror answers in well under
    that, so the common case is exactly one request. A mirror that
    fails fast (403, 502) doesn't even cost the delay - the next starts
    the moment it errors. Only a genuinely slow mirror causes overlap,
    which is precisely when a second opinion is worth asking for.
    """
    global _preferred_endpoint_index

    if not OVERPASS_ENABLED:
        raise RuntimeError("OpenStreetMap's Overpass service is not used: it refuses this site (14 Oct 2026)")
    start = _preferred_endpoint_index
    order = OVERPASS_ENDPOINTS[start:] + OVERPASS_ENDPOINTS[:start]

    pending: set = set()
    last_error: Exception | None = None

    def _harvest(done):
        """First successful result wins; remember which mirror gave it."""
        nonlocal last_error
        global _preferred_endpoint_index
        for task in done:
            try:
                endpoint, elements = task.result()
            except (httpx.HTTPError, RuntimeError, asyncio.CancelledError) as exc:
                last_error = exc if not isinstance(exc, asyncio.CancelledError) else last_error
                continue
            _preferred_endpoint_index = OVERPASS_ENDPOINTS.index(endpoint)
            return elements
        return None

    async with httpx.AsyncClient(timeout=OVERPASS_TIMEOUT_S) as client:
        try:
            for endpoint in order:
                pending.add(asyncio.ensure_future(_try_endpoint(client, endpoint, query)))
                done, pending = await asyncio.wait(
                    pending,
                    timeout=OVERPASS_HEDGE_DELAY_S,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                result = _harvest(done)
                if result is not None:
                    return result

            # Everything has been asked; wait out whatever is still running.
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                result = _harvest(done)
                if result is not None:
                    return result

            raise last_error or RuntimeError("no Overpass mirror returned a usable response")
        finally:
            for task in pending:
                task.cancel()


def cached_nearby(lat: float, lon: float) -> dict | None:
    """The full amenities result if it's already in memory, else None -
    never touches the network. The property report uses this to decide
    whether to render the amenities cards inline (cache hit) or hand
    them to a follow-up fetch after the page has loaded (cache miss):
    Overpass measured 7.5-10 s cold from Render, the whole rest of the
    report under 2 s, so waiting on it held every first view hostage."""
    return _cache.get(_cache.coord_key("amenities", lat, lon), CACHE_TTL_S)


async def nearby_amenities_and_station(lat: float, lon: float, lite: bool = False, country: str | None = None) -> dict:
    # "lite" is its own cache key, not just a fetch-time filter of the
    # full result - a lite response is missing entire categories, so
    # serving it back out to a full-fetch caller (or vice versa) would
    # silently show wrong/incomplete data instead of just being slower.
    key = _cache.coord_key("amenities_lite" if lite else "amenities", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_amenities_and_station(lat, lon, lite=lite, country=country)
    _cache.set(key, result)
    return result


# Where "Nearby essentials" and "Getting around" come from since 14 Oct
# 2026, when OpenStreetMap's Overpass service began refusing this site.
# Restaurants, pubs and supermarkets: the Food Standards Agency's
# register of food businesses. Pharmacies: NHS England's Organisation
# Data Service. Dentists and hospitals: the Care Quality Commission's
# register (nhs_places.py). GP surgeries: NHS England's practice list,
# the same table the Health Services card reads, England only. Stations: NaPTAN, the
# Department for Transport's register (stations.py), and the nearest bus
# stop from the Bus Open Data Service table the Bus Service card reads.
# The other categories were OpenStreetMap's alone. No official register
# lists them by location for this site to read, so they are named on
# the card as not covered rather than shown as a zero.
CATEGORIES = ("restaurant", "supermarket", "pharmacy", "pub", "hospital", "parking", "ev_charging",
              "gp", "dentist", "green_space", "wind_turbine", "solar_farm")
GP_RADIUS_M = 1609  # a mile, as the registers' other places are counted


async def _nothing():
    return None


async def _fetch_amenities_and_station(lat: float, lon: float, lite: bool = False, country: str | None = None) -> dict:
    tasks = [food_hygiene.places_near(lat, lon)]
    # NHS England's practice list covers England. With no nation given,
    # the CQC dentist list, which also covers England only, stands in.
    gp_covered = country == "England" if country else nhs_places.covers("dentist", lat, lon, None)
    if not lite:
        tasks += [asyncio.to_thread(health_services.near, lat, lon, GP_RADIUS_M) if gp_covered else _nothing(),
                  asyncio.to_thread(bus_service.stops_near, lat, lon, BUS_STOP_RADIUS_M)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    places = results[0]
    if isinstance(places, Exception):
        raise places  # the cards say "Data unavailable", and nothing wrong is cached
    gp = results[1] if not lite and not isinstance(results[1], Exception) else None
    bus = results[2] if not lite and not isinstance(results[2], Exception) else None

    categories: dict = {label: [] for label in CATEGORIES}
    totals, covered = {}, []
    for cat, found in places.items():
        categories[cat] = found["places"]
        totals[cat] = found["total"]
        covered.append(cat)
    for kind, found in nhs_places.near(lat, lon, country).items():
        categories[kind] = found["places"]
        totals[kind] = found["total"]
        covered.append(kind)
    if gp is not None:
        categories["gp"] = [{"name": p["name"], "distance_m": p["distance_m"], "lat": None, "lon": None}
                            for p in gp.get("practices", [])]
        totals["gp"] = gp.get("within", len(categories["gp"]))
        covered.append("gp")
    # Carried inside the categories so every path that already passes them
    # (the report, its follow-up fetch, the PDF, the area guide) has them.
    categories["_totals"] = totals
    categories["_covered"] = covered

    stations_by_mode = stations.near(lat, lon, STATION_RADIUS_M, STATION_LIST_LIMIT)
    stations_by_mode["bus"] = []
    if bus and bus.get("stops"):
        stop = bus["stops"][0]
        stations_by_mode["bus"] = [{"id": stop["atco_code"], "type": "naptan", "name": stop["name"],
                                    "network": "", "distance_m": stop["distance_m"]}]
    # The same dict objects in both, as before: walking times and city
    # journeys added to the nearest show in the list too.
    nearest_by_mode = {mode: found[0] for mode, found in stations_by_mode.items() if found}

    # Real walking-route distance for the nearest station of each mode
    # a person plausibly walks to (not bus - those are already within
    # BUS_STOP_RADIUS_M=800m, where straight-line rarely misleads).
    # distance_m (straight-line) is left untouched as the fallback the
    # template uses when this isn't configured or the lookup fails.
    walkable_modes = [m for m in ("rail", "tube", "tram") if m in nearest_by_mode]
    if walkable_modes and routing.is_configured():
        walks = await asyncio.gather(*(
            routing.walking_distance(lat, lon, nearest_by_mode[mode]["lat"], nearest_by_mode[mode]["lon"])
            for mode in walkable_modes
        ))
        for mode, walk in zip(walkable_modes, walks):
            if walk is not None:
                nearest_by_mode[mode]["walking_distance_m"] = round(walk["distance_m"])
                nearest_by_mode[mode]["walking_duration_min"] = round(walk["duration_min"])

    if "rail" in nearest_by_mode and nearest_by_mode["rail"].get("crs"):
        journeys = await rail_journey.fastest_to_cities(nearest_by_mode["rail"]["crs"])
        if journeys is not None:
            # An empty list is meaningful (queried successfully, no
            # direct city service found) and distinct from the key
            # being absent entirely (not configured, or the lookup
            # itself failed) - the template shows different messaging
            # for each case.
            nearest_by_mode["rail"]["city_journeys"] = journeys

    return {"categories": categories, "stations": nearest_by_mode, "stations_list": stations_by_mode}
