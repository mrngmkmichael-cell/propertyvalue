"""EPC (energy rating) certificates from the government's
Energy Performance of Buildings Data service. Requires a free
account and a bearer token (see .env.example) — the search
returns [] rather than raising when no token is configured, so
the rest of the site still works if this layer isn't set up yet.
"""
import os

import httpx

API_BASE = "https://api.get-energy-performance-data.communities.gov.uk"


def is_configured() -> bool:
    return bool(os.environ.get("EPC_API_TOKEN"))


async def certificates_for_postcode(canonical_postcode: str) -> list[dict]:
    """Fetch domestic EPC certificates for a postcode, newest first."""
    token = os.environ.get("EPC_API_TOKEN")
    if not token:
        return []

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{API_BASE}/api/domestic/search",
            params={"postcode": canonical_postcode},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
    if response.status_code == 404:
        # The API's way of saying "no certificates for this query" —
        # not a real failure.
        return []
    response.raise_for_status()
    records = response.json().get("data", [])

    certificates = []
    for rec in records:
        address_parts = [
            rec.get(f"addressLine{n}") for n in (1, 2, 3, 4)
        ]
        address = ", ".join(p for p in address_parts if p)
        certificates.append({
            "address": address or rec.get("postTown", ""),
            "rating": rec.get("currentEnergyEfficiencyBand", "?"),
            "date": rec.get("registrationDate", ""),
            "certificate_number": rec.get("certificateNumber", ""),
        })

    certificates.sort(key=lambda c: c["date"], reverse=True)
    return certificates


async def certificate_detail(certificate_number: str) -> dict | None:
    """Extra fields (floor area, dwelling type, room count) not
    included in the search results - a separate API call per
    certificate, so only fetch this for one representative property
    (the property header), not the whole list."""
    token = os.environ.get("EPC_API_TOKEN")
    if not token or not certificate_number:
        return None

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{API_BASE}/api/certificate",
            params={"certificate_number": certificate_number},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json().get("data", {})

    return {
        "dwelling_type": data.get("dwelling_type", ""),
        "total_floor_area": data.get("total_floor_area"),
        "habitable_room_count": data.get("habitable_room_count"),
    }
