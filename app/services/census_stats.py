"""Occupation and qualification breakdowns from our own
`occupation`/`qualification` tables (Census 2021 data, populated
offline by scripts/import_census_stats.py) - keyed by LSOA code,
same lookup pattern as app/services/area_stats.py.
"""
from app.db import get_session, is_configured
from app.models import Occupation, Qualification

OCCUPATION_LABELS = [
    ("managers_directors_senior", "Managers, directors and senior officials"),
    ("professional", "Professional occupations"),
    ("associate_professional_technical", "Associate professional and technical"),
    ("admin_secretarial", "Administrative and secretarial"),
    ("skilled_trades", "Skilled trades"),
    ("caring_leisure_service", "Caring, leisure and other service"),
    ("sales_customer_service", "Sales and customer service"),
    ("process_plant_machine_operatives", "Process, plant and machine operatives"),
    ("elementary", "Elementary occupations"),
]

QUALIFICATION_LABELS = [
    ("level_4_plus", "Level 4+ (degree or above)"),
    ("level_3", "Level 3 (A-levels or equivalent)"),
    ("apprenticeship", "Apprenticeship"),
    ("level_2", "Level 2 (GCSEs A*-C or equivalent)"),
    ("level_1_entry", "Level 1 / entry level"),
    ("other_qualifications", "Other qualifications"),
    ("no_qualifications", "No qualifications"),
]


def _breakdown(row, labels: list[tuple[str, str]]) -> list[dict]:
    if not row or not row.total:
        return []
    return [
        {"label": label, "count": getattr(row, field), "pct": round(getattr(row, field) / row.total * 100, 1)}
        for field, label in labels
    ]


def occupation_for_lsoa(lsoa_code: str) -> dict | None:
    if not is_configured() or not lsoa_code:
        return None
    with get_session() as session:
        row = session.get(Occupation, lsoa_code)
        if row is None or not row.total:
            return None
        professional_pct = round((row.managers_directors_senior + row.professional) / row.total * 100, 1)
        return {
            "total": row.total,
            "professional_pct": professional_pct,
            "breakdown": _breakdown(row, OCCUPATION_LABELS),
        }


def qualification_for_lsoa(lsoa_code: str) -> dict | None:
    if not is_configured() or not lsoa_code:
        return None
    with get_session() as session:
        row = session.get(Qualification, lsoa_code)
        if row is None or not row.total:
            return None
        degree_pct = round(row.level_4_plus / row.total * 100, 1)
        return {
            "total": row.total,
            "degree_pct": degree_pct,
            "breakdown": _breakdown(row, QUALIFICATION_LABELS),
        }
