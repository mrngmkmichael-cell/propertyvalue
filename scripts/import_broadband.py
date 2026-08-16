"""One-time/periodic offline import of fixed-line broadband coverage
into the `broadband_coverage` table.

NOT run by the deployed app - run manually from a dev machine.
Republished roughly annually - the hardcoded URL below will need
updating (same situation as the other DfE/Ofsted imports).

Source: Ofcom Connected Nations 2025, "Fixed broadband coverage"
download - a nested zip of ~120 CSVs, one per postcode-area prefix
(e.g. "BR", "SW"), at full postcode-unit level (not just district).
https://www.ofcom.org.uk/phones-and-broadband/coverage-and-speeds/connected-nations-20252/data-downloads-2025

This is the largest import in the project (~1.7M postcodes) - runs
in batches to keep memory/DB round-trips reasonable.
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
from app.models import BroadbandCoverage  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

OUTER_ZIP_URL = (
    "https://www.ofcom.org.uk/siteassets/resources/documents/research-and-data/"
    "multi-sector/infrastructure-research/connected-nations-2025/"
    "202507_fixed_broadband_coverage_r01.zip"
)
INNER_ZIP_PATH = "202507_fixed_coverage_r01/202507_fixed_pc_coverage_r01.zip"
INNER_FOLDER = "202507_fixed_pc_coverage_r01/postcode_files/"

HEADERS = {"User-Agent": "Mozilla/5.0"}
BATCH_SIZE = 10000


def _num_or_none(value: str):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_inner_zip() -> zipfile.ZipFile:
    print(f"Downloading {OUTER_ZIP_URL}")
    resp = httpx.get(OUTER_ZIP_URL, timeout=180, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    outer = zipfile.ZipFile(io.BytesIO(resp.content))
    print("Extracting nested postcode-level zip...")
    inner_bytes = outer.read(INNER_ZIP_PATH)
    return zipfile.ZipFile(io.BytesIO(inner_bytes))


def iter_records(inner: zipfile.ZipFile):
    names = sorted(
        n for n in inner.namelist()
        if n.startswith(INNER_FOLDER) and n.endswith(".csv")
    )
    print(f"{len(names)} postcode-area files to process")
    for i, name in enumerate(names, start=1):
        with inner.open(name) as f:
            text = io.TextIOWrapper(f, encoding="utf-8-sig")
            for row in csv.DictReader(text):
                postcode = row.get("postcode_space", "").strip()
                if not postcode:
                    continue
                yield {
                    "postcode": postcode,
                    "gigabit_pct": _num_or_none(row.get("Gigabit availability (% premises)")),
                    "ultrafast_pct": _num_or_none(row.get("UFBB availability (% premises)")),
                    "superfast_pct": _num_or_none(row.get("SFBB availability (% premises)")),
                    "below_uso_pct": _num_or_none(row.get("% of premises below the USO")),
                }
        if i % 20 == 0:
            print(f"  {i} files processed")


def load_into_db(inner: zipfile.ZipFile) -> None:
    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[BroadbandCoverage.__table__])
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        print("Clearing existing broadband_coverage table...")
        session.query(BroadbandCoverage).delete()
        session.commit()

        batch = []
        total = 0
        for record in iter_records(inner):
            batch.append(record)
            if len(batch) >= BATCH_SIZE:
                session.execute(BroadbandCoverage.__table__.insert(), batch)
                session.commit()
                total += len(batch)
                print(f"  inserted {total} rows so far")
                batch = []
        if batch:
            session.execute(BroadbandCoverage.__table__.insert(), batch)
            session.commit()
            total += len(batch)
        print(f"Inserted {total} rows total")


def main():
    inner = fetch_inner_zip()
    load_into_db(inner)
    print("Done.")


if __name__ == "__main__":
    main()
