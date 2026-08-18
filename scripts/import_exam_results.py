"""One-time/periodic offline import of GCSE (KS4) and SATs (KS2)
headline performance measures, plus KS4 leaver destinations, into the
`ks4_results`/`ks2_results`/`school_destinations` tables.

NOT run by the deployed app - run manually from a dev machine.
Republished annually/periodically - the hardcoded dataset IDs below
will need updating if DfE retires these API dataset versions (find
current ones via https://explore-education-statistics.service.gov.uk,
data catalogue for the relevant publication).

All three datasets are pulled from DfE's Explore Education Statistics
new query API (api.education.gov.uk/statistics/v1), which - unlike
the old single-year bulk ZIP this script used to rely on - already
publishes several years of school-level rows per dataset, so no
per-year URL hunting is needed: KS4 performance and KS2 attainment
both cover 2022/23-2024/25; KS4 destinations covers up to 2022/23 (its
most recent published year - destinations take longer to publish
since they require 6-12 months of post-leaving tracking data).

KS4 destinations is deliberately narrow: broad category percentages
(school sixth form, sixth-form college, FE college, apprenticeship,
employment, not sustained) only - NOT named receiving institutions or
feeder schools. DfE doesn't publish that level of pupil-tracing as
open data; it requires a restricted National Pupil Database
application, not a bulk CSV/API pull like everything else this
project imports.
"""
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.db import Base, _get_engine  # noqa: E402
from app.models import Ks2Result, Ks4Result, SchoolDestinations  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

API_BASE = "https://api.education.gov.uk/statistics/v1"
HEADERS = {"User-Agent": "Mozilla/5.0"}
SUPPRESSED_CODES = {"z", "c", "x", "np", "supp", "low", "n/a", ""}
PAGE_SIZE = 500

KS4_DATASET_ID = "19e39901-a96c-be76-b9c2-6af54ae076d2"  # Performance tables schools data
KS4_ACADEMIC_YEARS = ["2022/2023", "2023/2024", "2024/2025"]
KS4_INDICATORS = {
    "pupil_count": "IL3Bz",       # Number of pupils at the end of KS4
    "attainment8_avg": "kgVhs",   # Average Attainment 8 score
    "progress8_score": "Pwoeb",   # Average Progress 8 score
    "grade5_english_maths_pct": "dDo0Z",
    "grade4_english_maths_pct": "hCRyW",
    "ebacc_entry_pct": "bmztT",
    "ebacc_aps_avg": "flgYF",
}
# Filter groups that need pinning to "Total" (no characteristic
# breakdown) to get one row per school rather than one per
# disadvantage/sex/prior-attainment/etc combination.
KS4_TOTAL_FILTERS = {
    "pPmSo": "5Kydi",  # Disadvantaged status
    "IzpBz": "mws9K",  # First language
    "ibG6X": "WCb2b",  # Mobility status
    "ETvqF": "TaYuP",  # Prior attainment
    "LZ6Wj": "9b64v",  # Sex
}

KS2_DATASET_ID = "019afee4-e5d0-72f9-9a8f-d7a1a56eac1d"
KS2_ACADEMIC_YEARS = ["2022/2023", "2023/2024", "2024/2025"]
KS2_BREAKDOWN_TOTAL = "EXcPq"
KS2_SUBJECT_RWM = "PyBQe"
KS2_INDICATOR_EXPECTED = "IwjBz"
KS2_INDICATOR_HIGHER = "i2s6X"

