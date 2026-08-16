"""One-time/periodic offline import of Census 2021 occupation and
qualification breakdowns into the `occupation` and `qualification`
tables.

NOT run by the deployed app - run manually from a dev machine.
Static data (next refresh is the 2031 census), unlike the other
periodic imports in this project.

Sources - Nomis's official Census 2021 bulk download, LSOA level:
- TS063 Occupation: https://www.nomisweb.co.uk/datasets/c2021ts063
- TS067 Highest level of qualification: https://www.nomisweb.co.uk/datasets/c2021ts067

Both keyed by LSOA code, same as scripts/import_area_stats.py.
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
from app.models import Occupation, Qualification  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

OCCUPATION_ZIP_URL = "https://www.nomisweb.co.uk/output/census/2021/census2021-ts063.zip"
QUALIFICATION_ZIP_URL = "https://www.nomisweb.co.uk/output/census/2021/census2021-ts067.zip"

OCCUPATION_COLUMNS = {
    "Occupation (current): Total: All usual residents aged 16 years and over in employment the week before the census": "total",
    "Occupation (current): 1. Managers, directors and senior officials": "managers_directors_senior",
    "Occupation (current): 2. Professional occupations": "professional",
    "Occupation (current): 3. Associate professional and technical occupations": "associate_professional_technical",
    "Occupation (current): 4. Administrative and secretarial occupations": "admin_secretarial",
    "Occupation (current): 5. Skilled trades occupations": "skilled_trades",
    "Occupation (current): 6. Caring, leisure and other service occupations": "caring_leisure_service",
    "Occupation (current): 7. Sales and customer service occupations": "sales_customer_service",
    "Occupation (current): 8. Process, plant and machine operatives": "process_plant_machine_operatives",
    "Occupation (current): 9. Elementary occupations": "elementary",
}

QUALIFICATION_COLUMNS = {
    "Highest level of qualification: Total: All usual residents aged 16 years and over": "total",
    "Highest level of qualification: No qualifications": "no_qualifications",
    "Highest level of qualification: Level 1 and entry level qualifications": "level_1_entry",
    "Highest level of qualification: Level 2 qualifications": "level_2",
    "Highest level of qualification: Apprenticeship": "apprenticeship",
    "Highest level of qualification: Level 3 qualifications": "level_3",
    "Highest level of qualification: Level 4 qualifications and above": "level_4_plus",
    "Highest level of qualification: Other qualifications": "other_qualifications",
}


def _fetch_lsoa_csv(zip_url: str, csv_name: str) -> list[dict]:
    print(f"Downloading {zip_url}")
    resp = httpx.get(zip_url, timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    with z.open(csv_name) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig").read()
    return list(csv.DictReader(io.StringIO(text)))


def build_occupation_records() -> list[dict]:
    rows = _fetch_lsoa_csv(OCCUPATION_ZIP_URL, "census2021-ts063-lsoa.csv")
    records = []
    for row in rows:
        record = {"lsoa_code": row["geography code"]}
        for src_col, field in OCCUPATION_COLUMNS.items():
            record[field] = int(row.get(src_col, 0) or 0)
        records.append(record)
    print(f"  {len(records)} LSOAs (occupation)")
    return records


def build_qualification_records() -> list[dict]:
    rows = _fetch_lsoa_csv(QUALIFICATION_ZIP_URL, "census2021-ts067-lsoa.csv")
    records = []
    for row in rows:
        record = {"lsoa_code": row["geography code"]}
        for src_col, field in QUALIFICATION_COLUMNS.items():
            record[field] = int(row.get(src_col, 0) or 0)
        records.append(record)
    print(f"  {len(records)} LSOAs (qualification)")
    return records


def load_into_db(occupation_records: list[dict], qualification_records: list[dict]) -> None:
    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[Occupation.__table__, Qualification.__table__])
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        print("Clearing existing occupation/qualification tables...")
        session.query(Occupation).delete()
        session.query(Qualification).delete()
        session.commit()

        print(f"Inserting {len(occupation_records)} occupation rows...")
        session.execute(Occupation.__table__.insert(), occupation_records)
        session.commit()

        print(f"Inserting {len(qualification_records)} qualification rows...")
        session.execute(Qualification.__table__.insert(), qualification_records)
        session.commit()


def main():
    occupation_records = build_occupation_records()
    qualification_records = build_qualification_records()
    load_into_db(occupation_records, qualification_records)
    print("Done.")


if __name__ == "__main__":
    main()
