"""EPC (energy rating) certificates from the government's
Energy Performance of Buildings Data service. Requires a free
account and a bearer token (see .env.example) — the search
returns [] rather than raising when no token is configured, so
the rest of the site still works if this layer isn't set up yet.
"""
import os

import httpx

API_BASE = "https://api.get-energy-performance-data.communities.gov.uk"

# RdSAP's standard England & Wales construction age band lettering -
# not in the certificate's top-level fields, only nested inside
# sap_building_parts[n].construction_age_band as a bare letter.
CONSTRUCTION_AGE_BANDS = {
    "A": "Before 1900",
    "B": "1900–1929",
    "C": "1930–1949",
    "D": "1950–1966",
    "E": "1967–1975",
    "F": "1976–1982",
    "G": "1983–1990",
    "H": "1991–1995",
    "I": "1996–2002",
    "J": "2003–2006",
    "K": "2007–2011",
    "L": "2012 onwards",
}


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

    # RdSAP (existing dwellings) records a lettered age band; SAP
    # (new-build) records an exact construction_year instead - only
    # one of the two will be present, depending on assessment type.
    year_built = None
    for part in data.get("sap_building_parts") or []:
        if part.get("construction_year"):
            year_built = str(part["construction_year"])
            break
        if part.get("construction_age_band"):
            year_built = CONSTRUCTION_AGE_BANDS.get(part["construction_age_band"])
            break

    return {
        "dwelling_type": data.get("dwelling_type", ""),
        "total_floor_area": data.get("total_floor_area"),
        "habitable_room_count": data.get("habitable_room_count"),
        "year_built": year_built,
    }
