"""One-time/periodic offline import of ONS's Price Index of Private
Rents (PIPR) - median rental prices by local authority and bedroom
count - into the rental_price table.

NOT run by the deployed app - run manually from a dev machine.

Unlike the census imports, this genuinely IS a periodically-refreshed
dataset (ONS publish a new edition monthly) - re-run this occasionally
if the numbers start looking stale. Requires `pip install openpyxl`
(not in requirements.txt - dev-only, same as pyproj for the schools
import, since it's never imported by the deployed app itself).

Source: https://www.ons.gov.uk/economy/inflationandpriceindices/datasets/priceindexofprivaterentsukmonthlypricestatistics
This is the successor to ONS's discontinued "Private rental market
summary statistics in England" (last published Dec 2023) - that
dataset doesn't exist anymore, PIPR is the only free source left with
this granularity (local authority x bedroom count).

The download URL is dated (changes every edition) - update
XLSX_URL below to the current "full data download" link from the
dataset page above if this script starts 404ing.
"""
import io
import os
import sys

import httpx
import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.db import Base, _get_engine  # noqa: E402
from app.models import RentalPrice  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

XLSX_URL = (
    "https://www.ons.gov.uk/file?uri=/economy/inflationandpriceindices/datasets/"
    "priceindexofprivaterentsukmonthlypricestatistics/22july2026/"
    "priceindexofprivaterentsukmonthlypricestatistics14.xlsx"
)

# Local-authority-level area code prefixes (England unitary/district/
# metropolitan/London borough, and Wales) - excludes UK/country/region
# rows, which use different code prefixes (K, E12, W92, etc.).
LA_PREFIXES = ("E06", "E07", "E08", "E09", "W06")


def _num(value):
    """ONS uses the literal string '[x]' for suppressed/unavailable
    cells - anything non-numeric becomes None rather than crashing."""
    if isinstance(value, (int, float)):
        return value
    return None


def fetch_workbook() -> openpyxl.workbook.Workbook:
    print(f"Downloading {XLSX_URL}")
    resp = httpx.get(XLSX_URL, timeout=120, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return openpyxl.load_workbook(io.BytesIO(resp.content), read_only=True)


def build_records() -> list[dict]:
    wb = fetch_workbook()
    ws = wb["Table 1"]

    rows = list(ws.iter_rows(min_row=4, values_only=True))
    latest_period = max(r[0] for r in rows if r[0] is not None)
    print(f"Latest period in file: {latest_period:%Y-%m}")

    records = []
    for row in rows:
        period, area_code, area_name = row[0], row[1], row[2]
        if period != latest_period or not area_code or not area_code.startswith(LA_PREFIXES):
            continue
        records.append({
            "laua_code": area_code,
            "la_name": area_name or "",
            "period": f"{latest_period:%Y-%m}",
            "price_all": _num(row[7]),
            "change_all_pct": _num(row[6]),
            "price_1bed": _num(row[11]),
            "change_1bed_pct": _num(row[10]),
            "price_2bed": _num(row[15]),
            "change_2bed_pct": _num(row[14]),
            "price_3bed": _num(row[19]),
            "change_3bed_pct": _num(row[18]),
            "price_4plus_bed": _num(row[23]),
            "change_4plus_bed_pct": _num(row[22]),
        })
    print(f"  {len(records)} local authorities")
    return records


def load_into_db(records: list[dict]) -> None:
    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[RentalPrice.__table__])
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        print("Clearing existing rental_price...")
        session.query(RentalPrice).delete()
        session.commit()
        print(f"Inserting {len(records)} rows...")
        session.execute(RentalPrice.__table__.insert(), records)
        session.commit()


def main():
    records = build_records()
    load_into_db(records)
    print("Done.")


if __name__ == "__main__":
    main()
