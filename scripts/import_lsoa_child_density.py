"""One-time/periodic offline import of school-age population density
by LSOA - the two ingredients a population-density-based *modelled*
catchment radius needs (see app/models.py's LsoaChildDensity and
SchoolCatchmentEstimate docstrings for why this exists: a fallback for
schools whose council hasn't published a real admission-distance
figure, using the same class of technique competitor sites like
Locrating use as their default catchment indicator).

NOT run by the deployed app - run manually from a dev machine.
Static data (next refresh is the 2031 census / next SAM release).

Sources:
- TS007A Age (5-year bands), LSOA level: same Nomis bulk download as
  scripts/import_census_demographics.py uses for AgeProfile, just
  keeping the "5 to 9" and "10 to 14" bands separate here instead of
  summing them into "under_15" - https://www.nomisweb.co.uk/datasets/c2021ts007a
- Standard Area Measurements (SAM) for 2021 statistical geographies,
  LSOA land area in km2 - ONS Open Geography Portal item
  a488cb8fc9a74accb63cb52961e456ef, downloaded via ArcGIS Online's
  item data endpoint (the geoportal's own pages don't serve the file
  directly to a plain HTTP client, but the underlying ArcGIS Online
  item does) - https://www.ons.gov.uk/methodology/geography/geographicalproducts/otherproducts/ukstandardareameasurementssam
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
from app.models import LsoaChildDensity  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

TS007A_ZIP_URL = "https://www.nomisweb.co.uk/output/census/2021/census2021-ts007a.zip"
TS007A_CSV_NAME = "census2021-ts007a-lsoa.csv"
SAM_DATA_URL = "https://www.arcgis.com/sharing/rest/content/items/a488cb8fc9a74accb63cb52961e456ef/data"
SAM_CSV_NAME = (
    "Standard Area Measurements (Latest) for 2021 Statistical Geographies/"
    "Measurements/SAM_LSOA_DEC_2021_EW_in_KM.csv"
)


def fetch_age_bands() -> dict[str, dict]:
    print(f"Downloading {TS007A_ZIP_URL}")
    resp = httpx.get(TS007A_ZIP_URL, timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    with z.open(TS007A_CSV_NAME) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig").read()
    rows = list(csv.DictReader(io.StringIO(text)))

    p = "Age: Aged "
    by_lsoa = {}
    for row in rows:
        code = row.get("geography code", "")
        if not code:
            continue
        by_lsoa[code] = {
            "age_5_9": int(row.get(f"{p}5 to 9 years", 0) or 0),
            "age_10_14": int(row.get(f"{p}10 to 14 years", 0) or 0),
        }
    print(f"  {len(by_lsoa)} LSOAs with age-band data")
    return by_lsoa


def fetch_area_km2() -> dict[str, float]:
    print(f"Downloading {SAM_DATA_URL}")
    resp = httpx.get(SAM_DATA_URL, timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    with z.open(SAM_CSV_NAME) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig").read()
    rows = list(csv.DictReader(io.StringIO(text)))

    by_lsoa = {}
    for row in rows:
        code = row.get("LSOA21CD", "")
        area = row.get("Land Count (Area in KM2)", "")
        if not code or not area:
            continue
        by_lsoa[code] = float(area)
    print(f"  {len(by_lsoa)} LSOAs with area data")
    return by_lsoa


def build_records() -> list[dict]:
    age_bands = fetch_age_bands()
    areas = fetch_area_km2()
    codes = set(age_bands) & set(areas)
    print(f"  {len(codes)} LSOAs present in both sources")
    return [
        {
            "lsoa_code": code,
            "age_5_9": age_bands[code]["age_5_9"],
            "age_10_14": age_bands[code]["age_10_14"],
            "area_km2": areas[code],
        }
        for code in codes
    ]


def main():
    records = build_records()
    if not records:
        print("No records built - aborting without touching the database.")
        return

    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[LsoaChildDensity.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as session:
        session.query(LsoaChildDensity).delete()
        session.bulk_insert_mappings(LsoaChildDensity, records)
        session.commit()
    print(f"Done. {len(records)} LSOA child-density rows written.")


if __name__ == "__main__":
    main()
