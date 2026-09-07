"""GP practices near a point and the A&E performance of the trusts in
its integrated care board, from the tables scripts/import_health_services.py
fills (NHS England Digital list sizes and workforce, NHS England A&E
statistics, Open Government Licence).

"Patients per fully qualified GP" divides a practice's list by its fully
qualified GP full-time equivalents (trainees excluded, the national
headline measure). Trainees and locums do see patients, so the page shows
the all-GP figure beside it. The national median comes from the same
import, so the comparison is like for like.
"""
import json
import math
import pathlib
import re

from sqlalchemy import select

from app import db
from app.models import AeTrust, GpPractice
from app.services import _cache

RADIUS_M = 3000
MAX_PRACTICES = 3
MAX_TRUSTS = 5
_FLAG_TTL_S = 3600
_CONTEXT_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "health_context.json"


def context() -> dict:
    try:
        return json.loads(_CONTEXT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _distance_m(lat1, lon1, lat2, lon2) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi, dlam = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def period_label(raw: str | None) -> str:
    """"MSitAE-JULY-2026" as "July 2026"."""
    text = re.sub(r"^MSitAE-", "", raw or "").replace("-", " ").strip()
    return text.title()


_SMALL_WORDS = {"and", "of", "the", "at", "in", "on", "for", "by"}
_ACRONYMS = {"nhs", "gp", "uk", "pcn", "icb"}


def tidy_name(text: str | None) -> str:
    """NHS files shout or title-case blindly: KING'S COLLEGE HOSPITAL NHS
    FOUNDATION TRUST, or Guy'S And St Thomas'. Case each word once,
    apostrophes and acronyms included."""
    words = []
    for i, word in enumerate((text or "").lower().split()):
        if word in _ACRONYMS:
            words.append(word.upper())
        elif word in _SMALL_WORDS and i > 0:
            words.append(word)
        else:
            words.append("-".join(part[:1].upper() + part[1:] for part in word.split("-")))
    return " ".join(words)


def _pct_within(over: int, attendances: int) -> float | None:
    if not attendances:
        return None
    return round(100 * (1 - over / attendances), 1)


def _table_has_rows() -> bool:
    flag = _cache.get("gp_practices_populated", _FLAG_TTL_S)
    if flag is not None:
        return flag
    with db.get_session() as session:
        populated = session.execute(select(GpPractice.code).limit(1)).first() is not None
    _cache.set("gp_practices_populated", populated)
    return populated


def practice_row(row: GpPractice, distance: float, median: int | None) -> dict:
    fte = row.qualified_gp_fte
    per_gp = round(row.patients / fte) if fte and fte >= 0.5 and row.patients else None
    return {
        "code": row.code,
        "name": tidy_name(row.name),
        "postcode": row.postcode,
        "distance_m": int(round(distance)),
        "patients": row.patients,
        "gp_fte": round(row.gp_fte, 1) if row.gp_fte is not None else None,
        "qualified_gp_fte": round(fte, 1) if fte is not None else None,
        "patients_per_qualified_gp": per_gp,
        "vs_median": round(per_gp / median, 2) if per_gp and median else None,
        "gp_source": row.gp_source,
        "estimated": bool(row.gp_source) and not row.gp_source.startswith("Fully provided"),
        "pcn_name": tidy_name(row.pcn_name),
        "icb_code": row.icb_code,
        "icb_name": row.icb_name,
    }


def near(lat: float, lon: float, radius_m: int = RADIUS_M) -> dict | None:
    """None until the tables are filled. Otherwise the nearest practices
    with a list size, the median they compare against, and the Type 1
    A&E providers in the nearest practice's integrated care board."""
    if not db.is_configured() or not _table_has_rows():
        return None
    ctx = context()
    median = ctx.get("median_patients_per_qualified_gp")
    box_lat = radius_m / 111_320 * 1.05
    box_lon = box_lat / max(math.cos(math.radians(lat)), 0.2)
    with db.get_session() as session:
        rows = session.execute(
            select(GpPractice).where(
                GpPractice.latitude.between(lat - box_lat, lat + box_lat),
                GpPractice.longitude.between(lon - box_lon, lon + box_lon),
                GpPractice.patients > 0,
            )
        ).scalars().all()
        practices = []
        for row in rows:
            distance = _distance_m(lat, lon, row.latitude, row.longitude)
            if distance <= radius_m:
                practices.append(practice_row(row, distance, median))
        practices.sort(key=lambda p: p["distance_m"])
        practices = practices[:MAX_PRACTICES]
        trusts = []
        icb_code = practices[0]["icb_code"] if practices else ""
        if icb_code:
            for t in session.execute(select(AeTrust).where(AeTrust.icb_code == icb_code)).scalars().all():
                trusts.append({
                    "org_code": t.org_code, "name": tidy_name(t.name), "period": period_label(t.period),
                    "type1_attendances": t.type1_attendances,
                    "type1_within_4h_pct": _pct_within(t.type1_over_4h, t.type1_attendances),
                    "all_within_4h_pct": _pct_within(t.all_over_4h, t.all_attendances),
                })
    trusts.sort(key=lambda t: -t["type1_attendances"])
    nearest = practices[0] if practices else None
    return {
        "radius_m": radius_m,
        "practices": practices,
        "count": len(practices),
        "nearest": nearest,
        "median_patients_per_qualified_gp": median,
        "patients_date": ctx.get("patients_date"),
        "workforce_date": ctx.get("workforce_date"),
        "icb_code": icb_code,
        "icb_name": practices[0]["icb_name"] if practices else "",
        "trusts": trusts[:MAX_TRUSTS],
        "ae_period": period_label(ctx.get("ae_period")),
        "national_type1_within_4h_pct": ctx.get("national_type1_within_4h_pct"),
        "pressure": (nearest["vs_median"] if nearest else None),
    }
