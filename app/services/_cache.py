"""Two-tier cache shared by services and pages.

Tier 1 is a dict in this process: microseconds, gone on restart. Tier 2,
opt-in per call, is a row in Postgres (see models.PageCache): a few
milliseconds, survives deploys. Use tier 2 for things that are expensive
to build and read far more often than they change - area guides are the
canonical case. Everything here is best-effort: a database hiccup falls
through to tier 1, then to a rebuild, and never to an error page.
"""
import datetime
import json
import logging
import time

_store: dict = {}


def get(key, ttl_seconds: float):
    entry = _store.get(key)
    if entry and time.time() - entry[0] < ttl_seconds:
        return entry[1]
    return None


def set(key, value) -> None:
    _store[key] = (time.time(), value)


def coord_key(prefix: str, lat: float, lon: float) -> tuple:
    return (prefix, round(lat, 3), round(lon, 3))


def _db_key(key) -> str:
    if isinstance(key, tuple):
        return ":".join(str(part) for part in key)
    return str(key)


def get_persistent(key, ttl_seconds: float):
    """Tier 1, then tier 2. A tier-2 hit is promoted into tier 1 so the
    next read in this process is free."""
    hit = get(key, ttl_seconds)
    if hit is not None:
        return hit

    from app import db
    if not db.is_configured():
        return None
    try:
        from app.models import PageCache
        with db.get_session() as session:
            row = session.get(PageCache, _db_key(key))
            if row is None:
                return None
            created = row.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=datetime.timezone.utc)
            age = (datetime.datetime.now(datetime.timezone.utc) - created).total_seconds()
            if age >= ttl_seconds:
                return None
            value = json.loads(row.value)
    except Exception as exc:  # noqa: BLE001 - cache must never take a page down
        logging.warning("page cache read failed for %s: %s", _db_key(key), exc)
        return None

    # Keep tier 1's clock honest: it inherits the row's remaining life
    # rather than starting a fresh TTL from now.
    _store[key] = (time.time() - age, value)
    return value


def set_persistent(key, value) -> None:
    """Tier 1 always; tier 2 when the value is plain JSON. Anything that
    isn't (dataclasses, exceptions, datetimes) stays memory-only and is
    logged once, so the caller needn't care."""
    set(key, value)

    from app import db
    if not db.is_configured():
        return
    try:
        payload = json.dumps(value)
    except (TypeError, ValueError) as exc:
        logging.info("page cache: %s not JSON-serialisable (%s); memory only", _db_key(key), exc)
        return
    try:
        from app.models import PageCache
        with db.get_session() as session:
            session.merge(PageCache(
                cache_key=_db_key(key), value=payload,
                created_at=datetime.datetime.now(datetime.timezone.utc),
            ))
            session.commit()
    except Exception as exc:  # noqa: BLE001
        logging.warning("page cache write failed for %s: %s", _db_key(key), exc)
