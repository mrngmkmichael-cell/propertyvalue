"""Two-tier cache shared by services and pages.

Tier 1 is a dict in this process: microseconds, gone on restart. Tier 2,
opt-in per call, is a row in Postgres (see models.PageCache): a few
milliseconds, survives deploys. Use tier 2 for things that are expensive
to build and read far more often than they change - area guides are the
canonical case. Everything here is best-effort: a database hiccup falls
through to tier 1, then to a rebuild, and never to an error page.
"""
import collections
import datetime
import decimal
import builtins
import json
import logging
import sys
import zlib
import time

_store: "collections.OrderedDict" = collections.OrderedDict()
# Outcome of the most recent persistent read/write, for the Server-Timing
# diagnostic on pages that use it. Render's logs aren't reachable from a
# dev machine; the response header is.
last_outcome: str = ""

# Tier 1 is bounded and least-recently-used. It used to be a plain dict
# that nothing ever removed from: an expired entry simply stopped being
# returned, and sat there until the process restarted. Every property
# report leaves behind a full ~30-service gather result (hundreds of KB)
# plus one small entry per service, so on a 512 MB Render instance that
# was a slow, certain climb to the memory limit and a forced restart
# (observed 2026-08-23). Expired entries are now dropped on read, and
# the store evicts its oldest entry once it's full. Dict insertion order
# is the LRU order: a hit moves the key to the end.
MAX_ENTRIES = 1500

# An entry count alone wasn't enough: a second memory-limit restart
# followed during an area-guide crawl, because entries range from a few
# bytes to over a megabyte (a full property gather, a central-London
# area guide before its payload was slimmed). So the store also keeps a
# running total of approximate size and evicts oldest-first until it
# fits. Sizes are measured as the JSON length of the value - cheap, and
# a stable proxy; the live Python objects are a few times larger, which
# the budget below already allows for on a 512 MB instance. Anything
# over MAX_ENTRY_BYTES is served but never kept in memory: the second
# tier (Postgres) is milliseconds away for the things that size.
# Measured 2026-08-31, twenty distinct property reports against the real
# upstreams: the store said it held 33 MB, and dropping it returned 93 MB
# to the process. Sizing by JSON text length was undercounting real cost
# by about three times, because a gather result is thousands of small
# nested dicts, lists and strings and Python charges ~50-100 bytes of
# object header per one. So a "40 MB" budget was really licensing ~110 MB
# on a 512 MB instance, and the eviction loop never fired when it should
# have. The sizer below walks the object instead, which costs about what
# the json.dumps it replaces cost, and the number below is now real bytes.
MAX_BYTES = 48 * 1024 * 1024
MAX_ENTRY_BYTES = 4 * 1024 * 1024
_bytes = 0

# Key families kept whatever their size (18 Sep 2026, first-visitor audit
# item D5). The entry limit is right for a page that can be rebuilt on the
# next request, and wrong for a value something is waiting to find: the
# report's wait page asks every 900 ms whether the gather for its address
# is here, so a gather over the limit was built, refused, never found, and
# rebuilt every 30 seconds for as long as the page stayed open. A family
# named here still counts against MAX_BYTES and is evicted like any other
# entry; only the per-entry refusal is waived. Callers register the first
# element of their tuple keys with keep_oversized().
_KEEP_OVERSIZED: "builtins.set[str]" = builtins.set()


def keep_oversized(prefix: str) -> None:
    _KEEP_OVERSIZED.add(prefix)

# Deep enough for a gather result (dict -> service -> list -> row -> value)
# with room to spare. Anything deeper is charged at the cap rather than
# walked forever: a cache sizer must never be the thing that hangs.
_MAX_SIZE_DEPTH = 12


