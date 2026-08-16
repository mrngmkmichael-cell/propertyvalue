"""Nearby schools with Ofsted ratings, from our own `schools` table
(populated offline by scripts/import_schools.py) rather than a live
API - see that script for why: Ofsted ratings aren't available from
any live free API, only a monthly bulk file that needs joining
against DfE's separate school-establishment data by hand.
"""
import math

from sqlalchemy import select

from app.db import get_session, is_configured
from app.models import Ks2Result, Ks4Result, School, SchoolCharacteristics

SEARCH_RADIUS_KM = 5
PER_GROUP_LIMIT = 3
# Rough degrees-per-km at UK latitudes, generous enough for a first-pass
# bounding box before the precise haversine distance filter/sort below.
DEG_PER_KM = 1 / 111

GROUP_ORDER = ["Nursery", "Primary", "Secondary"]


def _phase_group(phase: str) -> str | None:
    """Collapses GIAS's ~8 PhaseOfEducation values down to the three
    groups parents actually think in terms of. 'All-through' and
    '16 plus' schools get folded into Secondary rather than added as
    a fourth group, since the ask was specifically Nursery/Primary/
    Secondary."""
    p = (phase or "").lower()
    if "nursery" in p:
        return "Nursery"
    if "primary" in p:
        return "Primary"
    if "secondary" in p or "16 plus" in p or "all-through" in p or "all through" in p:
        return "Secondary"
    return None


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearby_schools(lat: float, lon: float) -> dict[str, list[dict]]:
    """Nearest schools grouped into Nursery/Primary/Secondary, up to
    PER_GROUP_LIMIT each. Groups with no results nearby are omitted
    rather than shown empty."""
    grouped: dict[str, list[dict]] = {name: [] for name in GROUP_ORDER}
    if not is_configured():
        return grouped

    box = SEARCH_RADIUS_KM * DEG_PER_KM
    with get_session() as session:
        rows = session.scalars(
            select(School).where(
                School.latitude.between(lat - box, lat + box),
                School.longitude.between(lon - box, lon + box),
            )
        ).all()

        candidates = []
        for row in rows:
            group = _phase_group(row.phase)
            if group is None:
                continue
            distance_km = _haversine_km(lat, lon, row.latitude, row.longitude)
            if distance_km > SEARCH_RADIUS_KM:
                continue
            candidates.append({
                "urn": row.urn,
                "group": group,
                "name": row.name,
                "phase": row.phase,
                "type": row.type_name,
                "distance_m": round(distance_km * 1000),
                "ofsted_rating": row.ofsted_rating,
                "ofsted_rating_label": row.ofsted_rating_label,
                "ofsted_inspection_date": row.ofsted_inspection_date,
            })

        candidates.sort(key=lambda s: s["distance_m"])
        for school in candidates:
            group = school["group"]
            if len(grouped[group]) < PER_GROUP_LIMIT:
                grouped[group].append(school)

        secondary_urns = [s["urn"] for s in grouped["Secondary"]]
        if secondary_urns:
            ks4_by_urn = {
                r.urn: r for r in session.scalars(select(Ks4Result).where(Ks4Result.urn.in_(secondary_urns)))
            }
            for school in grouped["Secondary"]:
                r = ks4_by_urn.get(school["urn"])
                school["exam_results"] = {
                    "academic_year": r.academic_year,
                    "headline_label": "Progress 8",
                    "headline_value": r.progress8_score,
                    "grade5_english_maths_pct": r.grade5_english_maths_pct,
                } if r else None

        primary_urns = [s["urn"] for s in grouped["Primary"]]
        if primary_urns:
            ks2_by_urn = {
                r.urn: r for r in session.scalars(select(Ks2Result).where(Ks2Result.urn.in_(primary_urns)))
            }
            for school in grouped["Primary"]:
                r = ks2_by_urn.get(school["urn"])
                school["exam_results"] = {
                    "academic_year": r.academic_year,
                    "headline_label": "Meeting expected standard",
                    "headline_value": r.rwm_expected_pct,
                    "grade5_english_maths_pct": None,
                } if r else None

        for school in grouped["Nursery"]:
            school["exam_results"] = None

        all_urns = [s["urn"] for schools in grouped.values() for s in schools]
        if all_urns:
            fsm_by_urn = {
                r.urn: r.fsm_eligible_pct
                for r in session.scalars(
                    select(SchoolCharacteristics).where(SchoolCharacteristics.urn.in_(all_urns))
                )
            }
            for schools in grouped.values():
                for school in schools:
                    school["fsm_eligible_pct"] = fsm_by_urn.get(school["urn"])

    return {name: schools for name, schools in grouped.items() if schools}
