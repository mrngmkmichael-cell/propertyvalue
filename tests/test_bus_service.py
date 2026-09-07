"""Scheduled buses at the nearest stops: the GTFS tally helpers and the
query over the imported table."""
import datetime

from app import db
from app.models import BusStop
from app.services import _cache, bus_service, gtfs


def test_time_parsing_and_bands():
    assert gtfs.parse_gtfs_time("07:05:00") == 425 and gtfs.parse_gtfs_time("25:10:00") == 1510 and gtfs.parse_gtfs_time("x") is None
    assert gtfs.format_minutes(1510) == "01:10" and gtfs.format_minutes(None) == ""
    t = gtfs.StopTally()
    t.add(7 * 60, "10", True, False)      # weekday daytime
    t.add(18 * 60 + 59, "10", True, False)
    t.add(19 * 60, "43", True, False)     # evening
    t.add(23 * 60 + 30, "43", True, False)  # night: first/last only
    t.add(9 * 60, "10", False, True)      # Sunday daytime
    t.add(25 * 60, "10", True, True)      # after midnight: no band
    assert (t.weekday_day, t.weekday_eve, t.sunday_day) == (2, 1, 1)
    assert gtfs.format_minutes(t.first) == "07:00" and gtfs.format_minutes(t.last) == "01:00"
    assert t.top_routes() == ["10"]


def test_active_services_follow_the_calendar_and_its_exceptions():
    calendar = [
        {"service_id": "wk", "monday": "1", "tuesday": "1", "wednesday": "1", "thursday": "1", "friday": "1", "saturday": "0", "sunday": "0", "start_date": "20260907", "end_date": "20270607"},
        {"service_id": "sun", "monday": "0", "tuesday": "0", "wednesday": "0", "thursday": "0", "friday": "0", "saturday": "0", "sunday": "1", "start_date": "20260907", "end_date": "20270607"},
        {"service_id": "old", "monday": "1", "tuesday": "1", "wednesday": "1", "thursday": "1", "friday": "1", "saturday": "1", "sunday": "1", "start_date": "20250101", "end_date": "20250131"},
    ]
    exceptions = [{"service_id": "wk", "date": "20260908", "exception_type": "2"}, {"service_id": "extra", "date": "20260908", "exception_type": "1"}]
    assert gtfs.active_services(calendar, [], datetime.date(2026, 9, 8)) == {"wk"}
    assert gtfs.active_services(calendar, exceptions, datetime.date(2026, 9, 8)) == {"extra"}
    assert gtfs.active_services(calendar, exceptions, datetime.date(2026, 9, 13)) == {"sun"}
    tue, sun = gtfs.reference_dates(datetime.date(2026, 9, 7), datetime.date(2027, 6, 27), today=datetime.date(2026, 9, 7))
    assert (tue, sun) == (datetime.date(2026, 9, 8), datetime.date(2026, 9, 13))


def test_stops_near_returns_the_nearest_and_the_best(client):
    with db.get_session() as session:
        session.query(BusStop).delete()
        session.add_all([
            BusStop(atco_code="A1", name="High Street", latitude=53.4501, longitude=-2.2200, weekday_day=96, weekday_eve=16, sunday_day=36,
                    weekday_first="05:30", weekday_last="23:45", routes='["43", "X47"]',
                    feed_date=datetime.date(2026, 9, 7), ref_weekday=datetime.date(2026, 9, 8), ref_sunday=datetime.date(2026, 9, 13)),
            BusStop(atco_code="A2", name="Church Lane", latitude=53.4530, longitude=-2.2200, weekday_day=12, weekday_eve=0, sunday_day=0,
                    weekday_first="07:10", weekday_last="18:40", routes='[["7", 12]]'),  # the older stored shape
            BusStop(atco_code="FAR", name="Elsewhere", latitude=53.5, longitude=-2.3, weekday_day=200),
        ])
        session.commit()
    _cache._store.clear(); _cache._bytes = 0
    out = bus_service.stops_near(53.4500, -2.2200)
    assert [s["atco_code"] for s in out["stops"]] == ["A1", "A2"]
    assert out["best"]["atco_code"] == "A1" and out["best"]["weekday_day_per_hour"] == 8.0
    assert out["best"]["weekday_eve_per_hour"] == 4.0 and out["best"]["sunday_day_per_hour"] == 4.0
    assert out["routes"] == ["43", "X47", "7"] and out["ref_weekday"] == "2026-09-08"
    assert out["stops"][0]["distance_m"] < 20 and 300 < out["stops"][1]["distance_m"] < 400
    empty = bus_service.stops_near(51.5, 0.5)
    assert empty["count"] == 0 and empty["best"] is None


def test_route_names_read_both_stored_shapes():
    assert bus_service.route_names('["43", "X47"]') == ["43", "X47"]
    assert bus_service.route_names('[["43", 48], ["X47", 24], ["43", 2]]') == ["43", "X47"]
    assert bus_service.route_names("") == [] and bus_service.route_names("not json") == []