KS4_DEST_DATASET_ID = "019d4f41-22d1-71b2-a1a7-f3b91026815b"  # Key stage 4 leavers destinations
KS4_DEST_ACADEMIC_YEAR = "2022/2023"
KS4_DEST_MEASURES = {
    "school_sixth_form_pct": "DCz1Q",
    "sixth_form_college_pct": "eLsdu",
    "further_education_pct": "o2MJm",
    "apprenticeship_pct": "mlKo9",
    "employment_pct": "QIJEw",
    "not_sustained_pct": "RZrek",
}
KS4_DEST_INDICATOR = "dPjk0"  # Percentage of pupils sustaining a destination
# Same "Total" pinning idea as KS4_TOTAL_FILTERS, for this dataset's
# own filter groups (disadvantage/ethnicity/sex/student-characteristic -
# establishment type is deliberately NOT pinned to Total here, since a
# school-level row only has its own single establishment type, not a
# "Total" option, the way national/LA aggregate rows do).
KS4_DEST_TOTAL_FILTERS = {
    "9ss4v": "p9WRS",  # Disadvantage Status
    "pkLSo": "Y0RhH",  # Ethnicity Minor
    "7TuXo": "9c6y4",  # Ethnicity Major
    "IduBz": "X542f",  # Sex
    "iCY6X": "jHdaA",  # Student characteristic overall topic
    "LNqWj": "wBpIb",  # Student characteristics
}


def _num_or_none(value, cast=float):
    value = str(value if value is not None else "").strip()
    if value.lower() in SUPPRESSED_CODES:
        return None
    try:
        return cast(value)
    except ValueError:
        return None


def _location_to_urn(dataset_id: str) -> dict[str, int]:
    resp = httpx.get(f"{API_BASE}/data-sets/{dataset_id}/meta", timeout=30, headers=HEADERS)
    resp.raise_for_status()
    meta = resp.json()
    schools = next(g["options"] for g in meta["locations"] if g["level"]["code"] == "SCH")
    return {s["id"]: int(s["urn"]) for s in schools if s.get("urn", "").isdigit()}


def _query_all_pages(dataset_id: str, filters: dict, time_period: str, indicators: list[str]) -> list[dict]:
    results = []
    page = 1
    while True:
        body = {
            "criteria": {
                "and": [
                    *({"filters": {"eq": v}} for v in filters.values()),
                    {"timePeriods": {"eq": {"period": time_period, "code": "AY"}}},
                    {"geographicLevels": {"eq": "SCH"}},
                ]
            },
            "indicators": indicators,
            "page": page,
            "pageSize": PAGE_SIZE,
        }
        resp = httpx.post(f"{API_BASE}/data-sets/{dataset_id}/query", json=body, timeout=60, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data["results"])
        if page >= data["paging"]["totalPages"]:
            break
        page += 1
    return results


def fetch_ks4_records() -> list[dict]:
    print(f"KS4 performance: mapping school locations to URNs ({KS4_DATASET_ID})")
    location_to_urn = _location_to_urn(KS4_DATASET_ID)

    records = []
    for period in KS4_ACADEMIC_YEARS:
        label = f"{period[:4]}/{period[-2:]}"
        print(f"  querying {label}...")
        rows = _query_all_pages(KS4_DATASET_ID, KS4_TOTAL_FILTERS, period, list(KS4_INDICATORS.values()))
        for row in rows:
            urn = location_to_urn.get(row["locations"].get("SCH"))
            if urn is None:
                continue
            values = row["values"]
            records.append({
                "urn": urn,
                "academic_year": label,
                "pupil_count": _num_or_none(values.get(KS4_INDICATORS["pupil_count"]), int),
                "attainment8_avg": _num_or_none(values.get(KS4_INDICATORS["attainment8_avg"])),
                "progress8_score": _num_or_none(values.get(KS4_INDICATORS["progress8_score"])),
                "grade5_english_maths_pct": _num_or_none(values.get(KS4_INDICATORS["grade5_english_maths_pct"])),
                "grade4_english_maths_pct": _num_or_none(values.get(KS4_INDICATORS["grade4_english_maths_pct"])),
                "ebacc_entry_pct": _num_or_none(values.get(KS4_INDICATORS["ebacc_entry_pct"])),
                "ebacc_aps_avg": _num_or_none(values.get(KS4_INDICATORS["ebacc_aps_avg"])),
            })
        print(f"    {len(rows)} rows")
    print(f"  {len(records)} total KS4 school-year rows")
    return records


