"""Real pedestrian-routing distance/time between two points, via
OpenRouteService's Directions API (free tier: 2,500 requests/day).
Requires a free self-registered API key (see .env.example) - returns
None rather than raising when no key is configured, same pattern as
rail_journey.py's LDBWS lookup.

Used to upgrade the "nearest station" figure from straight-line
(haversine) distance to an actual walking route - the two diverge a
lot near rivers, railway embankments, and gated developments, which is
exactly where a straight-line figure misleads someone judging their
real commute.
"""
import os

import httpx

API_URL = "https://api.openrouteservice.org/v2/directions/foot-walking"


def is_configured() -> bool:
    return bool(os.environ.get("OPENROUTESERVICE_API_KEY"))


async def walking_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> dict | None:
    """Returns {"distance_m": float, "duration_min": float} for the
    real walking route from (lat1, lon1) to (lat2, lon2), or None if
    not configured or the lookup failed - callers should fall back to
    straight-line distance in that case, not hide the figure."""
    if not is_configured():
        return None

    params = {
        "api_key": os.environ["OPENROUTESERVICE_API_KEY"],
        "start": f"{lon1},{lat1}",
        "end": f"{lon2},{lat2}",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(API_URL, params=params)
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
