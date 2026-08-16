"""Deprivation and household income lookups from our own
`deprivation`/`household_income` tables (populated offline by
scripts/import_area_stats.py) - both keyed directly by the LSOA/MSOA
codes postcodes.io already returns, so no coordinate matching is
needed here, unlike schools.
"""
from sqlalchemy import func, select

from app.db import get_session, is_configured
from app.models import Deprivation, HouseholdIncome

DOMAIN_LABELS = [
    ("income_decile", "Income"),
    ("employment_decile", "Employment"),
    ("education_decile", "Education, Skills and Training"),
    ("health_decile", "Health and Disability"),
    ("crime_decile", "Crime"),
    ("housing_barriers_decile", "Barriers to Housing and Services"),
    ("living_environment_decile", "Living Environment"),
]


def deprivation_for_lsoa(lsoa_code: str) -> dict | None:
    if not is_configured() or not lsoa_code:
        return None
    with get_session() as session:
        row = session.get(Deprivation, lsoa_code)
        if row is None:
            return None
        return {
            "imd_score": row.imd_score,
            "imd_decile": row.imd_decile,
            "la_name": row.la_name,
            "domains": [
                {"label": label, "decile": getattr(row, field)}
                for field, label in DOMAIN_LABELS
                if getattr(row, field) is not None
            ],
        }


def income_for_msoa(msoa_code: str) -> dict | None:
    if not is_configured() or not msoa_code:
        return None
    with get_session() as session:
        row = session.get(HouseholdIncome, msoa_code)
        if row is None or row.total_annual_income is None:
            return None

        la_avg = session.scalar(
            select(func.avg(HouseholdIncome.total_annual_income)).where(
                HouseholdIncome.la_code == row.la_code,
                HouseholdIncome.total_annual_income.is_not(None),
            )
        )
        region_avg = session.scalar(
            select(func.avg(HouseholdIncome.total_annual_income)).where(
                HouseholdIncome.region_code == row.region_code,
                HouseholdIncome.total_annual_income.is_not(None),
            )
        )

        return {
            "here": row.total_annual_income,
            "la_name": row.la_name,
            "la_average": round(la_avg) if la_avg else None,
            "region_name": row.region_name,
            "region_average": round(region_avg) if region_avg else None,
        }
