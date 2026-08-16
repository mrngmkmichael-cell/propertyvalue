"""Census 2021 demographic breakdowns from our own tables (populated
offline by scripts/import_census_demographics.py) - keyed by LSOA
code, same lookup pattern as app/services/census_stats.py.

Grouped into four cards matching how they're presented on the
Summary tab: age profile on its own, then housing (type/tenure/
occupancy), background (ethnicity/religion/country of birth) and
wellbeing (health/marital status/NS-SEC) each bundle three related
tables into one modal.
"""
from app.db import get_session, is_configured
from app.models import (
    AgeProfile, CountryOfBirth, Ethnicity, GeneralHealth, HousingType, MaritalStatus, OccupancyRating,
    Religion, SocioeconomicClassification, Tenure,
)

AGE_LABELS = [
    ("under_15", "Under 15"),
    ("age_15_24", "15 to 24"),
    ("age_25_44", "25 to 44"),
    ("age_45_64", "45 to 64"),
    ("age_65_84", "65 to 84"),
    ("age_85_plus", "85 and over"),
]

HOUSING_TYPE_LABELS = [
    ("detached", "Detached"),
    ("semi_detached", "Semi-detached"),
    ("terraced", "Terraced"),
    ("flat_or_converted", "Flat, maisonette or converted building"),
    ("caravan_or_other", "Caravan or other mobile/temporary"),
]

TENURE_LABELS = [
    ("owned_outright", "Owned outright"),
    ("owned_mortgage", "Owned with a mortgage or loan"),
    ("shared_ownership", "Shared ownership"),
    ("social_rented", "Social rented"),
    ("private_rented", "Private rented"),
    ("rent_free", "Lives rent free"),
]

OCCUPANCY_LABELS = [
    ("plus_2_or_more", "2+ spare bedrooms"),
    ("plus_1", "1 spare bedroom"),
    ("exact", "Exactly enough bedrooms"),
    ("minus_1", "1 bedroom short"),
    ("minus_2_or_less", "2+ bedrooms short"),
]

ETHNICITY_LABELS = [
    ("white", "White"),
    ("asian", "Asian, Asian British or Asian Welsh"),
    ("black", "Black, Black British, Black Welsh, Caribbean or African"),
    ("mixed", "Mixed or Multiple ethnic groups"),
    ("other", "Other ethnic group"),
]

RELIGION_LABELS = [
    ("christian", "Christian"),
    ("no_religion", "No religion"),
    ("muslim", "Muslim"),
    ("hindu", "Hindu"),
    ("sikh", "Sikh"),
    ("jewish", "Jewish"),
    ("buddhist", "Buddhist"),
    ("other_religion", "Other religion"),
    ("not_answered", "Not answered"),
]

COUNTRY_OF_BIRTH_LABELS = [
    ("uk", "United Kingdom"),
    ("eu", "EU countries"),
    ("non_eu_europe", "Rest of Europe"),
    ("middle_east_asia", "Middle East and Asia"),
    ("africa", "Africa"),
    ("americas_caribbean", "The Americas and the Caribbean"),
    ("oceania_other", "Oceania, Antarctica and other"),
    ("british_overseas", "British Overseas"),
]

HEALTH_LABELS = [
    ("very_good", "Very good health"),
    ("good", "Good health"),
    ("fair", "Fair health"),
    ("bad", "Bad health"),
    ("very_bad", "Very bad health"),
]

MARITAL_LABELS = [
    ("never_married", "Never married"),
    ("married_or_civil_partnership", "Married or in a civil partnership"),
    ("separated", "Separated"),
    ("divorced_or_dissolved", "Divorced or dissolved partnership"),
    ("widowed_or_surviving_partner", "Widowed or surviving partner"),
]

