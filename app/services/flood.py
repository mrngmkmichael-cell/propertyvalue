"""Active flood warnings/alerts from the Environment Agency's
real-time flood-monitoring API (no key required).

Note: this is near-real-time warning data, not a long-term flood
risk score for the area — there isn't a free JSON API for that
long-term assessment, so we're clear in the UI about what this is.
"""
import httpx

API_BASE = "https://environment.data.gov.uk/flood-monitoring"
SEARCH_RADIUS_KM = 10

# https://environment.data.gov.uk/flood-monitoring/doc/reference
SEVERITY_LABELS = {
    1: "Severe flood warning",
    2: "Flood warning",
    3: "Flood alert",
    4: "Warning no longer in force",
}


async def warnings_near(lat: float, lon: float) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            f"{API_BASE}/id/floods",
            params={"lat": lat, "long": lon, "dist": SEARCH_RADIUS_KM},
        )
    response.raise_for_status()
    items = response.json().get("items", [])

    warnings = []
    for item in items:
        level = item.get("severityLevel")
        warnings.append({
            "description": item.get("description", ""),
            "severity": SEVERITY_LABELS.get(level, item.get("severity", "")),
            "severity_level": level,
            "date": item.get("timeRaised", "")[:10],
        })

    # Most severe (lowest level number) first.
    warnings.sort(key=lambda w: (w["severity_level"] is None, w["severity_level"]))
    return warnings
