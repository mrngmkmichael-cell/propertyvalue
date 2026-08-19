"""One-time/periodic offline import of gender split, EAL %, and
ethnicity breakdown into the `school_demographics` table - the same
DfE "schools, pupils and their characteristics" school census dataset
scripts/import_school_characteristics.py uses for FSM eligibility
(same Explore Education Statistics API, same dataset ID), just
querying more of its "Breakdown"/"Sex" filter dimensions.

NOT run by the deployed app - run manually from a dev machine.
Republished annually alongside the school census.

Ethnicity is collapsed to the same five broad categories
app/services/demographics.py already uses for LSOA-level Census
ethnicity, by summing DfE's ~19 detailed sub-category percentages
into those buckets (valid because they're all "% of this school's
total roll", so percentages sum the same way the underlying pupil
counts would).

Gender uses the "Number of pupils" indicator rather than "Percentage
of pupils" - the percentage indicator returns the DfE suppression
code "x" (not applicable) for a plain Sex breakdown with no other
breakdown dimension, confirmed by querying it directly; pupil counts
are available and percentages are computed from those here instead.

SEN status, class sizes, workforce and school finance remain
deliberately excluded - see SchoolCharacteristics's docstring for why
(not published at individual school level as free open data).
"""
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.db import Base, _get_engine  # noqa: E402
from app.models import SchoolDemographics  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

API_BASE = "https://api.education.gov.uk/statistics/v1"
DATASET_ID = "019e7403-4523-7749-b530-159f451dd83c"
ACADEMIC_YEAR = "2025/2026"
HEADERS = {"User-Agent": "Mozilla/5.0"}

BREAKDOWN_TOTAL = "0yMuT"
SEX_TOTAL = "HPLaz"
SEX_FEMALE = "6gvzr"
SEX_MALE = "0yKxT"
INDICATOR_PCT = "yWoXa"
INDICATOR_COUNT = "SdYrV"

EAL_OTHER_LANGUAGE = "gvusO"

# DfE's detailed ethnicity breakdown codes, collapsed to the same five
# broad buckets app/services/demographics.py uses for Census ethnicity.
ETHNICITY_GROUPS = {
    "ethnicity_white_pct": ["PL1he", "UZQ5R", "u2iFo", "W6G82", "TaMwP"],
    "ethnicity_asian_pct": ["lNgmc", "5kZ5d", "cDOF3", "f0kxY", "zeacF"],
    "ethnicity_black_pct": ["93Na4", "tf5RE", "kxbGh"],
    "ethnicity_mixed_pct": ["QBSAw", "93DL4", "2rgKl", "XdRlf"],
    "ethnicity_other_pct": ["u2ibo", "EY0Oq"],
}
ALL_ETHNICITY_CODES = [code for codes in ETHNICITY_GROUPS.values() for code in codes]


def _location_to_urn() -> dict[str, int]:
    resp = httpx.get(f"{API_BASE}/data-sets/{DATASET_ID}/meta", timeout=30, headers=HEADERS)
    resp.raise_for_status()
    meta = resp.json()
    schools = next(g["options"] for g in meta["locations"] if g["level"]["code"] == "SCH")
    return {s["id"]: int(s["urn"]) for s in schools if s.get("urn", "").isdigit()}


