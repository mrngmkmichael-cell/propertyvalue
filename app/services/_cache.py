"""Two-tier cache shared by services and pages.

Tier 1 is a dict in this process: microseconds, gone on restart. Tier 2,
opt-in per call, is a row in Postgres (see models.PageCache): a few
milliseconds, survives deploys. Use tier 2 for things that are expensive
to build and read far more often than they change - area guides are the
canonical case. Everything here is best-effort: a database hiccup falls
through to tier 1, then to a rebuild, and never to an error page.
"""
import datetime
import decimal
import json
import logging
import time

_store: dict = {}
# Outcome of the most recent persistent read/write, for the Server-Timing
# diagnostic on pages that use it. Render's logs aren't reachable from a
# dev machine; the response header is.
last_outcome: str = ""


def get(key, ttl_seconds: float):
    entry = _store.get(key)
    if entry and time.time() - entry[0] < ttl_seconds:
        return entry[1]
    return None


def set(key, value) -> None:
    _store[key] = (time.time(), value)


def coord_key(prefix: str, lat: float, lon: float) -> tuple:
    return (prefix, round(lat, 3), round(lon, 3))


def _json_default(value):
    """Postgres hands back Decimal for numeric columns and real dates for
    date columns; SQLite hands back floats and strings. A cache that
    works in dev and silently stays memory-only in production is worse
    than no cache, so both are normalised here."""
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    # A SQLAlchemy row (the school landscape embeds several per school):
    # store its columns as a dict. Jinja reads `row.field` and
    # `dict.field` identically, so templates are unaffected; only Python
    # code doing getattr() on one would notice, and the pages that cache
    # through here read aggregate counts, not rows.
    table = getattr(value, "__table__", None)
    if table is not None:
        return {c.name: getattr(value, c.name, None) for c in table.columns}
    raise TypeError(f"not JSON-serialisable: {type(value).__name__}")


def _db_key(key) -> str:
    if isinstance(key, tuple):
        return ":".join(str(part) for part in key)
    return str(key)


def get_persistent(key, ttl_seconds: float):
    """Tier 1, then tier 2. A tier-2 hit is promoted into tier 1 so the
    next read in this process is free."""
    global last_outcome
    hit = get(key, ttl_seconds)
    if hit is not None:
        last_outcome = "mem-hit"
        return hit

    from app import db
    if not db.is_configured():
        last_outcome = "db-not-configured"
        return None
    try:
        from app.models import PageCache
        with db.get_session() as session:
            row = session.get(PageCache, _db_key(key))
            if row is None:
                last_outcome = "db-miss"
                return None
            created = row.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=datetime.timezone.utc)
            age = (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds()
            if age >= ttl_seconds:
                last_outcome = "db-expired"
                return None
            value = json.loads(row.value)
    except Exception as exc:  # noqa: BLE001 - cache must never take a page down
        logging.warning("page cache read failed for %s: %s", _db_key(key), exc)
        last_outcome = "db-read-error:" + type(exc).__name__
        return None
    last_outcome = "db-hit"

    # Keep tier 1's clock honest: it inherits the row's remaining life
    # rather than starting a fresh TTL from now.
    _store[key] = (time.time() - age, value)
    return value


def set_persistent(key, value) -> None:
    """Tier 1 always; tier 2 when the value is plain JSON. Anything that
    isn't (dataclasses, exceptions, datetimes) stays memory-only and is
    logged once, so the caller needn't care."""
    global last_outcome
    set(key, value)

    from app import db
    if not db.is_configured():
        last_outcome = "db-not-configured"
        return
    try:
        payload = json.dumps(value, default=_json_default)
    except (TypeError, ValueError) as exc:
        logging.info("page cache: %s not JSON-serialisable (%s); memory only", _db_key(key), exc)
        last_outcome = "not-json:" + str(exc)[:60].replace(",", " ")
        return
    try:
        from app.models import PageCache
        with db.get_session() as session:
            session.merge(PageCache(
                cache_key=_db_key(key), value=payload,
                created_at=datetime.datetime.now(datetime.timezone.utc),
            ))
            session.commit()
        last_outcome = "db-write-ok"
    except Exception as exc:  # noqa: BLE001
        logging.warning("page cache write failed for %s: %s", _db_key(key), exc)
        last_outcome = "db-write-error:" + type(exc).__name__
