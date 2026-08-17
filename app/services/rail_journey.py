"""Fastest scheduled train journey from the nearest rail station to
the UK's official cities, via National Rail's Live Departure Board Web
Service (LDBWS), subscribed to through the Rail Data Marketplace.
Requires a free API key (see .env.example) - returns None rather than
raising when no key is configured, same pattern as EPC/Google Places.
"""
import os

import httpx

# The swagger describes realtime.nationalrail.co.uk as the origin, but
# requests actually have to go through the Rail Data Marketplace
# gateway for this specific product subscription - hitting the origin
# host directly returns 401 even with a valid key.
API_BASE = "https://api1.raildata.org.uk/1010-live-departure-board-dep1_2/LDBWS/api/20220120"

NUM_ROWS = 10  # how many upcoming departures to scan for a city-bound service
TIME_WINDOW_MIN = 120  # matches the API's own default
TOP_N = 2  # how many distinct cities to report

# CRS codes for the UK's officially-designated cities (city status, not
# just "big town") that sit on the Great Britain National Rail network.
# A few list entries are deliberately left out because LDBWS can never
# reach them:
#   - Northern Ireland cities (Armagh, Bangor, Belfast, Lisburn,
#     Londonderry, Newry) - a separate network (NI Railways/Translink),
#     not part of GB National Rail, so no CRS code exists for LDBWS to
#     use.
#   - Cities with no railway station at all: Ripon, Wells, St Asaph,
#     St Davids.
#   - Westminster - part of central London, not a distinct rail
#     destination from any of the London termini already listed.
# Multiple stations for one city (e.g. Manchester, Glasgow, London) all
# map to the same city name, so results are deduplicated by city
# further down, not by station.
CITY_STATIONS = {
    # England
    "BTH": "Bath", "BHM": "Birmingham", "BDI": "Bradford", "BDQ": "Bradford",
    "BTN": "Brighton & Hove", "BRI": "Bristol", "CBG": "Cambridge",
    "CBE": "Canterbury", "CBW": "Canterbury", "CAR": "Carlisle", "CHM": "Chelmsford",
    "CTR": "Chester", "CCH": "Chichester", "COL": "Colchester", "COV": "Coventry",
    "DBY": "Derby", "DON": "Doncaster", "DHM": "Durham", "ELY": "Ely",
    "EXD": "Exeter", "GCR": "Gloucester", "HFD": "Hereford", "HUL": "Kingston-upon-Hull",
    "LAN": "Lancaster", "LDS": "Leeds", "LEI": "Leicester",
    "LIC": "Lichfield", "LTV": "Lichfield", "LCN": "Lincoln", "LIV": "Liverpool",
    "PAD": "London", "VIC": "London", "WAT": "London", "KGX": "London", "EUS": "London",
    "LST": "London", "CHX": "London", "STP": "London", "MOG": "London", "FST": "London",
    "CST": "London", "BFR": "London", "MYB": "London", "LBG": "London",
    "MAN": "Manchester", "MCV": "Manchester", "MKC": "Milton Keynes", "NCL": "Newcastle-upon-Tyne",
    "NRW": "Norwich", "NOT": "Nottingham", "OXF": "Oxford", "PBO": "Peterborough",
    "PLY": "Plymouth", "PMS": "Portsmouth", "PMH": "Portsmouth", "PRE": "Preston",
    "SFD": "Salford", "SLD": "Salford", "SAL": "Salisbury", "SHF": "Sheffield",
    "SOU": "Southampton", "SOC": "Southend-on-Sea", "SOV": "Southend-on-Sea",
    "SAC": "St Albans", "SOT": "Stoke on Trent", "SUN": "Sunderland", "TRU": "Truro",
    "WKF": "Wakefield", "WKK": "Wakefield", "WIN": "Winchester", "WVH": "Wolverhampton",
    "WOF": "Worcester", "WOS": "Worcester", "YRK": "York",
    # Scotland
    "ABD": "Aberdeen", "DEE": "Dundee", "DFL": "Dunfermline", "DQM": "Dunfermline",
    "EDB": "Edinburgh", "GLC": "Glasgow", "GLQ": "Glasgow", "INV": "Inverness",
    "PTH": "Perth", "STG": "Stirling",
    # Wales
    "BNG": "Bangor", "CDF": "Cardiff", "NWP": "Newport", "SWA": "Swansea",
    "WRE": "Wrexham", "WXC": "Wrexham",
}


def is_configured() -> bool:
    return bool(os.environ.get("LDBWS_API_KEY"))


def _minutes_between(std: str, sta: str) -> int | None:
    """std/sta are "HH:MM" scheduled times - assumes same-day arrival,
    true for every realistic commute-to-a-city journey this feature
    covers."""
    if not std or not sta:
        return None
    try:
        dep_h, dep_m = (int(p) for p in std.split(":"))
        arr_h, arr_m = (int(p) for p in sta.split(":"))
    except ValueError:
        return None
    minutes = (arr_h * 60 + arr_m) - (dep_h * 60 + dep_m)
    return minutes if minutes > 0 else None


async def fastest_to_cities(station_crs: str) -> list[dict] | None:
    """The two nearest official UK cities reachable by a direct train
    (no change) from station_crs, among the next NUM_ROWS departures,
    fastest first. A single service can pass through several cities
    (e.g. a long-distance train calling at Peterborough, York and
    Newcastle) - every one of those is a genuine direct journey, so
    all are considered, not just the first city a service reaches.

    Returns None if the lookup itself failed or wasn't attempted (no
    key configured, no station). Returns [] - distinct from None - if
    the lookup succeeded but no calling point on any scanned service
    matched a city, meaning there's genuinely no direct train to a
    major city from here right now."""
    if not is_configured() or not station_crs:
        return None

    url = f"{API_BASE}/GetDepBoardWithDetails/{station_crs}"
    params = {"numRows": NUM_ROWS, "timeWindow": TIME_WINDOW_MIN}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                url,
                params=params,
                headers={"x-apikey": os.environ["LDBWS_API_KEY"], "Accept": "application/json"},
            )
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    data = response.json()
    best_per_city: dict[str, dict] = {}
    for service in data.get("trainServices") or []:
        if service.get("isCancelled"):
            continue
        std = service.get("std")
        for group in service.get("subsequentCallingPoints", []):
            for cp in group.get("callingPoint", []):
                city = CITY_STATIONS.get(cp.get("crs"))
                if not city:
                    continue
                minutes = _minutes_between(std, cp.get("st"))
                if minutes is None:
                    continue
                if city not in best_per_city or minutes < best_per_city[city]["minutes"]:
                    best_per_city[city] = {
                        "city": city,
                        "minutes": minutes,
                        "departs": std,
                        "arrives": cp.get("st"),
                        "operator": service.get("operator", ""),
                    }

    return sorted(best_per_city.values(), key=lambda j: j["minutes"])[:TOP_N]