def _approx_size(value) -> int:
    """Roughly what this value costs the process, in bytes.

    Counts each distinct object once (a gather result shares plenty of
    small strings), and stops at _MAX_SIZE_DEPTH. It is an estimate, but
    an estimate of the right quantity, which the JSON length was not.
    """
    # This module exports a function called set(), which shadows the
    # builtin for the whole file: bare set() raises here, and so does
    # isinstance(x, set) below. Hence builtins, spelled out.
    seen: builtins.set[int] = builtins.set()

    def walk(obj, depth: int) -> int:
        obj_id = id(obj)
        if obj_id in seen:
            return 0
        seen.add(obj_id)
        try:
            size = sys.getsizeof(obj)
        except TypeError:
            return 64
        if depth >= _MAX_SIZE_DEPTH:
            return size
        if isinstance(obj, dict):
            for key, val in obj.items():
                size += walk(key, depth + 1) + walk(val, depth + 1)
        elif isinstance(obj, (list, tuple, builtins.set, frozenset)):
            for item in obj:
                size += walk(item, depth + 1)
        return size

    try:
        return walk(value, 0)
    except RecursionError:
        return MAX_ENTRY_BYTES + 1  # too gnarly to keep: never cache it


def stats() -> dict:
    """What the store is holding. Read by the admin page, so a memory
    problem is visible without reaching Render's logs."""
    return {"entries": len(_store), "bytes": _bytes,
            "max_entries": MAX_ENTRIES, "max_bytes": MAX_BYTES}


def pack_text(text: str) -> bytes:
    """A finished page as the store keeps it: compressed.

    Measured 16 Sep 2026: a schools guide page was 452,640 bytes, 197,211
    of them the stylesheet every page inlines, byte for byte the same.
    Kept as a str, 108 of them filled all of MAX_BYTES, and a crawler
    walking the 684 guides in the sitemap (4,122 views that week) pushed
    out the report gathers sharing the store: the homepage's own sample
    report answered in 6.34 s that morning and 0.28 s on the repeat.
    Compressed, the same page is about 60 KB, so the budget holds
    roughly seven times as many. Decompressing costs about a millisecond.
    """
    return zlib.compress(text.encode("utf-8"), 6)


def unpack_text(packed: bytes) -> str:
    return zlib.decompress(packed).decode("utf-8")


def get(key, ttl_seconds: float, keep_expired: bool = False):
    """keep_expired leaves an expired entry in place (still returning
    None) so a caller can follow up with get_stale() and serve it while
    a refresh runs - see flood._national_warnings."""
    entry = _store.get(key)
    if entry is None:
        return None
    if time.time() - entry[0] >= ttl_seconds:
        if not keep_expired:
            _evict(key)
        return None
    _store.move_to_end(key)
    return entry[1]


def stored_at(key) -> float | None:
    """When the value tier 1 holds for key was gathered, as epoch
    seconds, or None if tier 1 does not hold it.

    After a get_persistent hit from tier 2 this is the database row's own
    created_at, carried into tier 1 by _put, so a page can say honestly
    when its figures were gathered without a second round trip. Added
    16 Sep 2026 for the pages' dateModified; a page never falls back to
    the render time, because that would claim a freshness nobody checked."""
    entry = _store.get(key)
    return entry[0] if entry is not None else None


def get_stale(key):
    """The cached value regardless of age, or None if never cached (or
    since evicted). For stale-while-revalidate callers only."""
    entry = _store.get(key)
    return entry[1] if entry is not None else None


def _evict(key) -> None:
    global _bytes
    entry = _store.pop(key, None)
    if entry is not None:
        _bytes -= entry[2]


def _put(key, stored_at: float, value) -> None:
    global _bytes
    _evict(key)
    size = _approx_size(value)
    if size > MAX_ENTRY_BYTES:
        family = key[0] if isinstance(key, tuple) and key else key
        if family not in _KEEP_OVERSIZED:
            # Said out loud since 18 Sep 2026: a refused value is a
            # rebuild on every request for it, and until then nothing
            # anywhere recorded that it had happened.
            logging.warning("cache: not keeping %s, %d bytes is over the %d-byte entry limit",
                            _db_key(key), size, MAX_ENTRY_BYTES)
            return
    _store[key] = (stored_at, value, size)
    _bytes += size
    while _store and (len(_store) > MAX_ENTRIES or _bytes > MAX_BYTES):
        _evict(next(iter(_store)))


def set(key, value) -> None:
    _put(key, time.time(), value)


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
    _put(key, time.time() - age, value)
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
