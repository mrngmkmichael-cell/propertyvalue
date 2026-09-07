"""The small, testable parts of reading a GTFS timetable feed: which
services run on a given date, which hour band a departure falls in, and
the per-stop tally the bus importer builds. Kept apart from the importer
so a test can exercise them on a handful of rows.

Bands, all local time:
  weekday daytime  07:00 to 18:59, twelve hours
  weekday evening  19:00 to 22:59, four hours
  Sunday daytime   09:00 to 17:59, nine hours
A departure after midnight on the same service day (GTFS writes 25:10:00)
counts for first and last bus but for no band.
"""
import datetime

WEEKDAY_DAY = ("weekday_day", 7, 19, 12)
WEEKDAY_EVE = ("weekday_eve", 19, 23, 4)
SUNDAY_DAY = ("sunday_day", 9, 18, 9)
BAND_HOURS = {"weekday_day": 12, "weekday_eve": 4, "sunday_day": 9}
DAY_FLAGS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MAX_ROUTES = 6


def parse_gtfs_time(text: str) -> int | None:
    """"07:05:00" -> 425 minutes past midnight; hours may exceed 23."""
    try:
        h, m, _s = text.strip().split(":")
        return int(h) * 60 + int(m)
    except (AttributeError, ValueError):
        return None


def format_minutes(minutes: int | None) -> str:
    if minutes is None:
        return ""
    minutes = minutes % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def active_services(calendar_rows, exception_rows, on: datetime.date) -> set[str]:
    """service_ids running on a date: the calendar's weekday flag within
    its date range, then calendar_dates exceptions (1 adds, 2 removes)."""
    stamp = on.strftime("%Y%m%d")
    flag = DAY_FLAGS[on.weekday()]
    active = set()
    for row in calendar_rows:
        if row.get(flag) == "1" and row.get("start_date", "") <= stamp <= row.get("end_date", ""):
            active.add(row["service_id"])
    for row in exception_rows:
        if row.get("date") != stamp:
            continue
        if row.get("exception_type") == "1":
            active.add(row["service_id"])
        elif row.get("exception_type") == "2":
            active.discard(row["service_id"])
    return active


def reference_dates(feed_start: datetime.date, feed_end: datetime.date, today: datetime.date | None = None) -> tuple[datetime.date, datetime.date]:
    """The first Tuesday and the first Sunday at least a day ahead and
    inside the feed's own validity, so the tally describes a normal week
    the feed actually covers."""
    today = today or datetime.date.today()
    start = max(today + datetime.timedelta(days=1), feed_start)
    tuesday = start + datetime.timedelta(days=(1 - start.weekday()) % 7)
    sunday = start + datetime.timedelta(days=(6 - start.weekday()) % 7)
    if tuesday > feed_end or sunday > feed_end:
        raise ValueError(f"feed ends {feed_end}, before a full reference week from {start}")
    return tuesday, sunday


class StopTally:
    """Departures per stop for the reference days, plus first and last
    weekday departure and the routes that call most often."""
    __slots__ = ("weekday_day", "weekday_eve", "sunday_day", "first", "last", "routes")

    def __init__(self):
        self.weekday_day = 0
        self.weekday_eve = 0
        self.sunday_day = 0
        self.first = None
        self.last = None
        self.routes: dict[str, int] = {}

    def add(self, minutes: int, route: str, weekday: bool, sunday: bool) -> None:
        hour = minutes // 60
        if weekday:
            if 7 <= hour < 19:
                self.weekday_day += 1
                self.routes[route] = self.routes.get(route, 0) + 1
            elif 19 <= hour < 23:
                self.weekday_eve += 1
            if self.first is None or minutes < self.first:
                self.first = minutes
            if self.last is None or minutes > self.last:
                self.last = minutes
        if sunday and 9 <= hour < 18:
            self.sunday_day += 1

    def any(self) -> bool:
        return bool(self.weekday_day or self.weekday_eve or self.sunday_day)

    def top_routes(self, limit: int = MAX_ROUTES) -> list[list]:
        return [[name, count] for name, count in sorted(self.routes.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]