NSSEC_LABELS = [
    ("higher_managerial_professional", "Higher managerial/professional"),
    ("lower_managerial_professional", "Lower managerial/professional"),
    ("intermediate", "Intermediate occupations"),
    ("small_employers_self_employed", "Small employers/self-employed"),
    ("lower_supervisory_technical", "Lower supervisory/technical"),
    ("semi_routine", "Semi-routine occupations"),
    ("routine", "Routine occupations"),
    ("never_worked_long_term_unemployed", "Never worked/long-term unemployed"),
    ("full_time_students", "Full-time students"),
]


def _breakdown(row, labels: list[tuple[str, str]]) -> list[dict]:
    if not row or not row.total:
        return []
    return [
        {"label": label, "count": getattr(row, field), "pct": round(getattr(row, field) / row.total * 100, 1)}
        for field, label in labels
    ]


def age_profile_for_lsoa(lsoa_code: str) -> dict | None:
    if not is_configured() or not lsoa_code:
        return None
    with get_session() as session:
        row = session.get(AgeProfile, lsoa_code)
        if row is None or not row.total:
            return None
        under_25_pct = round((row.under_15 + row.age_15_24) / row.total * 100, 1)
        return {
            "total": row.total,
            "under_25_pct": under_25_pct,
            "breakdown": _breakdown(row, AGE_LABELS),
        }


def housing_for_lsoa(lsoa_code: str) -> dict | None:
    if not is_configured() or not lsoa_code:
        return None
    with get_session() as session:
        housing_row = session.get(HousingType, lsoa_code)
        tenure_row = session.get(Tenure, lsoa_code)
        occupancy_row = session.get(OccupancyRating, lsoa_code)
        if not any([housing_row, tenure_row, occupancy_row]):
            return None
        owned_pct = None
        if tenure_row and tenure_row.total:
            owned_pct = round((tenure_row.owned_outright + tenure_row.owned_mortgage) / tenure_row.total * 100, 1)
        return {
            "owned_pct": owned_pct,
            "type_breakdown": _breakdown(housing_row, HOUSING_TYPE_LABELS),
            "tenure_breakdown": _breakdown(tenure_row, TENURE_LABELS),
            "occupancy_breakdown": _breakdown(occupancy_row, OCCUPANCY_LABELS),
        }


def background_for_lsoa(lsoa_code: str) -> dict | None:
    if not is_configured() or not lsoa_code:
        return None
    with get_session() as session:
        ethnicity_row = session.get(Ethnicity, lsoa_code)
        religion_row = session.get(Religion, lsoa_code)
        birth_row = session.get(CountryOfBirth, lsoa_code)
        if not any([ethnicity_row, religion_row, birth_row]):
            return None
        born_abroad_pct = None
        if birth_row and birth_row.total:
            born_abroad_pct = round((birth_row.total - birth_row.uk) / birth_row.total * 100, 1)
        return {
            "born_abroad_pct": born_abroad_pct,
            "ethnicity_breakdown": _breakdown(ethnicity_row, ETHNICITY_LABELS),
            "religion_breakdown": _breakdown(religion_row, RELIGION_LABELS),
            "country_of_birth_breakdown": _breakdown(birth_row, COUNTRY_OF_BIRTH_LABELS),
        }


def wellbeing_for_lsoa(lsoa_code: str) -> dict | None:
    if not is_configured() or not lsoa_code:
        return None
    with get_session() as session:
        health_row = session.get(GeneralHealth, lsoa_code)
        marital_row = session.get(MaritalStatus, lsoa_code)
        nssec_row = session.get(SocioeconomicClassification, lsoa_code)
        if not any([health_row, marital_row, nssec_row]):
            return None
        good_health_pct = None
        if health_row and health_row.total:
            good_health_pct = round((health_row.very_good + health_row.good) / health_row.total * 100, 1)
        return {
            "good_health_pct": good_health_pct,
            "health_breakdown": _breakdown(health_row, HEALTH_LABELS),
            "marital_breakdown": _breakdown(marital_row, MARITAL_LABELS),
            "nssec_breakdown": _breakdown(nssec_row, NSSEC_LABELS),
        }
