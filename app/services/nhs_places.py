"""Pharmacies, dentists and hospitals near a point, from
app/data/nhs_places.json: NHS England's Organisation Data Service for
pharmacies, the Care Quality Commission's register for dentists and
hospitals (scripts/import_nhs_places.py, refreshed monthly).

Replaced OpenStreetMap on 14 Oct 2026, when its Overpass service began
refusing this site. Read from memory: no network call, no database round
trip. A list answers only for the nations it covers (pharmacies England
and Wales, dentists and hospitals England); elsewhere the card says the
list does not cover the address, never that there are none.
"""
import json
import math
import pathlib

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "nhs_places.json"
MILE_M = 1609
# Hospitals are fewer and further apart, so they are counted over three
# miles, and the card says so; the others over one mile.
RADIUS_M = {"pharmacy": MILE_M, "dentist": MILE_M, "hospital": 3 * MILE_M}
LIST_LIMIT = 10
# With no nation to go on, a list covers a point when it holds a place of
# that kind within this distance.
COVERAGE_PROBE_M = 30_000

_data: dict | None = None


def _load() -> dict:
    global _data
    if _data is None:
        raw = json.loads(DATA.read_text(encoding="utf-8"))
        by_kind: dict[str, list] = {kind: [] for kind in RADIUS_M}
        for kind, name, lat, lon, _country in raw["places"]:
            by_kind[kind].append((name, lat, lon))
        _data = {"by_kind": by_kind, "coverage": raw["coverage"]}
    return _data


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _within(places: list, lat: float, lon: float, radius_m: float) -> list[tuple[float, str, float, float]]:
    box_lat = radius_m / 111_320 * 1.05
    box_lon = box_lat / max(math.cos(math.radians(lat)), 0.2)
    found = []
    for name, p_lat, p_lon in places:
        if abs(p_lat - lat) > box_lat or abs(p_lon - lon) > box_lon:
            continue
        distance = _haversine_m(lat, lon, p_lat, p_lon)
        if distance <= radius_m:
            found.append((distance, name, p_lat, p_lon))
    found.sort()
    return found


def covers(kind: str, lat: float, lon: float, country: str | None) -> bool:
    data = _load()
    if country:
        return country in data["coverage"].get(kind, [])
    return bool(_within(data["by_kind"][kind], lat, lon, COVERAGE_PROBE_M))


def near(lat: float, lon: float, country: str | None = None) -> dict[str, dict]:
    """{"pharmacy"|"dentist"|"hospital": {"places": nearest first, "total":
    how many within the kind's radius}}, for the kinds whose list covers
    this point only."""
    data = _load()
    out = {}
    for kind, radius in RADIUS_M.items():
        if not covers(kind, lat, lon, country):
            continue
        found = _within(data["by_kind"][kind], lat, lon, radius)
        out[kind] = {
            "places": [{"name": name, "distance_m": round(d), "lat": p_lat, "lon": p_lon}
                       for d, name, p_lat, p_lon in found[:LIST_LIMIT]],
            "total": len(found),
        }
    return out
