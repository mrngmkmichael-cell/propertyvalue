"""The tier-2 cache has to tidy itself.

Rows were written and never removed. By 1 Oct 2026 the table held
22,050 rows and 48 MB inside a 512 MB database sitting at 499 MB, a
cache on its way to an outage. These tests pin the three things that
make the sweep safe: it only takes rows nothing can still use, it never
takes the two deliberately long-lived keys, and a sweep that cannot run
is silent rather than fatal.
"""
import datetime

from sqlalchemy import select

from app.db import get_session
from app.models import PageCache
from app.services import _cache


def _row(key, days_old, session):
    session.merge(PageCache(
        cache_key=key, value='{"a": 1}',
        created_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_old)))


def _keys():
    with get_session() as session:
        return set(session.execute(select(PageCache.cache_key)).scalars().all())


def test_old_rows_go_and_rows_a_page_could_still_use_stay(client):
    with get_session() as session:
        _row("prunetest:ancient", 40, session)
        _row("prunetest:a_week", 7, session)
        _row("prunetest:today", 0, session)
        session.commit()
    try:
        assert _cache.prune_persistent() >= 1
        keys = _keys()
        assert "prunetest:ancient" not in keys, "40 days old is four TTLs past any page"
        assert "prunetest:a_week" in keys and "prunetest:today" in keys
    finally:
        with get_session() as session:
            for key in ("prunetest:ancient", "prunetest:a_week", "prunetest:today"):
                row = session.get(PageCache, key)
                if row is not None:
                    session.delete(row)
            session.commit()


def test_the_long_lived_keys_are_never_pruned(client):
    assert set(_cache.NEVER_PRUNE) == {"indexnow_submitted_hash", "watchlist_alert_runs"}
    with get_session() as session:
        for key in _cache.NEVER_PRUNE:
            if session.get(PageCache, key) is None:
                _row(key, 400, session)
        session.commit()
    _cache.prune_persistent()
    keys = _keys()
    for key in _cache.NEVER_PRUNE:
        assert key in keys, f"{key} is written once and read for a year or more"


def test_the_sweep_is_bounded_so_it_never_holds_a_long_lock(client):
    assert _cache.prune_persistent(older_than_days=40, limit=0) == 0
    assert _cache.PRUNE_BATCH <= 5000


def test_a_sweep_that_cannot_run_returns_zero_rather_than_raising(monkeypatch):
    monkeypatch.setattr("app.db.is_configured", lambda: False)
    assert _cache.prune_persistent() == 0


def test_writes_trip_the_sweep_rather_than_a_cron_nobody_runs():
    assert _cache.PRUNE_EVERY_WRITES > 0
    assert _cache.PRUNE_AFTER_DAYS >= 4 * 7, "four times the longest page TTL"
