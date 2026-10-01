"""Reported road collisions near an address, from the DfT's STATS19 file.

Read straight out of app/data/road_collisions.bin, which
scripts/import_road_collisions.py writes: three parallel arrays sorted by
grid cell, about 7 MB for the five years to 2025. No database and no
network, so a report pays nothing for this card beyond a few microseconds
of arithmetic.

Every figure here is a count of collisions the police attended and
recorded, which is not the same as every collision that happened. Slight
collisions in particular are known to be under-reported, so a low count
is weaker evidence than a high one. The card says that in words.

Coverage is Great Britain. Northern Ireland collisions are published by
the PSNI and are not in this file, so an address there is told the check
does not cover it rather than being shown a zero.
"""
import array
import bisect
import json
import logging
import math
import os
import struct
import threading

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "road_collisions.bin")
MAGIC = b"UKPIRC1"
DEFAULT_METRES = 500
MOST_SERIOUS_SHOWN = 5
SEVERITIES = {1: "Fatal", 2: "Serious", 3: "Slight"}
METRES_PER_DEGREE_LAT = 110540.0
METRES_PER_DEGREE_LON = 111320.0

_lock = threading.Lock()
_loaded = False
_header: dict = {}
_cells = _lats = _lons = _packed = None


def _load() -> bool:
    """Read the file once per process. A missing or unreadable file means
    the card is not shown, never an error page."""
    global _loaded, _header, _cells, _lats, _lons, _packed
    with _lock:
        if _loaded:
            return _cells is not None
        _loaded = True
        try:
            with open(DATA, "rb") as handle:
                if handle.read(len(MAGIC)) != MAGIC:
                    raise ValueError("not a road collisions file")
                length = struct.unpack("<I", handle.read(4))[0]
                _header = json.loads(handle.read(length))
                count = _header["count"]
                cells, lats, lons, packed = (array.array("I"), array.array("i"),
                                             array.array("i"), array.array("H"))
                for store in (cells, lats, lons, packed):
                    store.fromfile(handle, count)
        except Exception as problem:  # noqa: BLE001 - the report must not care
            logging.warning("road collisions file unavailable: %s", problem)
            _cells = None
            return False
        _cells, _lats, _lons, _packed = cells, lats, lons, packed
        return True


def source() -> dict | None:
    """What the file says about itself: the release, the years and the
    date it was read."""
    if not _load():
        return None
    return {"name": _header["source"], "url": _header["dataset_page"],
            "read_on": _header["read_on"], "years": _header["years"],
            "first_year": _header["years"][0], "last_year": _header["years"][-1],
            "count": _header["count"], "coverage": _header["coverage"]}


OTHER_NATIONS = {
    "Northern Ireland": {
        "body": "the Police Service of Northern Ireland",
        "url": "https://www.psni.police.uk/about-us/our-publications-and-reports/"
               "official-statistics/road-traffic-collision-statistics",
    },
}


def outside_coverage(country: str | None) -> dict | None:
    """None where the DfT file applies (Great Britain, or a country we
    were not told). Otherwise the country and who publishes there."""
    name = (country or "").strip()
    if not name or name in ("England", "Wales", "Scotland"):
        return None
    return {"country": name, **OTHER_NATIONS.get(name, {})}


def _cell_range(lat: float, lon: float, metres: float):
    """The grid cells a circle of this radius touches, as (first key, last
    key) pairs, one pair per row of latitude."""
    cell = _header["cell_deg"]
    lon_cells = _header["lon_cells"]
    d_lat = metres / METRES_PER_DEGREE_LAT
    d_lon = metres / (METRES_PER_DEGREE_LON * max(math.cos(math.radians(lat)), 0.1))
    lat_lo = int((lat - d_lat + 90) / cell)
    lat_hi = int((lat + d_lat + 90) / cell)
    lon_lo = int((lon - d_lon + 180) / cell)
    lon_hi = int((lon + d_lon + 180) / cell)
    for row in range(lat_lo, lat_hi + 1):
        yield row * lon_cells + lon_lo, row * lon_cells + lon_hi


def _distance(lat1, lon1, lat2, lon2) -> float:
    """Flat-earth distance, which is accurate to well under a metre over
    the few hundred metres this card looks at."""
    dy = (lat2 - lat1) * METRES_PER_DEGREE_LAT
    dx = (lon2 - lon1) * METRES_PER_DEGREE_LON * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def near(lat: float | None, lon: float | None, metres: int = DEFAULT_METRES) -> dict | None:
    """Collisions within `metres` of a point, counted by severity, with
    the most serious few named. None when the file is unavailable or the
    address has no coordinates."""
    if lat is None or lon is None or not _load():
        return None
    scale = _header["scale"]
    base_year = _header["base_year"]
    found = []
    for first, last in _cell_range(lat, lon, metres):
        start = bisect.bisect_left(_cells, first)
        end = bisect.bisect_right(_cells, last)
        for index in range(start, end):
            away = _distance(lat, lon, _lats[index] / scale, _lons[index] / scale)
            if away > metres:
                continue
            bits = _packed[index]
            found.append({
                "severity": (bits & 0b11) + 1,
                "casualties": (bits >> 2) & 0b111111,
                "year": base_year + ((bits >> 8) & 0b1111),
                "month": (bits >> 12) & 0b1111,
                "metres": int(round(away)),
            })
    counts = {level: 0 for level in SEVERITIES}
    for hit in found:
        counts[hit["severity"]] += 1
    worst = sorted(found, key=lambda hit: (hit["severity"], hit["metres"]))[:MOST_SERIOUS_SHOWN]
    for hit in worst:
        hit["label"] = SEVERITIES[hit["severity"]]
        hit["when"] = _when(hit["year"], hit["month"])
    return {
        "radius_m": metres,
        "total": len(found),
        "fatal": counts[1],
        "serious": counts[2],
        "slight": counts[3],
        "killed_or_serious": counts[1] + counts[2],
        "casualties": sum(hit["casualties"] for hit in found),
        "worst": worst,
        "years": _header["years"],
        "first_year": _header["years"][0],
        "last_year": _header["years"][-1],
        "source": _header["source"],
        "source_url": _header["dataset_page"],
        "read_on": _header["read_on"],
    }


MONTHS = ("", "January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December")


def _when(year: int, month: int) -> str:
    return f"{MONTHS[month]} {year}" if 1 <= month <= 12 else str(year)
