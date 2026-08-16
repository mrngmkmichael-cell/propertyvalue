"""One-time/periodic offline import of deprivation and household
income estimates into the `deprivation` and `household_income`
tables.

NOT run by the deployed app - run manually from a dev machine when
the data needs refreshing (both datasets are only republished every
few years). Needs `openpyxl` installed locally (not a production
dependency - only used here, to read the ONS income spreadsheet).

Sources:
- English Indices of Deprivation 2025, File 7 (all ranks, scores,
  deciles, by LSOA): https://www.gov.uk/government/statistics/english-indices-of-deprivation-2025
- ONS small area (MSOA) model-based household income estimates,
  financial year ending 2023: https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/earningsandworkinghours/datasets/smallareaincomeestimatesformiddlelayersuperoutputareasenglandandwales

Both keyed by the ONS area codes (LSOA/MSOA) that postcodes.io
returns directly in `location["codes"]` - no coordinate math needed,
unlike the schools import.
"""
import csv
import io
import os
import sys

import httpx
import openpyxl

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.db import Base, _get_engine  # noqa: E402
from app.models import Deprivation, HouseholdIncome  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

IMD_URL = (
    "https://assets.publishing.service.gov.uk/media/691ded56d140bbbaa59a2a7d/"
    "File_7_IoD2025_All_Ranks_Scores_Deciles_Population_Denominators.csv"
)
INCOME_URL = (
    "https://www.ons.gov.uk/file?uri=/employmentandlabourmarket/peopleinwork/"
    "earningsandworkinghours/datasets/smallareaincomeestimatesformiddlelayersuperoutputareasenglandandwales/"
    "financialyearending2023/datasetfinal.xlsx"
)


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _int_or_none(value: str) -> int | None:
    value = (value or "").strip()
    return int(value) if value.lstrip("-").isdigit() else None


def _float_or_none(value: str) -> float | None:
    value = (value or "").strip()
    try:
        return float(value)
    except ValueError:
        return None


def fetch_deprivation_records() -> list[dict]:
    print(f"Downloading IMD 2025 data from {IMD_URL}")
    resp = httpx.get(IMD_URL, timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    text = _decode(resp.content)

    records = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        lsoa_code = row.get("LSOA code (2021)", "").strip()
        if not lsoa_code:
            continue
        records.append({
            "lsoa_code": lsoa_code,
            "lsoa_name": row.get("LSOA name (2021)", "").strip(),
            "la_code": row.get("Local Authority District code (2024)", "").strip(),
            "la_name": row.get("Local Authority District name (2024)", "").strip(),
            "imd_score": _float_or_none(row.get("Index of Multiple Deprivation (IMD) Score")),
            "imd_decile": _int_or_none(row.get("Index of Multiple Deprivation (IMD) Decile (where 1 is most deprived 10% of LSOAs)")),
            "income_decile": _int_or_none(row.get("Income Decile (where 1 is most deprived 10% of LSOAs)")),
            "employment_decile": _int_or_none(row.get("Employment Decile (where 1 is most deprived 10% of LSOAs)")),
            "education_decile": _int_or_none(row.get("Education, Skills and Training Decile (where 1 is most deprived 10% of LSOAs)")),
            "health_decile": _int_or_none(row.get("Health Deprivation and Disability Decile (where 1 is most deprived 10% of LSOAs)")),
            "crime_decile": _int_or_none(row.get("Crime Decile (where 1 is most deprived 10% of LSOAs)")),
            "housing_barriers_decile": _int_or_none(row.get("Barriers to Housing and Services Decile (where 1 is most deprived 10% of LSOAs)")),
            "living_environment_decile": _int_or_none(row.get("Living Environment Decile (where 1 is most deprived 10% of LSOAs)")),
        })

    print(f"  {len(records)} LSOAs")
    return records


def fetch_income_records() -> list[dict]:
    print(f"Downloading ONS income estimates from {INCOME_URL}")
    resp = httpx.get(INCOME_URL, timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    wb = openpyxl.load_workbook(io.BytesIO(resp.content), read_only=True, data_only=True)
    ws = wb["Total annual income"]

    records = []
    header_seen = False
    for row in ws.iter_rows(values_only=True):
        if not header_seen:
            if row and row[0] == "MSOA code":
                header_seen = True
            continue
        if not row or not row[0]:
            continue
        msoa_code, msoa_name, la_code, la_name, region_code, region_name, income = row[:7]
        records.append({
            "msoa_code": msoa_code,
            "msoa_name": msoa_name or "",
            "la_code": la_code or "",
            "la_name": la_name or "",
            "region_code": region_code or "",
            "region_name": region_name or "",
            "total_annual_income": int(income) if isinstance(income, (int, float)) else None,
        })

    print(f"  {len(records)} MSOAs")
    return records


def load_into_db(deprivation_records: list[dict], income_records: list[dict]) -> None:
    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[Deprivation.__table__, HouseholdIncome.__table__])
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        print("Clearing existing deprivation/household_income tables...")
        session.query(Deprivation).delete()
        session.query(HouseholdIncome).delete()
        session.commit()

        print(f"Inserting {len(deprivation_records)} deprivation rows...")
        session.execute(Deprivation.__table__.insert(), deprivation_records)
        session.commit()

        print(f"Inserting {len(income_records)} household income rows...")
        session.execute(HouseholdIncome.__table__.insert(), income_records)
        session.commit()


def main():
    deprivation_records = fetch_deprivation_records()
    income_records = fetch_income_records()
    load_into_db(deprivation_records, income_records)
    print("Done.")


if __name__ == "__main__":
    main()
