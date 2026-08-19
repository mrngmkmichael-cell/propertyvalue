"""Nearby schools with Ofsted ratings, from our own `schools` table
(populated offline by scripts/import_schools.py) rather than a live
API - see that script for why: Ofsted ratings aren't available from
any live free API, only a monthly bulk file that needs joining
against DfE's separate school-establishment data by hand.
"""
import math

from sqlalchemy import func, select

from app.db import get_session, is_configured
from app.models import (
    Ks2Result, Ks4Result, School, SchoolAdmissionRadius, SchoolCatchmentEstimate, SchoolCharacteristics,
    SchoolDemographics, SchoolDestinations, SchoolDetail,
)
from app.services import _cache, overview_score, reviews

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


def _latest_and_trend(rows) -> tuple[dict[int, object], dict[int, list]]:
    """Splits a list of Ks4Result/Ks2Result rows (now one per school
    *per year* since they're keyed on (urn, academic_year)) into the
    latest row per URN - for the existing "current" fields - plus the
    full year-ordered list per URN, for a trend view. academic_year
    sorts correctly as a plain string ("2022/23" < "2023/24" < ...)
    since all years use the same YYYY/YY format."""
    by_urn: dict[int, list] = {}
    for r in rows:
        by_urn.setdefault(r.urn, []).append(r)
    trend_by_urn = {urn: sorted(rs, key=lambda r: r.academic_year) for urn, rs in by_urn.items()}
    latest_by_urn = {urn: rs[-1] for urn, rs in trend_by_urn.items()}
    return latest_by_urn, trend_by_urn


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
            ks4_by_urn, _ = _latest_and_trend(
                session.scalars(select(Ks4Result).where(Ks4Result.urn.in_(secondary_urns)))
            )
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
            ks2_by_urn, _ = _latest_and_trend(
                session.scalars(select(Ks2Result).where(Ks2Result.urn.in_(primary_urns)))
            )
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


RATING_ORDER = ["Outstanding", "Good", "Requires improvement", "Inadequate", "No current grade"]


