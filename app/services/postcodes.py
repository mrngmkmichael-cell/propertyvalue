"""Geocode and validate UK postcodes via the free postcodes.io API."""
from urllib.parse import quote

import httpx

from app.services import _cache

API_BASE = "https://api.postcodes.io"

# A postcode's coordinates, district and area codes change only when ONS
# publishes a new release, which is quarterly. Until 11 Sep 2026 this
# function was the one lookup on the site with no cache at all, and it
# is on the front of nearly every path that matters: the report route,
# the "building your report" page, the poll that page makes, the
# running-costs answer, and every school and area address check. Twenty
# four call sites, each an HTTP round trip to a third party before any
# of our own work began. Measured that day on production, the wait page
# that exists to remove the wait took 3.96 and 5.37 s to appear.
#
# A week is far shorter than the release cadence and short enough that a
# newly created postcode appears without a deploy. A miss is cached too,
# for an hour only: a postcode that does not exist is usually a typo,
# and a genuinely new one should not be denied for a week.
_TTL_S = 7 * 24 * 3600
_MISS_TTL_S = 3600


async def lookup_postcode(raw_postcode: str) -> dict | None:
    """Look up a postcode. Returns the postcodes.io result dict, or None
    if the postcode is not valid / not found."""
    cleaned = raw_postcode.strip().upper()
    key = ("postcode_lookup", cleaned)
    # None is a real answer here, so the cache holds a one-key wrapper
    # rather than the result itself; a bare None cannot be told apart
    # from a miss.
    hit = _cache.get(key, _TTL_S)
    if hit is not None:
        if hit["result"] is not None:
            return hit["result"]
        if _cache.get(key, _MISS_TTL_S) is not None:
            return None

    encoded = quote(raw_postcode.strip())
    url = f"{API_BASE}/postcodes/{encoded}"

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)

    if response.status_code == 404:
        _cache.set(key, {"result": None})
        return None
    response.raise_for_status()
    result = response.json()["result"]
    _cache.set(key, {"result": result})
    return result


_MONTHS = ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December")


async def retired_postcode(raw_postcode: str) -> dict | None:
    """The retirement record postcodes.io returns inside its own 404.

    Royal Mail withdraws a postcode when the addresses under it change,
    and postcodes.io answers one with a 404 whose body still carries the
    postcode, the year and month it was terminated, and its old
    coordinates. "We couldn't find that postcode, check the spelling" is
    the wrong answer for these: the spelling is right and the postcode is
    simply gone. Old deeds, probate letters and inherited addresses carry
    them (LS6 2AA, retired May 2018, and B29 6AA, August 2010, both
    reached the typo message on 7 Sep 2026).

    Returns None for a postcode that never existed, which is the real
    typo case, and None on any transport failure: the caller already has
    a correct, if blunt, message to fall back on.
    """
    encoded = quote(raw_postcode.strip())
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{API_BASE}/postcodes/{encoded}")
        if response.status_code != 404:
            return None
        terminated = (response.json() or {}).get("terminated")
    except (httpx.HTTPError, ValueError):
        return None
    if not terminated or not terminated.get("year_terminated"):
        return None

    month = terminated.get("month_terminated")
    month_name = _MONTHS[month - 1] if isinstance(month, int) and 1 <= month <= 12 else ""
    return {
        "postcode": terminated.get("postcode") or raw_postcode.strip().upper(),
        "year": terminated["year_terminated"],
        "month_name": month_name,
        "retired_on": f"{month_name} {terminated['year_terminated']}".strip(),
        "latitude": terminated.get("latitude"),
        "longitude": terminated.get("longitude"),
    }


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
        "admin_county": _first(result.get("admin_county")),
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
