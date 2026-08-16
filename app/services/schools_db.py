"""Nearby schools with Ofsted ratings, from our own `schools` table
(populated offline by scripts/import_schools.py) rather than a live
API - see that script for why: Ofsted ratings aren't available from
any live free API, only a monthly bulk file that needs joining
against DfE's separate school-establishment data by hand.
"""
import math

from sqlalchemy import select

from app.db import get_session, is_configured
from app.models import School

SEARCH_RADIUS_KM = 3
# Rough degrees-per-km at UK latitudes, generous enough for a first-pass
# bounding box before the precise haversine distance filter/sort below.
DEG_PER_KM = 1 / 111


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearby_schools(lat: float, lon: float, limit: int = 10) -> list[dict]:
    if not is_configured():
        return []

    box = SEARCH_RADIUS_KM * DEG_PER_KM
    with get_session() as session:
        rows = session.scalars(
            select(School).where(
                School.latitude.between(lat - box, lat + box),
                School.longitude.between(lon - box, lon + box),
            )
        ).all()

        schools = []
        for row in rows:
            distance_km = _haversine_km(lat, lon, row.latitude, row.longitude)
            if distance_km > SEARCH_RADIUS_KM:
                continue
            schools.append({
                "name": row.name,
                "phase": row.phase,
                "type": row.type_name,
                "distance_m": round(distance_km * 1000),
                "ofsted_rating": row.ofsted_rating,
                "ofsted_rating_label": row.ofsted_rating_label,
                "ofsted_inspection_date": row.ofsted_inspection_date,
            })

    schools.sort(key=lambda s: s["distance_m"])
    return schools[:limit]
