"""Scheduled bus service at the stops near a point, from the BusStop table
that scripts/import_bus_frequency.py fills from the Bus Open Data Service
timetables (Department for Transport, Open Government Licence).

The figures are the timetable's promise for one reference Tuesday and
one reference Sunday inside the feed's validity: departures a passenger
can board, per band. "Buses an hour" divides the band's departures by
its hours. A stop on each side of the road is two stops, so the page
leads with the best single stop rather than summing them.
"""
import json
import math

from sqlalchemy import select

from app import db
from app.models import BusStop
from app.services import _cache
from app.services.gtfs import BAND_HOURS

RADIUS_M = 500
MAX_STOPS = 5
_TABLE_FLAG_TTL_S = 3600


def _distance_m(lat1, lon1, lat2, lon2) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlam = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def per_hour(departures: int, band: str) -> float:
    return round(departures / BAND_HOURS[band], 1)


def _row_to_stop(row: BusStop, distance: float) -> dict:
    try:
        routes = json.loads(row.routes or "[]")
    except ValueError:
        routes = []
    return {
        "atco_code": row.atco_code,
        "name": row.name,
        "distance_m": int(round(distance)),
        "latitude": row.latitude,
        "longitude": row.longitude,
        "weekday_day": row.weekday_day,
        "weekday_eve": row.weekday_eve,
        "sunday_day": row.sunday_day,
        "weekday_day_per_hour": per_hour(row.weekday_day, "weekday_day"),
        "weekday_eve_per_hour": per_hour(row.weekday_eve, "weekday_eve"),
        "sunday_day_per_hour": per_hour(row.sunday_day, "sunday_day"),
        "weekday_first": row.weekday_first,
        "weekday_last": row.weekday_last,
        "routes": routes,
    }


def _table_has_rows() -> bool:
    flag = _cache.get("bus_stops_populated", _TABLE_FLAG_TTL_S)
    if flag is not None:
        return flag
    with db.get_session() as session:
        populated = session.execute(select(BusStop.atco_code).limit(1)).first() is not None
    _cache.set("bus_stops_populated", populated)
    return populated


def stops_near(lat: float, lon: float, radius_m: int = RADIUS_M) -> dict | None:
    """None when the table has never been filled (the card says no data);
    otherwise the nearest stops with a scheduled departure, nearest first,
    and the best of them by weekday daytime departures."""
    if not db.is_configured() or not _table_has_rows():
        return None
    box_lat = radius_m / 111_320 * 1.05
    box_lon = box_lat / max(math.cos(math.radians(lat)), 0.2)
    with db.get_session() as session:
        rows = session.execute(
            select(BusStop).where(
                BusStop.latitude.between(lat - box_lat, lat + box_lat),
                BusStop.longitude.between(lon - box_lon, lon + box_lon),
            )
        ).scalars().all()
        stops = []
        for row in rows:
            distance = _distance_m(lat, lon, row.latitude, row.longitude)
            if distance <= radius_m:
                stops.append(_row_to_stop(row, distance))
        feed = (rows[0].feed_date, rows[0].ref_weekday, rows[0].ref_sunday) if rows else (None, None, None)
    stops.sort(key=lambda s: s["distance_m"])
    stops = stops[:MAX_STOPS]
    best = max(stops, key=lambda s: (s["weekday_day"], -s["distance_m"]), default=None)
    routes: dict[str, int] = {}
    for s in stops:
        for name, count in s["routes"]:
            routes[name] = max(routes.get(name, 0), count)
    return {
        "radius_m": radius_m,
        "stops": stops,
        "count": len(stops),
        "nearest": stops[0] if stops else None,
        "best": best,
        "routes": [name for name, _ in sorted(routes.items(), key=lambda kv: (-kv[1], kv[0]))][:8],
        "feed_date": feed[0].isoformat() if feed[0] else None,
        "ref_weekday": feed[1].isoformat() if feed[1] else None,
        "ref_sunday": feed[2].isoformat() if feed[2] else None,
    }
