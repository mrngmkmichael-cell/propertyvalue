"""One-time/periodic offline import of GCSE (KS4) and SATs (KS2)
headline performance measures into the `ks4_results`/`ks2_results`
tables.

NOT run by the deployed app - run manually from a dev machine.
Republished annually - the hardcoded IDs below will need updating
when DfE publishes the next academic year's release (same situation
as the Ofsted URL in import_schools.py).

Sources - both via DfE's Explore Education Statistics service:
- KS4: bulk release download (release ID hardcoded below - find the
  current one from https://explore-education-statistics.service.gov.uk/find-statistics/key-stage-4-performance)
- KS2: the public query API (dataset ID hardcoded below - find the
  current one from https://explore-education-statistics.service.gov.uk/find-statistics/key-stage-2-attainment),
  since the bulk file mixes years/subjects/breakdowns into ~1.2M rows
  and the API lets us ask for just what we need.
"""
import csv
import io
import os
import sys
import zipfile

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.db import Base, _get_engine  # noqa: E402
from app.models import Ks2Result, Ks4Result  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

KS4_RELEASE_ZIP_URL = (
    "https://content.explore-education-statistics.service.gov.uk/api/releases/"
    "b76a938a-7875-4542-af20-0b23ecb99a49/files?fromPage=ReleaseDownloads"
)
KS4_CSV_NAME = "data/202324_performance_tables_schools_final.csv"
KS4_ACADEMIC_YEAR = "2023/24"

KS2_API_BASE = "https://api.education.gov.uk/statistics/v1"
KS2_DATASET_ID = "019afee4-e5d0-72f9-9a8f-d7a1a56eac1d"
KS2_BREAKDOWN_TOTAL = "EXcPq"
KS2_SUBJECT_RWM = "PyBQe"
KS2_ACADEMIC_YEAR = "2024/2025"
KS2_INDICATOR_EXPECTED = "IwjBz"
KS2_INDICATOR_HIGHER = "i2s6X"

HEADERS = {"User-Agent": "Mozilla/5.0"}
SUPPRESSED_CODES = {"z", "c", "x", "np", "supp", "low", "n/a", ""}


def _num_or_none(value: str, cast=float):
    value = (value or "").strip()
    if value.lower() in SUPPRESSED_CODES:
        return None
    try:
        return cast(value)
    except ValueError:
        return None


def fetch_ks4_records() -> list[dict]:
    print(f"Downloading KS4 release from {KS4_RELEASE_ZIP_URL}")
    resp = httpx.get(KS4_RELEASE_ZIP_URL, timeout=120, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    with z.open(KS4_CSV_NAME) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig").read()

    records = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("breakdown_topic") != "Total" or row.get("breakdown") != "Total":
            continue
        urn = _num_or_none(row.get("school_urn"), int)
        if urn is None:
            continue
        records.append({
            "urn": urn,
            "academic_year": KS4_ACADEMIC_YEAR,
            "pupil_count": _num_or_none(row.get("t_pupils"), int),
            "attainment8_avg": _num_or_none(row.get("avg_att8")),
            "progress8_score": _num_or_none(row.get("avg_p8score")),
            "grade5_english_maths_pct": _num_or_none(row.get("pt_l2basics_95")),
            "grade4_english_maths_pct": _num_or_none(row.get("pt_l2basics_94")),
            "ebacc_entry_pct": _num_or_none(row.get("pt_ebacc_e_ptq_ee")),
            "ebacc_aps_avg": _num_or_none(row.get("avg_ebaccaps")),
        })
    print(f"  {len(records)} secondary schools")
    return records


def _ks2_location_to_urn() -> dict[str, int]:
    resp = httpx.get(f"{KS2_API_BASE}/data-sets/{KS2_DATASET_ID}/meta", timeout=30, headers=HEADERS)
    resp.raise_for_status()
    meta = resp.json()
    schools = next(g["options"] for g in meta["locations"] if g["level"]["code"] == "SCH")
    return {s["id"]: int(s["urn"]) for s in schools if s.get("urn", "").isdigit()}


def fetch_ks2_records() -> list[dict]:
    print("Downloading KS2 location list")
    location_to_urn = _ks2_location_to_urn()
    print(f"  {len(location_to_urn)} primary schools in location list")

    print("Querying KS2 attainment data (Total pupils, reading/writing/maths combined)")
    records = []
    page = 1
    while True:
        body = {
            "criteria": {
                "and": [
                    {"filters": {"eq": KS2_BREAKDOWN_TOTAL}},
                    {"filters": {"eq": KS2_SUBJECT_RWM}},
                    {"timePeriods": {"eq": {"period": KS2_ACADEMIC_YEAR, "code": "AY"}}},
                ]
            },
            "indicators": [KS2_INDICATOR_EXPECTED, KS2_INDICATOR_HIGHER],
            "page": page,
            "pageSize": 500,
        }
        resp = httpx.post(f"{KS2_API_BASE}/data-sets/{KS2_DATASET_ID}/query", json=body, timeout=30, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()

        for row in data["results"]:
            location_id = row["locations"].get("SCH")
            urn = location_to_urn.get(location_id)
            if urn is None:
                continue
            values = row["values"]
            records.append({
                "urn": urn,
                "academic_year": f"{KS2_ACADEMIC_YEAR[:4]}/{KS2_ACADEMIC_YEAR[-2:]}",
                "pupil_count": None,
                "rwm_expected_pct": _num_or_none(values.get(KS2_INDICATOR_EXPECTED)),
                "rwm_higher_pct": _num_or_none(values.get(KS2_INDICATOR_HIGHER)),
            })

        print(f"  page {page}/{data['paging']['totalPages']}")
        if page >= data["paging"]["totalPages"]:
            break
        page += 1

    print(f"  {len(records)} primary schools")
    return records


def load_into_db(ks4_records: list[dict], ks2_records: list[dict]) -> None:
    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[Ks4Result.__table__, Ks2Result.__table__])
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        print("Clearing existing ks4_results/ks2_results tables...")
        session.query(Ks4Result).delete()
        session.query(Ks2Result).delete()
        session.commit()

        print(f"Inserting {len(ks4_records)} KS4 rows...")
        session.execute(Ks4Result.__table__.insert(), ks4_records)
        session.commit()

        print(f"Inserting {len(ks2_records)} KS2 rows...")
        session.execute(Ks2Result.__table__.insert(), ks2_records)
        session.commit()


def main():
    ks4_records = fetch_ks4_records()
    ks2_records = fetch_ks2_records()
    load_into_db(ks4_records, ks2_records)
    print("Done.")


if __name__ == "__main__":
    main()