def _paged_query(criteria: dict, indicator: str) -> list[dict]:
    rows = []
    page = 1
    while True:
        body = {
            "criteria": criteria,
            "indicators": [indicator],
            "page": page,
            "pageSize": 5000,
        }
        resp = httpx.post(f"{API_BASE}/data-sets/{DATASET_ID}/query", json=body, timeout=60, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        rows.extend(data["results"])
        print(f"    page {page}/{data['paging']['totalPages']} ({len(rows)} rows so far)")
        if page >= data["paging"]["totalPages"]:
            break
        page += 1
    return rows


def fetch_eal_and_ethnicity(location_to_urn: dict[str, int]) -> dict[int, dict]:
    print("Querying EAL + ethnicity breakdown (Sex: Total)")
    criteria = {
        "and": [
            {"filters": {"in": [EAL_OTHER_LANGUAGE] + ALL_ETHNICITY_CODES}},
            {"filters": {"eq": SEX_TOTAL}},
            {"timePeriods": {"eq": {"period": ACADEMIC_YEAR, "code": "AY"}}},
        ]
    }
    rows = _paged_query(criteria, INDICATOR_PCT)

    code_to_group = {code: group for group, codes in ETHNICITY_GROUPS.items() for code in codes}
    by_urn: dict[int, dict] = {}
    for row in rows:
        urn = location_to_urn.get(row["locations"].get("SCH"))
        if urn is None:
            continue
        breakdown_code = row["filters"].get("6HVrf")
        try:
            pct = float(row["values"].get(INDICATOR_PCT))
        except (TypeError, ValueError):
            continue

        entry = by_urn.setdefault(urn, {})
        if breakdown_code == EAL_OTHER_LANGUAGE:
            entry["eal_pct"] = pct
        elif breakdown_code in code_to_group:
            group = code_to_group[breakdown_code]
            entry[group] = entry.get(group, 0.0) + pct

    for entry in by_urn.values():
        for group in ETHNICITY_GROUPS:
            if group in entry:
                entry[group] = round(entry[group], 1)
    return by_urn


def fetch_gender(location_to_urn: dict[str, int]) -> dict[int, dict]:
    print("Querying gender split (Breakdown: Total, pupil counts)")
    criteria = {
        "and": [
            {"filters": {"eq": BREAKDOWN_TOTAL}},
            {"filters": {"in": [SEX_FEMALE, SEX_MALE]}},
            {"timePeriods": {"eq": {"period": ACADEMIC_YEAR, "code": "AY"}}},
        ]
    }
    rows = _paged_query(criteria, INDICATOR_COUNT)

    counts_by_urn: dict[int, dict] = {}
    for row in rows:
        urn = location_to_urn.get(row["locations"].get("SCH"))
        if urn is None:
            continue
        sex_code = row["filters"].get("atYLP")
        try:
            count = float(row["values"].get(INDICATOR_COUNT))
        except (TypeError, ValueError):
            continue
        counts_by_urn.setdefault(urn, {})[sex_code] = count

    by_urn: dict[int, dict] = {}
    for urn, counts in counts_by_urn.items():
        female, male = counts.get(SEX_FEMALE), counts.get(SEX_MALE)
        total = (female or 0) + (male or 0)
        if total <= 0:
            continue
        by_urn[urn] = {
            "female_pct": round(female / total * 100, 1) if female is not None else None,
            "male_pct": round(male / total * 100, 1) if male is not None else None,
        }
    return by_urn


def build_records() -> list[dict]:
    print("Downloading school location list")
    location_to_urn = _location_to_urn()
    print(f"  {len(location_to_urn)} schools in location list")

    eal_ethnicity_by_urn = fetch_eal_and_ethnicity(location_to_urn)
    gender_by_urn = fetch_gender(location_to_urn)

    all_urns = set(eal_ethnicity_by_urn) | set(gender_by_urn)
    records = []
    for urn in all_urns:
        rec = {"urn": urn, "academic_year": f"{ACADEMIC_YEAR[:4]}/{ACADEMIC_YEAR[-2:]}"}
        rec.update(eal_ethnicity_by_urn.get(urn, {}))
        rec.update(gender_by_urn.get(urn, {}))
        for field in (
            "male_pct", "female_pct", "eal_pct", "ethnicity_white_pct", "ethnicity_asian_pct",
            "ethnicity_black_pct", "ethnicity_mixed_pct", "ethnicity_other_pct",
        ):
            rec.setdefault(field, None)
        records.append(rec)

    print(f"  {len(records)} schools with at least one demographic figure")
    return records


def load_into_db(records: list[dict]) -> None:
    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[SchoolDemographics.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as session:
        print("Clearing existing school_demographics table...")
        session.query(SchoolDemographics).delete()
        session.commit()

        print(f"Inserting {len(records)} rows...")
        session.execute(SchoolDemographics.__table__.insert(), records)
        session.commit()


def main():
    records = build_records()
    if not records:
        print("No records built - aborting without touching the database.")
        return
    load_into_db(records)
    print("Done.")


if __name__ == "__main__":
    main()
