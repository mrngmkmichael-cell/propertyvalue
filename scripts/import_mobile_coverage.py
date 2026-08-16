"""One-time/periodic offline import of mobile signal coverage into
the `mobile_coverage` table.

NOT run by the deployed app - run manually from a dev machine.
Republished roughly annually alongside the fixed-broadband release
(same URL pattern as scripts/import_broadband.py - will need
updating when Ofcom publishes the next Connected Nations release).

Source: Ofcom Connected Nations 2025, "Mobile coverage" download,
local-authority-level file. Unlike fixed broadband, mobile coverage
isn't published at postcode-unit level - the columns encode number
of operators providing coverage (suffix _0 = no operators up to _4 =
all four), separately for premises indoor/outdoor and geographic
area. We only keep a few headline columns.
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
from app.models import MobileCoverage  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

ZIP_URL = (
    "https://www.ofcom.org.uk/siteassets/resources/documents/research-and-data/"
    "multi-sector/infrastructure-research/connected-nations-2025/"
    "202507_mobile_coverage_r01.zip"
)
CSV_PATH = "202507_mobile_coverage_r01/202507_mobile_coverage_laua_r01.csv"

HEADERS = {"User-Agent": "Mozilla/5.0"}


def _num_or_none(value: str):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_records() -> list[dict]:
    print(f"Downloading {ZIP_URL}")
    resp = httpx.get(ZIP_URL, timeout=120, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    with z.open(CSV_PATH) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig")
        records = []
        for row in csv.DictReader(text):
            laua = row.get("laua", "").strip()
            if not laua:
                continue
            records.append({
                "laua_code": laua,
                "la_name": row.get("laua_name", "").strip(),
                "coverage_4g_outdoor_all_pct": _num_or_none(row.get("4G_prem_out_4")),
                "coverage_4g_indoor_all_pct": _num_or_none(row.get("4G_prem_in_4")),
                "no_4g_outdoor_pct": _num_or_none(row.get("4G_prem_out_0")),
                "coverage_5g_outdoor_pct": _num_or_none(row.get("5G_high_confidence_prem_out_4")),
            })
    print(f"  {len(records)} local authorities")
    return records


def load_into_db(records: list[dict]) -> None:
    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[MobileCoverage.__table__])
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        print("Clearing existing mobile_coverage table...")
        session.query(MobileCoverage).delete()
        session.commit()

        print(f"Inserting {len(records)} rows...")
        session.execute(MobileCoverage.__table__.insert(), records)
        session.commit()


def main():
    records = fetch_records()
    load_into_db(records)
    print("Done.")


if __name__ == "__main__":
    main()