def fetch_ks2_records() -> list[dict]:
    print(f"KS2 attainment: mapping school locations to URNs ({KS2_DATASET_ID})")
    location_to_urn = _location_to_urn(KS2_DATASET_ID)

    records = []
    for period in KS2_ACADEMIC_YEARS:
        label = f"{period[:4]}/{period[-2:]}"
        print(f"  querying {label}...")
        filters = {"breakdown": KS2_BREAKDOWN_TOTAL, "subject": KS2_SUBJECT_RWM}
        rows = _query_all_pages(KS2_DATASET_ID, filters, period, [KS2_INDICATOR_EXPECTED, KS2_INDICATOR_HIGHER])
        for row in rows:
            urn = location_to_urn.get(row["locations"].get("SCH"))
            if urn is None:
                continue
            values = row["values"]
            records.append({
                "urn": urn,
                "academic_year": label,
                "pupil_count": None,
                "rwm_expected_pct": _num_or_none(values.get(KS2_INDICATOR_EXPECTED)),
                "rwm_higher_pct": _num_or_none(values.get(KS2_INDICATOR_HIGHER)),
            })
        print(f"    {len(rows)} rows")
    print(f"  {len(records)} total KS2 school-year rows")
    return records


def fetch_ks4_destinations() -> list[dict]:
    print(f"KS4 destinations: mapping school locations to URNs ({KS4_DEST_DATASET_ID})")
    location_to_urn = _location_to_urn(KS4_DEST_DATASET_ID)

    label = f"{KS4_DEST_ACADEMIC_YEAR[:4]}/{KS4_DEST_ACADEMIC_YEAR[-2:]}"
    by_urn: dict[int, dict] = {}
    for field, measure_id in KS4_DEST_MEASURES.items():
        print(f"  querying {field}...")
        filters = {**KS4_DEST_TOTAL_FILTERS, "destination_measure": measure_id}
        rows = _query_all_pages(KS4_DEST_DATASET_ID, filters, KS4_DEST_ACADEMIC_YEAR, [KS4_DEST_INDICATOR])
        for row in rows:
            urn = location_to_urn.get(row["locations"].get("SCH"))
            if urn is None:
                continue
            by_urn.setdefault(urn, {"urn": urn, "academic_year": label})[field] = _num_or_none(
                row["values"].get(KS4_DEST_INDICATOR)
            )
        print(f"    {len(rows)} rows")

    records = list(by_urn.values())
    print(f"  {len(records)} schools with destination data")
    return records


def load_into_db(ks4_records: list[dict], ks2_records: list[dict], dest_records: list[dict]) -> None:
    engine = _get_engine()
    # Ks4Result/Ks2Result moved from a single-column urn primary key to
    # a composite (urn, academic_year) key so multiple years can
    # coexist - create_all() only creates missing tables, it doesn't
    # alter an existing table's primary key, so the old single-key
    # tables need dropping first to pick up the new schema.
    Ks4Result.__table__.drop(engine, checkfirst=True)
    Ks2Result.__table__.drop(engine, checkfirst=True)
    Base.metadata.create_all(engine, tables=[Ks4Result.__table__, Ks2Result.__table__, SchoolDestinations.__table__])
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        print("Clearing existing school_destinations table...")
        session.query(SchoolDestinations).delete()
        session.commit()

        print(f"Inserting {len(ks4_records)} KS4 rows...")
        for i in range(0, len(ks4_records), PAGE_SIZE):
            session.execute(Ks4Result.__table__.insert(), ks4_records[i:i + PAGE_SIZE])
        session.commit()

        print(f"Inserting {len(ks2_records)} KS2 rows...")
        for i in range(0, len(ks2_records), PAGE_SIZE):
            session.execute(Ks2Result.__table__.insert(), ks2_records[i:i + PAGE_SIZE])
        session.commit()

        print(f"Inserting {len(dest_records)} destination rows...")
        for i in range(0, len(dest_records), PAGE_SIZE):
            session.execute(SchoolDestinations.__table__.insert(), dest_records[i:i + PAGE_SIZE])
        session.commit()


def main():
    ks4_records = fetch_ks4_records()
    ks2_records = fetch_ks2_records()
    dest_records = fetch_ks4_destinations()
    load_into_db(ks4_records, ks2_records, dest_records)
    print("Done.")


if __name__ == "__main__":
    main()