def school_landscape(lat: float, lon: float) -> dict | None:
    """Aggregate counts for the local area, rather than a list of
    individual schools - answers "is this a good area for schools"
    rather than "which specific school". Same SEARCH_RADIUS_KM as
    nearby_schools() so the two stay consistent with each other.

    Special schools are counted separately from the Nursery/Primary/
    Secondary phase breakdown: GIAS records almost all of them with
    phase="Not applicable" rather than a real phase, so they'd
    otherwise vanish from a phase-only breakdown entirely. Higher
    education (universities) and further education (sixth-form/FE
    colleges) are also GIAS establishment types, just not schools in
    the phase-of-education sense, so they're counted separately too
    rather than folded into "school" totals.
    """
    if not is_configured():
        return None

    box = SEARCH_RADIUS_KM * DEG_PER_KM
    with get_session() as session:
        rows = session.scalars(
            select(School).where(
                School.latitude.between(lat - box, lat + box),
                School.longitude.between(lon - box, lon + box),
            )
        ).all()

    by_rating = {label: 0 for label in RATING_ORDER}
    by_phase = {name: 0 for name in GROUP_ORDER}
    schools_by_rating = {label: [] for label in RATING_ORDER}
    schools_by_phase = {name: [] for name in GROUP_ORDER}
    special_count = 0
    special_schools = []
    further_education = 0
    higher_education_names = []

    for row in rows:
        distance_km = _haversine_km(lat, lon, row.latitude, row.longitude)
        if distance_km > SEARCH_RADIUS_KM:
            continue

        type_lower = (row.type_name or "").lower()
        if type_lower == "higher education institutions":
            higher_education_names.append(row.name)
            continue  # not Ofsted-rated, not part of the school counts below
        if type_lower == "further education":
            further_education += 1
            continue

        entry = {
            "urn": row.urn, "name": row.name, "type": row.type_name,
            "distance_m": round(distance_km * 1000), "phase_group": None,
            "ofsted_rating": row.ofsted_rating, "ofsted_rating_label": row.ofsted_rating_label,
            "ofsted_inspection_date": row.ofsted_inspection_date,
            "latitude": row.latitude, "longitude": row.longitude,
        }

        if "special" in type_lower:
            special_count += 1
            special_schools.append(entry)
        else:
            group = _phase_group(row.phase)
            if not group:
                continue  # e.g. "Offshore schools", "Miscellaneous" - not a recognised phase, excluded entirely rather than counted in ratings but not in by_phase
            entry["phase_group"] = group
            by_phase[group] += 1
            schools_by_phase[group].append(entry)

        # Ofsted's 2024 reform moved many inspections to an ungraded
        # report-card format with no overall 1-4 grade - "no rating"
        # here often means recently inspected under the new system,
        # not "never inspected", so the label has to stay honest
        # about that rather than implying Ofsted hasn't visited.
        label = row.ofsted_rating_label or "No current grade"
        by_rating[label] += 1
        schools_by_rating[label].append(entry)

    total_schools = sum(by_phase.values()) + special_count
    if total_schools == 0 and not higher_education_names and further_education == 0:
        return None

    # Every included school appears in exactly one rating bucket, so
    # that's the complete set to enrich - one batched IN-query each
    # rather than a query per school, same pattern as nearby_schools().
    all_entries = [e for bucket in schools_by_rating.values() for e in bucket]
    all_urns = [e["urn"] for e in all_entries]
    if all_urns:
        with get_session() as session:
            ks4_by_urn, ks4_trend_by_urn = _latest_and_trend(
                session.scalars(select(Ks4Result).where(Ks4Result.urn.in_(all_urns)))
            )
            ks2_by_urn, ks2_trend_by_urn = _latest_and_trend(
                session.scalars(select(Ks2Result).where(Ks2Result.urn.in_(all_urns)))
            )
            fsm_by_urn = {
                r.urn: r.fsm_eligible_pct
                for r in session.scalars(select(SchoolCharacteristics).where(SchoolCharacteristics.urn.in_(all_urns)))
            }
            detail_by_urn = {
                r.urn: r for r in session.scalars(select(SchoolDetail).where(SchoolDetail.urn.in_(all_urns)))
            }
            destinations_by_urn = {
                r.urn: r for r in session.scalars(select(SchoolDestinations).where(SchoolDestinations.urn.in_(all_urns)))
            }
            admission_radius_by_urn = {
                # round() here, not at each display site - some authorities'
                # fetchers store an unrounded metres/miles conversion
                # (e.g. Hertfordshire), which otherwise displays as
                # "3.4260877129755056 mi" instead of "3.43 mi".
                r.urn: {
                    "last_distance_miles": round(r.last_distance_miles, 2), "academic_year": r.academic_year,
                    "source_authority": r.source_authority,
                }
                for r in session.scalars(select(SchoolAdmissionRadius).where(SchoolAdmissionRadius.urn.in_(all_urns)))
            }
            catchment_estimate_by_urn = {
                r.urn: {"radius_miles": r.radius_miles}
                for r in session.scalars(select(SchoolCatchmentEstimate).where(SchoolCatchmentEstimate.urn.in_(all_urns)))
            }
            demographics_by_urn = {
                r.urn: r for r in session.scalars(select(SchoolDemographics).where(SchoolDemographics.urn.in_(all_urns)))
            }
        review_summaries = reviews.summaries_for_many("school", [str(urn) for urn in all_urns])
        for e in all_entries:
            e["fsm_eligible_pct"] = fsm_by_urn.get(e["urn"])
            e["demographics"] = demographics_by_urn.get(e["urn"])
            e["detail"] = detail_by_urn.get(e["urn"])
            e["destinations"] = destinations_by_urn.get(e["urn"])
            e["review_summary"] = review_summaries.get(str(e["urn"]))
            e["admission_radius"] = admission_radius_by_urn.get(e["urn"])
            # Modelled fallback only where there's no real published
            # figure - never shown alongside/instead of real data.
            if not e["admission_radius"]:
                e["catchment_estimate"] = catchment_estimate_by_urn.get(e["urn"])
            if e["phase_group"] == "Secondary" and e["urn"] in ks4_by_urn:
                r = ks4_by_urn[e["urn"]]
                e["exam_results"] = {
                    "academic_year": r.academic_year, "headline_label": "Progress 8",
                    "headline_value": r.progress8_score, "grade5_english_maths_pct": r.grade5_english_maths_pct,
                    "pupil_count": r.pupil_count,
                }
                e["exam_trend"] = [
                    {"academic_year": t.academic_year, "headline_value": t.progress8_score,
                     "grade5_english_maths_pct": t.grade5_english_maths_pct}
                    for t in ks4_trend_by_urn[e["urn"]]
                ]
            elif e["phase_group"] == "Primary" and e["urn"] in ks2_by_urn:
                r = ks2_by_urn[e["urn"]]
                e["exam_results"] = {
                    "academic_year": r.academic_year, "headline_label": "Meeting expected standard",
                    "headline_value": r.rwm_expected_pct, "grade5_english_maths_pct": None,
                    "pupil_count": r.pupil_count,
                }
                e["exam_trend"] = [
                    {"academic_year": t.academic_year, "headline_value": t.rwm_expected_pct,
                     "grade5_english_maths_pct": None}
                    for t in ks2_trend_by_urn[e["urn"]]
                ]
            else:
                e["exam_results"] = None
                e["exam_trend"] = []
            e["verdict"] = overview_score.school_verdict(e)

    higher_education_names.sort()
    # Deliberately not "ofsted-1"/"ofsted-2" etc - those bare class
    # names are already used for the small inline Ofsted badge in the
    # plain school table (.ofsted-1 { background: ... }), and being
    # unscoped, they'd match this chip too and paint the whole
    # expandable container solid green - a real bug caught by testing.
    rating_css = {
        "Outstanding": "rating-outstanding", "Good": "rating-good",
        "Requires improvement": "rating-reqimprovement", "Inadequate": "rating-inadequate",
        "No current grade": "rating-none",
    }
    graded = total_schools - by_rating["No current grade"]
    good_or_better_pct = (
        round((by_rating["Outstanding"] + by_rating["Good"]) / graded * 100) if graded else None
    )
    for bucket in schools_by_rating.values():
        bucket.sort(key=lambda s: s["distance_m"])
    for bucket in schools_by_phase.values():
        bucket.sort(key=lambda s: s["distance_m"])
    special_schools.sort(key=lambda s: s["distance_m"])

    return {
        "radius_km": SEARCH_RADIUS_KM,
        "total_schools": total_schools,
        "good_or_better_pct": good_or_better_pct,
        "by_rating": [
            {"label": label, "count": by_rating[label], "css": rating_css[label], "schools": schools_by_rating[label]}
            for label in RATING_ORDER if by_rating[label]
        ],
        "by_phase": [
            {"label": name, "count": by_phase[name], "schools": schools_by_phase[name]}
            for name in GROUP_ORDER if by_phase[name]
        ],
        "special_count": special_count,
        "special_schools": special_schools,
        "further_education": further_education,
        "higher_education_count": len(higher_education_names),
        "higher_education_names": higher_education_names,
        # Every included school has exactly one rating bucket, so this
        # is already the complete deduplicated set - used to render
        # each school's detail modal exactly once, even though the
        # same school can appear in both its rating list and its
        # phase list.
        "all_schools": all_entries,
    }


