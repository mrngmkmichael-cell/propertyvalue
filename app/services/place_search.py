"""Resolves a free-text search (full postcode, postcode district, or
a plain place name) to a location - the School Guide's search box
accepts all three, since a parent comparing areas is just as likely
to type "Oxford" as "OX1 1BP".

Tries postcodes.io first (exact and authoritative for anything
postcode-shaped), then falls back to Nominatim/OpenStreetMap's free
geocoder for a bare place name - the same OSM ecosystem already used
elsewhere in this app (Overpass, Leaflet tiles), no API key needed.
"""
from urllib.parse import quote

import httpx

from app.services.postcodes import lookup_postcode, outcode_centroid

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
# A dedicated User-Agent identifying the app is required by Nominatim's
# usage policy - the default httpx one gets silently rate-limited.
HEADERS = {"User-Agent": "PropertyValue/1.0 (UK property research tool)"}


async def resolve(query: str) -> dict | None:
    """Returns {"latitude", "longitude", "label"} for the best match,
    or None if nothing was found by either source."""
    query = query.strip()
    if not query:
        return None

    postcode_result = await lookup_postcode(query)
    if postcode_result:
        return {
            "latitude": postcode_result["latitude"],
            "longitude": postcode_result["longitude"],
            "label": postcode_result["postcode"],
        }

    # A short all-letters-then-digits token like "OX1" or "SW1A" is
    # very likely a postcode district rather than a place name -
    # postcodes.io's own outcode endpoint handles it more precisely
    # than a general-purpose geocoder would.
    if len(query) <= 4 and any(c.isdigit() for c in query):
        outcode_result = await outcode_centroid(query.upper())
        if outcode_result:
            return {
                "latitude": outcode_result["latitude"],
                "longitude": outcode_result["longitude"],
                "label": query.upper(),
            }

    async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
        response = await client.get(
            NOMINATIM_URL,
            params={"q": query, "countrycodes": "gb", "format": "jsonv2", "limit": 1},
        )
    response.raise_for_status()
    results = response.json()
    if not results:
        return None
    place = results[0]
    label = place.get("display_name", query).split(",")[0]
    return {"latitude": float(place["lat"]), "longitude": float(place["lon"]), "label": label}
