"""Import scheduled bus departures per stop from the Bus Open Data Service.

Source: Department for Transport, Bus Open Data Service (BODS), the GTFS
timetable download, Open Government Licence. Operators of local bus
services in England must publish their timetables there; the "all" file
(https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/all/,
about 420 MB, no key needed) carries every published service, and
regional files exist for testing (north_east is 44 MB). Scottish and
Welsh services appear only where an operator has chosen to publish.

What is kept, per stop (app.models.BusStop): departures you can board
(pickup allowed) on the next Tuesday in the daytime (07:00 to 18:59) and
evening (19:00 to 22:59), on the next Sunday in the daytime (09:00 to
17:59), the first and last weekday departure, and the six routes that
call most often. Scheduled, not observed: what the timetable promises.
Stops with no departure on either day are not stored.

    python scripts/import_bus_frequency.py                 # downloads "all"
    python scripts/import_bus_frequency.py path/to/gtfs.zip
    python scripts/import_bus_frequency.py gtfs.zip --dry-run   # tally only

Re-runnable: replaces the table. Refresh monthly; the feed is rebuilt
daily and a reference week is chosen inside its validity. The national
file takes about 20 to 40 minutes and around 1 GB of memory on a PC.
"""
import csv
import datetime
import io
import json
import os
import sys
import tempfile
import time
import zipfile

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from app import db  # noqa: E402
from app.models import BusStop  # noqa: E402
from app.services import gtfs  # noqa: E402

ALL_URL = "https://data.bus-data.dft.gov.uk/timetable/download/gtfs-file/all/"


def _rows(z: zipfile.ZipFile, name: str):
    with z.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))


def _date(text: str) -> datetime.date:
    return datetime.datetime.strptime(text.strip(), "%Y%m%d").date()


def tally(path: str, today: datetime.date | None = None) -> tuple[dict, dict]:
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    info = next(_rows(z, "feed_info.txt")) if "feed_info.txt" in names else {}
    feed_start = _date(info.get("feed_start_date") or "20000101")
    feed_end = _date(info.get("feed_end_date") or "20991231")
    tuesday, sunday = gtfs.reference_dates(feed_start, feed_end, today)
    calendar = list(_rows(z, "calendar.txt"))
    exceptions = list(_rows(z, "calendar_dates.txt")) if "calendar_dates.txt" in names else []
    on_tuesday = gtfs.active_services(calendar, exceptions, tuesday)
    on_sunday = gtfs.active_services(calendar, exceptions, sunday)
    print(f"feed {info.get('feed_version', '?')} valid {feed_start} to {feed_end}; reference Tuesday {tuesday} ({len(on_tuesday)} services), Sunday {sunday} ({len(on_sunday)})")

    routes = {r["route_id"]: (r.get("route_short_name") or r.get("route_long_name") or r["route_id"]).strip() for r in _rows(z, "routes.txt")}
    # trip_id -> (route name, runs Tuesday, runs Sunday); only trips that run.
    trips: dict[str, tuple] = {}
    for r in _rows(z, "trips.txt"):
        tue, sun = r["service_id"] in on_tuesday, r["service_id"] in on_sunday
        if tue or sun:
            trips[r["trip_id"]] = (routes.get(r["route_id"], r["route_id"]), tue, sun)
    print(f"{len(trips):,} trips run on the reference days")

    tallies: dict[str, gtfs.StopTally] = {}
    t0 = time.perf_counter()
    seen = 0
    with z.open("stop_times.txt") as raw:
        reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
        header = next(reader)
        col = {name: i for i, name in enumerate(header)}
        i_trip, i_dep, i_stop = col["trip_id"], col["departure_time"], col["stop_id"]
        i_pickup = col.get("pickup_type")
        for row in reader:
            seen += 1
            trip = trips.get(row[i_trip])
            if trip is None:
                continue
            if i_pickup is not None and row[i_pickup] == "1":
                continue  # set-down only: nobody boards here
            minutes = gtfs.parse_gtfs_time(row[i_dep])
            if minutes is None:
                continue
            t = tallies.get(row[i_stop])
            if t is None:
                t = tallies[row[i_stop]] = gtfs.StopTally()
            t.add(minutes, trip[0], trip[1], trip[2])
            if seen % 5_000_000 == 0:
                print(f"  {seen:,} stop times read, {len(tallies):,} stops, {time.perf_counter() - t0:.0f}s")
    print(f"{seen:,} stop times; {len(tallies):,} stops with a departure")

    records = {}
    for r in _rows(z, "stops.txt"):
        t = tallies.get(r["stop_id"])
        if t is None or not t.any():
            continue
        try:
            lat, lon = float(r["stop_lat"]), float(r["stop_lon"])
        except (TypeError, ValueError):
            continue
        records[r["stop_id"]] = {
            "atco_code": r["stop_id"][:24],
            "name": (r.get("stop_name") or "").strip()[:100],
            "latitude": lat,
            "longitude": lon,
            "weekday_day": t.weekday_day,
            "weekday_eve": t.weekday_eve,
            "sunday_day": t.sunday_day,
            "weekday_first": gtfs.format_minutes(t.first),
            "weekday_last": gtfs.format_minutes(t.last),
            "routes": json.dumps(t.top_routes())[:200],
            "feed_date": feed_start,
            "ref_weekday": tuesday,
            "ref_sunday": sunday,
        }
    meta = {"feed_version": info.get("feed_version", ""), "feed_start": feed_start, "tuesday": tuesday, "sunday": sunday}
    return records, meta


def write(records: dict) -> None:
    db.init_db()
    rows = list(records.values())
    with db.get_session() as session:
        session.query(BusStop).delete()
        session.commit()
        batch = 2000
        for i in range(0, len(rows), batch):
            session.execute(BusStop.__table__.insert(), rows[i:i + batch])
            session.commit()
            if (i // batch) % 25 == 0:
                print(f"  {min(i + batch, len(rows)):,}/{len(rows):,}")
    print(f"{len(rows):,} stops written")


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in argv
    if args:
        path = args[0]
    else:
        path = os.path.join(tempfile.gettempdir(), "bods_gtfs_all.zip")
        print(f"downloading {ALL_URL} to {path}")
        with httpx.stream("GET", ALL_URL, timeout=1800, follow_redirects=True) as r, open(path, "wb") as f:
            r.raise_for_status()
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
    records, meta = tally(path)
    busiest = sorted(records.values(), key=lambda r: -r["weekday_day"])[:5]
    for r in busiest:
        print(f"  busiest: {r['name']} ({r['atco_code']}): {r['weekday_day']} weekday daytime, {r['weekday_eve']} evening, {r['sunday_day']} Sunday; first {r['weekday_first']} last {r['weekday_last']}; {r['routes']}")
    if dry:
        print("dry run: nothing written")
        return 0
    write(records)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
