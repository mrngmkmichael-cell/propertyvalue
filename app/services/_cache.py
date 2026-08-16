"""Tiny in-memory TTL cache shared by services that call external
APIs with data that doesn't need per-request freshness. Resets on
server restart - that's fine, it's purely a speed optimization, not
something anything depends on for correctness.
"""
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
