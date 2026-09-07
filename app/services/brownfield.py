"""Brownfield land register sites near a point.

Source: MHCLG's planning data platform, planning.data.gov.uk, dataset
"brownfield-land", Open Government Licence. Since 2017 every local
planning authority in England must keep a register of previously
developed land it considers suitable for housing (the Town and Country
Planning (Brownfield Land Register) Regulations 2017), and the platform
collects those registers into one national layer: 37,670 sites on
7 Sep 2026. Not every council has published to the platform and some
registers are years old, so the result carries the council's own count
there and the newest entry near the home, and says plainly when the
council has nothing on it. England only: the regulations do not apply
in Wales or Scotland.

A register entry is a council's view that a site could take homes, not
a planning permission; the register records whether permission exists,
and the page says which.
"""
import json
import math
import pathlib
import re

import httpx

from app.services import _cache

ENTITY_URL = "https://www.planning.data.gov.uk/entity.json"
DATASET = "brownfield-land"
RADIUS_M = 800  # about half a mile
CACHE_TTL_S = 86400 * 30  # registers are reviewed yearly at most

# GSS code -> {entity, name, type}, from scripts/import_planning_organisations.py.
_ORGS_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "planning_organisations.json"
try:
    ORGANISATIONS: dict[str, dict] = json.loads(_ORGS_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    ORGANISATIONS = {}

_POINT = re.compile(r"POINT\s*\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s*\)")

PERMISSION_LABELS = {
    "permissioned": "Has planning permission",
    "not-permissioned": "No permission yet",
    "pending-decision": "Decision pending",
}
OWNERSHIP_LABELS = {
    "owned-by-a-public-authority": "Public authority",
    "not-owned-by-a-public-authority": "Private",
    "mixed-ownership": "Mixed",
    "unknown-ownership": "Not stated",
}


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_sites(entities: list[dict], lat: float, lon: float, radius_m: int = RADIUS_M) -> list[dict]:
    """The platform's entities as rows for the page: within the radius,
    nearest first, closed entries (an end date: developed or withdrawn)
    left out."""
    sites = []
    for e in entities or []:
        if e.get("end-date"):
            continue
        m = _POINT.match(e.get("point") or "")
        if not m:
            continue
        site_lon, site_lat = float(m.group(1)), float(m.group(2))
        distance = _distance_m(lat, lon, site_lat, site_lon)
        if distance > radius_m:
            continue
        status = (e.get("planning-permission-status") or "").strip()
        sites.append({
            "reference": e.get("reference") or e.get("name") or "",
            "address": e.get("site-address") or e.get("name") or "Unnamed site",
            "distance_m": int(round(distance)),
            "hectares": _float(e.get("hectares")),
            "min_dwellings": _int(e.get("minimum-net-dwellings")),
            "max_dwellings": _int(e.get("maximum-net-dwellings")),
            "permission": PERMISSION_LABELS.get(status, status.replace("-", " ").capitalize() if status else "Not stated"),
            "permission_type": (e.get("planning-permission-type") or "").replace("-", " "),
            "ownership": OWNERSHIP_LABELS.get(e.get("ownership-status") or "", "Not stated"),
            "deliverable": (e.get("deliverable") or "").lower() == "yes",
            "entry_date": e.get("entry-date") or e.get("start-date") or "",
            "plan_url": e.get("site-plan-url") or "",
            "lat": site_lat,
            "lon": site_lon,
        })
    sites.sort(key=lambda s: s["distance_m"])
    return sites


def summarise(sites: list[dict], council: dict | None, register_count: int | None) -> dict:
    return {
        "covered": True,
        "radius_m": RADIUS_M,
        "sites": sites,
        "count": len(sites),
        "hectares": round(sum(s["hectares"] or 0 for s in sites), 2),
        "dwellings": sum((s["max_dwellings"] or s["min_dwellings"] or 0) for s in sites),
        "dwellings_stated": sum(1 for s in sites if s["max_dwellings"] or s["min_dwellings"]),
        "permissioned": sum(1 for s in sites if s["permission"] == "Has planning permission"),
        "newest_entry": max((s["entry_date"] for s in sites), default=""),
        "council": {
            "name": council["name"],
            "entity": council["entity"],
            "register_count": register_count,
            "published": bool(register_count),
        } if council else None,
    }


def bounding_polygon(lat: float, lon: float, radius_m: int = RADIUS_M) -> str:
    """A square a little larger than the radius, as WKT the platform
    accepts; the exact circle is applied afterwards in parse_sites."""
    dlat = radius_m / 111_320 * 1.1
    dlon = dlat / max(math.cos(math.radians(lat)), 0.2)
    corners = [(lon - dlon, lat - dlat), (lon + dlon, lat - dlat), (lon + dlon, lat + dlat), (lon - dlon, lat + dlat), (lon - dlon, lat - dlat)]
    return "POLYGON((" + ",".join(f"{x:.6f} {y:.6f}" for x, y in corners) + "))"


async def sites_near(lat: float, lon: float, admin_district_code: str = "", country: str = "England") -> dict:
    """Register sites within RADIUS_M of the point, plus the council's
    standing on the platform. Raises httpx.HTTPError when the platform
    does not answer, so the card can say the data was unavailable rather
    than "none nearby"."""
    if country and country != "England":
        return {"covered": False, "country": country}
    key = _cache.coord_key("brownfield", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached

    council = ORGANISATIONS.get(admin_district_code or "")
    register_count = None
    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(ENTITY_URL, params={
            "dataset": DATASET,
            "geometry": bounding_polygon(lat, lon),
            "geometry_relation": "intersects",
            "limit": 200,
        })
        response.raise_for_status()
        entities = response.json().get("entities", [])
        if council:
            counted = await client.get(ENTITY_URL, params={
                "dataset": DATASET, "organisation_entity": council["entity"], "limit": 1,
            })
            if counted.status_code == 200:
                register_count = counted.json().get("count")

    out = summarise(parse_sites(entities, lat, lon), council, register_count)
    _cache.set(key, out)
    return out
