"""How a small area changed between the 2011 and 2021 censuses: tenure,
age, qualifications, country of birth, health and ethnic group, as
shares, with England's own change beside each for scale.

2011 comes from scripts/import_census_2011.py (ONS Key Statistics,
re-keyed to 2021 LSOA codes with the ONS best-fit lookup); 2021 from the
topic summary tables the report already uses. Where a 2021 area was
formed by merging 2011 areas the 2011 counts are summed; where a 2011
area was split, only one child carries its figures, and the other says
it has no comparable 2011 figure. Everything is a share of the area's
own residents or households, so a growing area and a shrinking one
compare fairly.
"""
import json
import pathlib

from app import db
from app.models import AgeProfile, Census2011, CountryOfBirth, Ethnicity, GeneralHealth, Qualification, Tenure

_CONTEXT_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "census_change_context.json"
_CONTEXT: dict | None = None

# key, label, numerator, denominator (both years use the same names)
MEASURES = [
    ("owned", "Households that own their home", "owned", "households"),
    ("private_rented", "Households renting privately", "private_rented", "households"),
    ("social_rented", "Households renting socially", "social_rented", "households"),
    ("under_15", "Residents under 15", "under_15", "residents"),
    ("over_65", "Residents 65 and over", "over_65", "residents"),
    ("level_4_plus", "Adults with a degree or equivalent", "level_4_plus", "adults"),
    ("born_outside_uk", "Residents born outside the UK", None, "cob_total"),
    ("health_good", "Residents in good or very good health", "health_good", "health_total"),
    ("white", "Residents in a white ethnic group", "white", "eth_total"),
]
SHORT = {
    "owned": "Owner-occupiers", "private_rented": "Private renting", "social_rented": "Social renting",
    "under_15": "Under-15s", "over_65": "Over-65s", "level_4_plus": "Degree-level adults",
    "born_outside_uk": "Born outside the UK", "health_good": "Good health", "white": "White residents",
}


def context() -> dict:
    global _CONTEXT
    if _CONTEXT is None:
        try:
            _CONTEXT = json.loads(_CONTEXT_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _CONTEXT = {}
    return _CONTEXT


def _pct(num, den) -> float | None:
    if not den:
        return None
    return round(100 * num / den, 1)


def _shares(counts: dict) -> dict[str, float | None]:
    out = {}
    for key, _, num_key, den_key in MEASURES:
        if key == "born_outside_uk":
            out[key] = _pct(counts.get("cob_total", 0) - counts.get("born_uk", 0), counts.get("cob_total", 0))
        else:
            out[key] = _pct(counts.get(num_key, 0), counts.get(den_key, 0))
    return out


def _counts_2021(session, lsoa: str) -> dict | None:
    tenure = session.get(Tenure, lsoa)
    age = session.get(AgeProfile, lsoa)
    qual = session.get(Qualification, lsoa)
    cob = session.get(CountryOfBirth, lsoa)
    health = session.get(GeneralHealth, lsoa)
    eth = session.get(Ethnicity, lsoa)
    if not any((tenure, age, qual, cob, health, eth)):
        return None
    return {
        "households": tenure.total if tenure else 0,
        "owned": (tenure.owned_outright + tenure.owned_mortgage) if tenure else 0,
        "private_rented": tenure.private_rented if tenure else 0,
        "social_rented": tenure.social_rented if tenure else 0,
        "residents": age.total if age else 0,
        "under_15": age.under_15 if age else 0,
        "over_65": (age.age_65_84 + age.age_85_plus) if age else 0,
        "adults": qual.total if qual else 0,
        "level_4_plus": qual.level_4_plus if qual else 0,
        "cob_total": cob.total if cob else 0,
        "born_uk": cob.uk if cob else 0,
        "health_total": health.total if health else 0,
        "health_good": (health.very_good + health.good) if health else 0,
        "eth_total": eth.total if eth else 0,
        "white": eth.white if eth else 0,
    }


def for_lsoa(lsoa: str) -> dict | None:
    """None when nothing is known for the area; otherwise the measures
    with both years, the change in percentage points, and England's."""
    if not lsoa or not db.is_configured():
        return None
    with db.get_session() as session:
        old = session.get(Census2011, lsoa)
        new = _counts_2021(session, lsoa)
        old_counts = {k: getattr(old, k) for k in (
            "households", "owned", "private_rented", "social_rented", "residents", "under_15", "over_65",
            "adults", "level_4_plus", "cob_total", "born_uk", "health_total", "health_good", "eth_total", "white",
        )} if old else None
        merged_from = old.lsoa11_count if old else 0
    if not new and not old_counts:
        return None
    ctx = context()
    eng_2011 = _shares(ctx.get("england_2011") or {})
    eng_2021 = _shares(ctx.get("england_2021") or {})
    s_old = _shares(old_counts) if old_counts else {}
    s_new = _shares(new) if new else {}
    rows = []
    for key, label, _, _ in MEASURES:
        a, b = s_old.get(key), s_new.get(key)
        e_a, e_b = eng_2011.get(key), eng_2021.get(key)
        rows.append({
            "key": key, "label": label, "short": SHORT[key], "in_2011": a, "in_2021": b,
            "change": round(b - a, 1) if a is not None and b is not None else None,
            "england_2011": e_a, "england_2021": e_b,
            "england_change": round(e_b - e_a, 1) if e_a is not None and e_b is not None else None,
        })
    movers = sorted([r for r in rows if r["change"] is not None], key=lambda r: -abs(r["change"]))
    residents_2011 = old_counts["residents"] if old_counts else None
    residents_2021 = new["residents"] if new else None
    return {
        "lsoa": lsoa,
        "rows": rows,
        "biggest": movers[0] if movers else None,
        "residents_2011": residents_2011,
        "residents_2021": residents_2021,
        "residents_change_pct": round(100 * (residents_2021 / residents_2011 - 1), 1) if residents_2011 and residents_2021 else None,
        "has_2011": bool(old_counts),
        "has_2021": bool(new),
        "merged_from": merged_from,
    }