NATIONAL_BASELINE_CACHE_KEY = "schools_national_baseline"
NATIONAL_BASELINE_TTL_S = 86400  # a full table scan - the underlying data only changes on a periodic re-import


def national_baseline() -> dict | None:
    """% of graded schools nationally rated Outstanding or Good - the
    context a single area's numbers need to actually mean something
    ("38% Outstanding/Good" only tells a parent anything next to the
    national figure). Excludes higher/further education (not
    Ofsted-rated the same way) but otherwise counts every school with
    a current grade, cached for a day since this is a full-table
    aggregate."""
    if not is_configured():
        return None
    cached = _cache.get(NATIONAL_BASELINE_CACHE_KEY, NATIONAL_BASELINE_TTL_S)
    if cached is not None:
        return cached

    with get_session() as session:
        rows = session.execute(
            select(School.ofsted_rating, func.count()).where(School.ofsted_rating.is_not(None)).group_by(School.ofsted_rating)
        ).all()

    by_rating = dict(rows)
    total = sum(by_rating.values())
    if not total:
        return None
    good_or_better = by_rating.get(1, 0) + by_rating.get(2, 0)
    result = {"good_or_better_pct": round(good_or_better / total * 100), "total_graded": total}
    _cache.set(NATIONAL_BASELINE_CACHE_KEY, result)
    return result
