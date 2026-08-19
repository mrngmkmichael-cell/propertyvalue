"""Real routing distance/time between two points, via OpenRouteService's
Directions API (free tier: 2,500 requests/day). Requires a free
self-registered API key (see .env.example) - returns None rather than
raising when no key is configured, same pattern as rail_journey.py's
LDBWS lookup.

Two uses:
- walking_distance(): upgrades the "nearest station" figure from
  straight-line (haversine) distance to an actual walking route - the
  two diverge a lot near rivers, railway embankments, and gated
  developments, which is exactly where a straight-line figure misleads
  someone judging their real commute.
- commute_times(): a user-specified commute-to-work calculator, driving
  and cycling times to an arbitrary destination the user types in.
"""
import asyncio
import os

import httpx

BASE_URL = "https://api.openrouteservice.org/v2/directions"

# OpenRouteService profile names, keyed by our own short label.
_PROFILES = {"walking": "foot-walking", "driving": "driving-car", "cycling": "cycling-regular"}


def is_configured() -> bool:
    return bool(os.environ.get("OPENROUTESERVICE_API_KEY"))


async def _route(client: httpx.AsyncClient, profile: str, lat1: float, lon1: float, lat2: float, lon2: float) -> dict | None:
    params = {
        "api_key": os.environ["OPENROUTESERVICE_API_KEY"],
        "start": f"{lon1},{lat1}",
        "end": f"{lon2},{lat2}",
    }
    try:
        response = await client.get(f"{BASE_URL}/{profile}", params=params)
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    try:
        # A GET to this endpoint returns GeoJSON by default (not the
        # "routes"/"summary" shape shown in the API docs, which is
        # what POSTing a JSON body with format=json gets you) -
        # confirmed against a live response before writing this.
        segment = response.json()["features"][0]["properties"]["segments"][0]
        return {"distance_m": segment["distance"], "duration_min": segment["duration"] / 60}
    except (KeyError, IndexError, TypeError):
        return None


async def walking_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> dict | None:
    """Returns {"distance_m": float, "duration_min": float} for the
    real walking route from (lat1, lon1) to (lat2, lon2), or None if
    not configured or the lookup failed - callers should fall back to
    straight-line distance in that case, not hide the figure."""
    if not is_configured():
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        return await _route(client, _PROFILES["walking"], lat1, lon1, lat2, lon2)


async def commute_times(lat1: float, lon1: float, lat2: float, lon2: float) -> dict | None:
    """Driving and cycling time from (lat1, lon1) to (lat2, lon2) for
    the commute-time calculator. Returns
    {"driving": {...} | None, "cycling": {...} | None} - each leg is
    independently None if that specific routing call failed (e.g. no
    drivable route to a pedestrianised destination), distinct from the
    whole result being None when the feature isn't configured at all."""
    if not is_configured():
        return None
    async with httpx.AsyncClient(timeout=10) as client:
        driving, cycling = await asyncio.gather(
            _route(client, _PROFILES["driving"], lat1, lon1, lat2, lon2),
            _route(client, _PROFILES["cycling"], lat1, lon1, lat2, lon2),
        )
    return {"driving": driving, "cycling": cycling}
