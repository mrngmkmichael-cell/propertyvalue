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


async def any_postcode_in_outcode(outcode: str) -> str | None:
    """The first real postcode in a district, by prefix. The last resort
    for geocoding a district whose centroid is open country - Highland
    and island districts like HS2 or IV27 have no postcode within 2 km
    of their centre, which is postcodes.io's radius ceiling."""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{API_BASE}/postcodes/{quote(outcode.strip())}/autocomplete", params={"limit": 1}
        )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    result = response.json().get("result") or []
    # The prefix match is textual: "HS2" would also match "HS20 ..." if
    # such a district existed, so insist on the space.
    return next((p for p in result if p.upper().startswith(outcode.strip().upper() + " ")), None)


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
    # postcodes.io returns these as lists on an outcode, because a
    # district can straddle a boundary; the first is the dominant one.
    def _first(value):
        if isinstance(value, list):
            return value[0] if value else None
        return value

    return {
        "latitude": result["latitude"],
        "longitude": result["longitude"],
        "admin_district": _first(result.get("admin_district")),
        "region": _first(result.get("region")),
        "country": _first(result.get("country")),
    }


async def autocomplete(partial: str, limit: int = 8) -> list[str]:
    """Postcode suggestions for a partial entry, from postcodes.io's
    autocomplete endpoint. Returns [] on any failure - the search box
    works fine without suggestions."""
    partial = partial.strip().upper()
    if len(partial) < 2 or len(partial) > 8:
        return []
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(
                f"{API_BASE}/postcodes/{quote(partial)}/autocomplete",
                params={"limit": limit},
            )
            response.raise_for_status()
            return response.json().get("result") or []
    except (httpx.HTTPError, ValueError):
        return []
