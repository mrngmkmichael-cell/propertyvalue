"""One-time/periodic offline import of free school meal (FSM)
eligibility into the `school_characteristics` table.

NOT run by the deployed app - run manually from a dev machine.
Republished annually alongside the school census - same "IDs will
need updating" situation as the other DfE imports.

Source: DfE's "Schools, pupils and their characteristics" school
census, "School level" dataset, via the public Explore Education
Statistics API (same query technique as scripts/import_exam_results.py's
KS2 import - the bulk file for this dataset is 5M+ rows across many
breakdowns we don't need).

SEN status, class sizes, workforce and school finance were also
investigated here - none are published at individual school level as
free open data (only aggregated to local authority/national), so
they're deliberately left out rather than faked or approximated.
"""
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.db import Base, _get_engine  # noqa: E402
from app.models import SchoolCharacteristics  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

API_BASE = "https://api.education.gov.uk/statistics/v1"
DATASET_ID = "019e7403-4523-7749-b530-159f451dd83c"
BREAKDOWN_FSM_ELIGIBLE = "VR8g5"
ATTENDANCE_TOTAL = "QBpjw"
SEX_TOTAL = "HPLaz"
INDICATOR_PCT = "yWoXa"
ACADEMIC_YEAR = "2025/2026"

HEADERS = {"User-Agent": "Mozilla/5.0"}


def _location_to_urn() -> dict[str, int]:
    resp = httpx.get(f"{API_BASE}/data-sets/{DATASET_ID}/meta", timeout=30, headers=HEADERS)
    resp.raise_for_status()
    meta = resp.json()
    schools = next(g["options"] for g in meta["locations"] if g["level"]["code"] == "SCH")
    return {s["id"]: int(s["urn"]) for s in schools if s.get("urn", "").isdigit()}


def fetch_records() -> list[dict]:
    print("Downloading school location list")
    location_to_urn = _location_to_urn()
    print(f"  {len(location_to_urn)} schools in location list")

    print("Querying FSM eligibility (all pupils, total)")
    records_by_urn: dict[int, dict] = {}
    page = 1
    while True:
        body = {
            "criteria": {
                "and": [
                    {"filters": {"eq": BREAKDOWN_FSM_ELIGIBLE}},
                    {"filters": {"eq": ATTENDANCE_TOTAL}},
                    {"filters": {"eq": SEX_TOTAL}},
                    {"timePeriods": {"eq": {"period": ACADEMIC_YEAR, "code": "AY"}}},
                ]
            },
            "indicators": [INDICATOR_PCT],
            "page": page,
            "pageSize": 500,
        }
        resp = httpx.post(f"{API_BASE}/data-sets/{DATASET_ID}/query", json=body, timeout=30, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()

        for row in data["results"]:
            urn = location_to_urn.get(row["locations"].get("SCH"))
            if urn is None:
                continue
            value = row["values"].get(INDICATOR_PCT)
            try:
                pct = float(value)
            except (TypeError, ValueError):
                pct = None
            records_by_urn[urn] = {
                "urn": urn,
                "academic_year": f"{ACADEMIC_YEAR[:4]}/{ACADEMIC_YEAR[-2:]}",
                "fsm_eligible_pct": pct,
            }

        print(f"  page {page}/{data['paging']['totalPages']}")
        if page >= data["paging"]["totalPages"]:
            break
        page += 1

    records = list(records_by_urn.values())
    print(f"  {len(records)} schools")
    return records


def load_into_db(records: list[dict]) -> None:
    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[SchoolCharacteristics.__table__])
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        print("Clearing existing school_characteristics table...")
        session.query(SchoolCharacteristics).delete()
        session.commit()

        print(f"Inserting {len(records)} rows...")
        session.execute(SchoolCharacteristics.__table__.insert(), records)
        session.commit()


def main():
    records = fetch_records()
    load_into_db(records)
    print("Done.")


if __name__ == "__main__":
    main()
