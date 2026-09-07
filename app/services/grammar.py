"""England's state grammar schools: the 163 state-funded secondary
schools whose admissions policy on the Department for Education's
register (Get Information About Schools) is "Selective", with the
admission distance the council published where we hold one, and the
official familiarisation materials for the tests they set.

What the address decides: a grammar school admits by test first, then
by its oversubscription criteria, and where a school publishes a
"last distance offered" that figure applies after the pass mark. We
link the official practice papers and set no tests of our own.
"""
import math

from sqlalchemy import select

from app import db
from app.models import School, SchoolAdmissionRadius, SchoolDetail
from app.services import _cache
from app.services.schools_db import _slugify

CACHE_TTL_S = 86400
RADIUS_KM = 16.0  # about ten miles
MAX_NEAR = 8

# Where the tests' own free familiarisation papers live. Three sources
# cover nearly every area; the school's or council's admissions page
# says which test it uses and when to register.
SOURCES = [
    {"name": "GL Assessment 11+ familiarisation materials",
     "url": "https://11plus.gl-assessment.co.uk/pages/free-materials",
     "note": "GL Assessment sets the 11+ for most consortia and councils; its free familiarisation papers cover verbal reasoning, non-verbal reasoning, English and maths."},
    {"name": "CSSE, the Consortium for Selective Schools in Essex",
     "url": "https://csse.org.uk/examination/",
     "note": "The 11+ for the Essex and Southend grammar schools, with free familiarisation papers on the examination page."},
    {"name": "The Kent Test, Kent County Council",
     "url": "https://www.kent.gov.uk/education-and-children/schools/school-places/kent-test",
     "note": "Kent's own test for its 32 grammar schools, with the familiarisation booklet, registration dates and how places are then offered."},
]


def _state_secondary(school: School) -> bool:
    return school.phase in ("Secondary", "All-through") and "independent" not in (school.type_name or "").lower()


def _row(school: School, detail: SchoolDetail, radius: SchoolAdmissionRadius | None, distance_m: int | None = None) -> dict:
    return {
        "urn": school.urn,
        "name": school.name,
        "slug": _slugify(school.name),
        "council": detail.local_authority or "",
        "town": detail.town or "",
        "postcode": school.postcode,
        "gender": detail.gender or "",
        "type": school.type_name,
        "ofsted_rating_label": school.ofsted_rating_label or "",
        "ofsted_note": school.ofsted_note or "",
        "website": detail.website or "",
        "latitude": school.latitude,
        "longitude": school.longitude,
        "last_distance_miles": radius.last_distance_miles if radius else None,
        "distance_year": radius.academic_year if radius else "",
        "distance_source": radius.source_authority if radius else "",
        "distance_m": distance_m,
    }


def _query(session, where=()):
    stmt = (
        select(School, SchoolDetail, SchoolAdmissionRadius)
        .join(SchoolDetail, SchoolDetail.urn == School.urn)
        .outerjoin(SchoolAdmissionRadius, SchoolAdmissionRadius.urn == School.urn)
        .where(SchoolDetail.admissions_policy == "Selective", *where)
    )
    return [(s, d, r) for s, d, r in session.execute(stmt).all() if _state_secondary(s)]


def all_grammar_schools() -> list[dict]:
    """Every state grammar school, by council then name; cached a day."""
    cached = _cache.get(("grammar_schools",), CACHE_TTL_S)
    if cached is not None:
        return cached
    if not db.is_configured():
        return []
    with db.get_session() as session:
        rows = [_row(s, d, r) for s, d, r in _query(session)]
    rows.sort(key=lambda r: (r["council"], r["name"]))
    _cache.set(("grammar_schools",), rows)
    return rows


def by_council(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["council"] or "Council not recorded", []).append(r)
    return sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def _distance_m(lat1, lon1, lat2, lon2) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlam = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def schools_near(lat: float, lon: float, radius_km: float = RADIUS_KM, limit: int = MAX_NEAR) -> list[dict]:
    """State grammar schools within about ten miles, nearest first."""
    if not db.is_configured():
        return []
    box_lat = radius_km / 111.32 * 1.05
    box_lon = box_lat / max(math.cos(math.radians(lat)), 0.2)
    with db.get_session() as session:
        found = _query(session, (
            School.latitude.between(lat - box_lat, lat + box_lat),
            School.longitude.between(lon - box_lon, lon + box_lon),
        ))
        rows = []
        for s, d, r in found:
            distance = _distance_m(lat, lon, s.latitude, s.longitude)
            if distance <= radius_km * 1000:
                rows.append(_row(s, d, r, int(round(distance))))
    rows.sort(key=lambda r: r["distance_m"])
    return rows[:limit]
