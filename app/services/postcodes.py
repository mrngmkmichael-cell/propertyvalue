"""Geocode and validate UK postcodes via the free postcodes.io API."""
from urllib.parse import quote

import httpx

API_BASE = "https://api.postcodes.io"


async def lookup_postcode(raw_postcode: str) -> dict | None:
    """Look up a postcode. Returns the postcodes.io result dict, or None
    if the postcode is not valid / not found."""
    encoded = quote(raw_postcode.strip())
    url = f"{API_BASE}/postcodes/{encoded}"

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)

    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()["result"]


async def nearby_postcodes(lat: float, lon: float, radius_m: int = 1000, limit: int = 100) -> list[dict]:
    """Postcodes within a radius of a point, nearest first - used to
    build a Land Registry VALUES batch for "sold nearby" comparables,
    since exact-postcode lookups are fast but a postcode-prefix scan
    of the whole Land Registry dataset times out (see land_registry.py)."""
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{API_BASE}/postcodes",
            params={"lon": lon, "lat": lat, "radius": radius_m, "limit": limit},
        )
    response.raise_for_status()
    result = response.json()["result"] or []
    return [
        {
            "postcode": r["postcode"],
            "distance_m": round(r["distance"]),
            "latitude": r["latitude"],
            "longitude": r["longitude"],
        }
        for r in result
    ]


async def outcode_centroid(outcode: str) -> dict | None:
    """Centroid of a postcode district (e.g. 'BR6') - used as a second
    reference point for wider-area comparisons, since postcodes.io has
    no local-authority-boundary centroid lookup."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{API_BASE}/outcodes/{quote(outcode.strip())}")

    if response.status_code == 404:
        return None
    response.raise_for_status()
    result = response.json()["result"]
    return {"latitude": result["latitude"], "longitude": result["longitude"]}
