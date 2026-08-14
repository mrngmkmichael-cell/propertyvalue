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
