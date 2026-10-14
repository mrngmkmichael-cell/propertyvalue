"""Stations near a point, from app/data/stations.json: every National
Rail, Underground, metro and tram station in Great Britain, from the
Department for Transport's NaPTAN register, with Underground lines from
TfL open data (scripts/import_stations.py, refreshed monthly).

Replaced OpenStreetMap on 14 Oct 2026, when its Overpass service began
refusing this site. Read from memory: no network call, no database
round trip, so it can never be the slow part of a report.
"""
import json
import math
import pathlib

from app.services import transit_lines

DATA = pathlib.Path(__file__).resolve().parent.parent / "data" / "stations.json"
SOURCE = "NaPTAN, the Department for Transport's register of stations and stops"

_stations: list[dict] | None = None


def _load() -> list[dict]:
    global _stations
    if _stations is None:
        _stations = json.loads(DATA.read_text(encoding="utf-8"))["stations"]
    return _stations


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _badges(names: list[str]) -> list[dict]:
    out = []
    for name in names:
        color = transit_lines.color_for_line(name)
        out.append({"name": name, "color": color, "text_color": transit_lines.text_color_for(color)})
    return out


def near(lat: float, lon: float, radius_m: int, limit: int) -> dict[str, list[dict]]:
    """Stations within radius_m by mode ("rail", "tube", "tram"), nearest
    first, at most `limit` each, in the shape the report's transport card
    has always read: name, network, straight-line distance, CRS for a
    National Rail station, and line badges (the Underground lines, or the
    one network badge of a tram or light railway)."""
    box_lat = radius_m / 111_320 * 1.05
    box_lon = box_lat / max(math.cos(math.radians(lat)), 0.2)
    by_mode: dict[str, list[dict]] = {"rail": [], "tube": [], "tram": []}
    for s in _load():
        if abs(s["lat"] - lat) > box_lat or abs(s["lon"] - lon) > box_lon:
            continue
        distance = _haversine_m(lat, lon, s["lat"], s["lon"])
        if distance > radius_m:
            continue
        lines = s["lines"] or ([s["network"]] if s["network"] and s["network"] != "London Underground" else [])
        by_mode[s["mode"]].append({
            "id": s["atco"], "type": "naptan", "name": s["name"], "network": s["network"],
            "distance_m": round(distance), "lat": s["lat"], "lon": s["lon"],
            "crs": s["crs"], "lines": _badges(lines),
        })
    for found in by_mode.values():
        found.sort(key=lambda st: st["distance_m"])
    return {mode: found[:limit] for mode, found in by_mode.items()}
