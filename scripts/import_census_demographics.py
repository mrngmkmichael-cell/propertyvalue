"""One-time/periodic offline import of nine more Census 2021 topic
summary tables into their own LSOA-keyed tables, following the exact
pattern of scripts/import_census_stats.py (occupation/qualification).

NOT run by the deployed app - run manually from a dev machine.
Static data (next refresh is the 2031 census).

Sources - all Nomis's official Census 2021 bulk downloads, LSOA level:
- TS007A Age (5-year bands): https://www.nomisweb.co.uk/datasets/c2021ts007a
- TS044 Accommodation type: https://www.nomisweb.co.uk/datasets/c2021ts044
- TS054 Tenure: https://www.nomisweb.co.uk/datasets/c2021ts054
- TS052 Occupancy rating (bedrooms): https://www.nomisweb.co.uk/datasets/c2021ts052
- TS021 Ethnic group: https://www.nomisweb.co.uk/datasets/c2021ts021
- TS030 Religion: https://www.nomisweb.co.uk/datasets/c2021ts030
- TS004 Country of birth: https://www.nomisweb.co.uk/datasets/c2021ts004
- TS037 General health: https://www.nomisweb.co.uk/datasets/c2021ts037
- TS002 Marital/civil partnership status: https://www.nomisweb.co.uk/datasets/c2021ts002
- TS062 NS-SEC: https://www.nomisweb.co.uk/datasets/c2021ts062
  (NOT the same as commercial "social grade" AB/C1/C2/DE - that scheme
  isn't published as a bulk zip, only via a long-format API query that
  needs pivoting; NS-SEC is the closest free equivalent, see models.py)

Two of these (TS004, TS002) have "; measures: Value" suffixed column
headers unlike the others - handled per-table below. TS002's LSOA csv
inside the zip is also, unusually, named with a double dot
("census2021-ts002-lsoa..csv") - this is a real quirk in Nomis's own
published file, confirmed by listing the zip contents, not a typo here.
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
from app.models import (  # noqa: E402
    AgeProfile, CountryOfBirth, Ethnicity, GeneralHealth, HousingType, MaritalStatus, OccupancyRating,
    Religion, SocioeconomicClassification, Tenure,
)
from sqlalchemy.orm import sessionmaker  # noqa: E402

ZIP_BASE = "https://www.nomisweb.co.uk/output/census/2021/census2021-{ts}.zip"


def _fetch_lsoa_csv(ts: str, csv_name: str) -> list[dict]:
    url = ZIP_BASE.format(ts=ts)
    print(f"Downloading {url}")
    resp = httpx.get(url, timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(resp.content))
    with z.open(csv_name) as f:
        text = io.TextIOWrapper(f, encoding="utf-8-sig").read()
    return list(csv.DictReader(io.StringIO(text)))


def _sum_cols(row: dict, cols: list[str]) -> int:
    return sum(int(row.get(c, 0) or 0) for c in cols)


def build_age_profile_records() -> list[dict]:
    rows = _fetch_lsoa_csv("ts007a", "census2021-ts007a-lsoa.csv")
    p = "Age: Aged "
    records = []
    for row in rows:
        records.append({
            "lsoa_code": row["geography code"],
            "total": int(row.get("Age: Total", 0) or 0),
            "under_15": _sum_cols(row, [f"{p}4 years and under", f"{p}5 to 9 years", f"{p}10 to 14 years"]),
            "age_15_24": _sum_cols(row, [f"{p}15 to 19 years", f"{p}20 to 24 years"]),
            "age_25_44": _sum_cols(row, [f"{p}{a} to {a + 4} years" for a in (25, 30, 35, 40)]),
            "age_45_64": _sum_cols(row, [f"{p}{a} to {a + 4} years" for a in (45, 50, 55, 60)]),
            "age_65_84": _sum_cols(row, [f"{p}{a} to {a + 4} years" for a in (65, 70, 75, 80)]),
            "age_85_plus": int(row.get(f"{p}85 years and over", 0) or 0),
        })
    print(f"  {len(records)} LSOAs (age profile)")
    return records


def build_housing_type_records() -> list[dict]:
    rows = _fetch_lsoa_csv("ts044", "census2021-ts044-lsoa.csv")
    p = "Accommodation type: "
    flat_cols = [
        f"{p}In a purpose-built block of flats or tenement",
        f"{p}Part of a converted or shared house, including bedsits",
        f"{p}Part of another converted building, for example, former school, church or warehouse",
        f"{p}In a commercial building, for example, in an office building, hotel or over a shop",
    ]
    records = []
    for row in rows:
        records.append({
            "lsoa_code": row["geography code"],
            "total": int(row.get(f"{p}Total: All households", 0) or 0),
            "detached": int(row.get(f"{p}Detached", 0) or 0),
            "semi_detached": int(row.get(f"{p}Semi-detached", 0) or 0),
            "terraced": int(row.get(f"{p}Terraced", 0) or 0),
            "flat_or_converted": _sum_cols(row, flat_cols),
            "caravan_or_other": int(row.get(f"{p}A caravan or other mobile or temporary structure", 0) or 0),
        })
    print(f"  {len(records)} LSOAs (housing type)")
    return records


def build_tenure_records() -> list[dict]:
    rows = _fetch_lsoa_csv("ts054", "census2021-ts054-lsoa.csv")
    p = "Tenure of household: "
    records = []
    for row in rows:
        records.append({
            "lsoa_code": row["geography code"],
            "total": int(row.get(f"{p}Total: All households", 0) or 0),
            "owned_outright": int(row.get(f"{p}Owned: Owns outright", 0) or 0),
            "owned_mortgage": int(row.get(f"{p}Owned: Owns with a mortgage or loan", 0) or 0),
            "shared_ownership": int(row.get(f"{p}Shared ownership", 0) or 0),
            "social_rented": int(row.get(f"{p}Social rented", 0) or 0),
            "private_rented": int(row.get(f"{p}Private rented", 0) or 0),
            "rent_free": int(row.get(f"{p}Lives rent free", 0) or 0),
        })
    print(f"  {len(records)} LSOAs (tenure)")
    return records


def build_occupancy_rating_records() -> list[dict]:
    rows = _fetch_lsoa_csv("ts052", "census2021-ts052-lsoa.csv")
    p = "Occupancy rating for bedrooms: "
    records = []
    for row in rows:
        records.append({
            "lsoa_code": row["geography code"],
            "total": int(row.get(f"{p}Total: All households", 0) or 0),
            "plus_2_or_more": int(row.get(f"{p}Occupancy rating of bedrooms: +2 or more", 0) or 0),
            "plus_1": int(row.get(f"{p}Occupancy rating of bedrooms: +1", 0) or 0),
            "exact": int(row.get(f"{p}Occupancy rating of bedrooms: 0", 0) or 0),
            "minus_1": int(row.get(f"{p}Occupancy rating of bedrooms: -1", 0) or 0),
            "minus_2_or_less": int(row.get(f"{p}Occupancy rating of bedrooms: -2 or less", 0) or 0),
        })
    print(f"  {len(records)} LSOAs (occupancy rating)")
    return records


def build_ethnicity_records() -> list[dict]:
    rows = _fetch_lsoa_csv("ts021", "census2021-ts021-lsoa.csv")
    p = "Ethnic group: "
    records = []
    for row in rows:
        records.append({
            "lsoa_code": row["geography code"],
            "total": int(row.get(f"{p}Total: All usual residents", 0) or 0),
            "asian": int(row.get(f"{p}Asian, Asian British or Asian Welsh", 0) or 0),
            "black": int(row.get(f"{p}Black, Black British, Black Welsh, Caribbean or African", 0) or 0),
            "mixed": int(row.get(f"{p}Mixed or Multiple ethnic groups", 0) or 0),
            "white": int(row.get(f"{p}White", 0) or 0),
            "other": int(row.get(f"{p}Other ethnic group", 0) or 0),
        })
    print(f"  {len(records)} LSOAs (ethnicity)")
    return records


def build_religion_records() -> list[dict]:
    rows = _fetch_lsoa_csv("ts030", "census2021-ts030-lsoa.csv")
    p = "Religion: "
    records = []
    for row in rows:
        records.append({
            "lsoa_code": row["geography code"],
            "total": int(row.get(f"{p}Total: All usual residents", 0) or 0),
            "no_religion": int(row.get(f"{p}No religion", 0) or 0),
            "christian": int(row.get(f"{p}Christian", 0) or 0),
            "buddhist": int(row.get(f"{p}Buddhist", 0) or 0),
            "hindu": int(row.get(f"{p}Hindu", 0) or 0),
            "jewish": int(row.get(f"{p}Jewish", 0) or 0),
            "muslim": int(row.get(f"{p}Muslim", 0) or 0),
            "sikh": int(row.get(f"{p}Sikh", 0) or 0),
            "other_religion": int(row.get(f"{p}Other religion", 0) or 0),
            "not_answered": int(row.get(f"{p}Not answered", 0) or 0),
        })
    print(f"  {len(records)} LSOAs (religion)")
    return records


def build_country_of_birth_records() -> list[dict]:
    rows = _fetch_lsoa_csv("ts004", "census2021-ts004-lsoa.csv")
    p = "Country of birth: "
    s = "; measures: Value"
    records = []
    for row in rows:
        records.append({
            "lsoa_code": row["geography code"],
            "total": int(row.get(f"{p}Total{s}", 0) or 0),
            "uk": int(row.get(f"{p}Europe: United Kingdom{s}", 0) or 0),
            "eu": int(row.get(f"{p}Europe: EU countries{s}", 0) or 0),
            "non_eu_europe": int(row.get(f"{p}Europe: Non-EU countries{s}", 0) or 0),
            "africa": int(row.get(f"{p}Africa{s}", 0) or 0),
            "middle_east_asia": int(row.get(f"{p}Middle East and Asia{s}", 0) or 0),
            "americas_caribbean": int(row.get(f"{p}The Americas and the Caribbean{s}", 0) or 0),
            "oceania_other": int(row.get(f"{p}Antarctica and Oceania (including Australasia) and Other{s}", 0) or 0),
            "british_overseas": int(row.get(f"{p}British Overseas {s}", 0) or 0),
        })
    print(f"  {len(records)} LSOAs (country of birth)")
    return records


def build_general_health_records() -> list[dict]:
    rows = _fetch_lsoa_csv("ts037", "census2021-ts037-lsoa.csv")
    p = "General health: "
    records = []
    for row in rows:
        records.append({
            "lsoa_code": row["geography code"],
            "total": int(row.get(f"{p}Total: All usual residents", 0) or 0),
            "very_good": int(row.get(f"{p}Very good health", 0) or 0),
            "good": int(row.get(f"{p}Good health", 0) or 0),
            "fair": int(row.get(f"{p}Fair health", 0) or 0),
            "bad": int(row.get(f"{p}Bad health", 0) or 0),
            "very_bad": int(row.get(f"{p}Very bad health", 0) or 0),
        })
    print(f"  {len(records)} LSOAs (general health)")
    return records


def build_marital_status_records() -> list[dict]:
    rows = _fetch_lsoa_csv("ts002", "census2021-ts002-lsoa..csv")
    p = "Marital and civil partnership status: "
    s = "; measures: Value"
    records = []
    for row in rows:
        records.append({
            "lsoa_code": row["geography code"],
            "total": int(row.get(f"{p}Total{s}", 0) or 0),
            "never_married": int(row.get(f"{p}Never married and never registered a civil partnership{s}", 0) or 0),
            "married_or_civil_partnership": int(row.get(f"{p}Married or in a registered civil partnership{s}", 0) or 0),
            "separated": int(row.get(f"{p}Separated, but still legally married or still legally in a civil partnership{s}", 0) or 0),
            "divorced_or_dissolved": int(row.get(f"{p}Divorced or civil partnership dissolved{s}", 0) or 0),
            "widowed_or_surviving_partner": int(row.get(f"{p}Widowed or surviving civil partnership partner{s}", 0) or 0),
        })
    print(f"  {len(records)} LSOAs (marital status)")
    return records


def build_socioeconomic_classification_records() -> list[dict]:
    rows = _fetch_lsoa_csv("ts062", "census2021-ts062-lsoa.csv")
    p = "National Statistics Socio-economic Classification (NS-SEC): "
    records = []
    for row in rows:
        records.append({
            "lsoa_code": row["geography code"],
            "total": int(row.get(f"{p}Total: All usual residents aged 16 years and over", 0) or 0),
            "higher_managerial_professional": int(row.get(
                f"{p}L1, L2 and L3 Higher managerial, administrative and professional occupations", 0) or 0),
            "lower_managerial_professional": int(row.get(
                f"{p}L4, L5 and L6 Lower managerial, administrative and professional occupations", 0) or 0),
            "intermediate": int(row.get(f"{p}L7 Intermediate occupations", 0) or 0),
            "small_employers_self_employed": int(row.get(
                f"{p}L8 and L9 Small employers and own account workers", 0) or 0),
            "lower_supervisory_technical": int(row.get(
                f"{p}L10 and L11 Lower supervisory and technical occupations", 0) or 0),
            "semi_routine": int(row.get(f"{p}L12 Semi-routine occupations", 0) or 0),
            "routine": int(row.get(f"{p}L13 Routine occupations", 0) or 0),
            "never_worked_long_term_unemployed": int(row.get(
                f"{p}L14.1 and L14.2 Never worked and long-term unemployed", 0) or 0),
            "full_time_students": int(row.get(f"{p}L15 Full-time students", 0) or 0),
        })
    print(f"  {len(records)} LSOAs (NS-SEC)")
    return records


TABLES = [
    (AgeProfile, build_age_profile_records),
    (HousingType, build_housing_type_records),
    (Tenure, build_tenure_records),
    (OccupancyRating, build_occupancy_rating_records),
    (Ethnicity, build_ethnicity_records),
    (Religion, build_religion_records),
    (CountryOfBirth, build_country_of_birth_records),
    (GeneralHealth, build_general_health_records),
    (MaritalStatus, build_marital_status_records),
    (SocioeconomicClassification, build_socioeconomic_classification_records),
]


def main():
    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[model.__table__ for model, _ in TABLES])
    SessionLocal = sessionmaker(bind=engine)

    for model, builder in TABLES:
        records = builder()
        with SessionLocal() as session:
            print(f"Clearing existing {model.__tablename__}...")
            session.query(model).delete()
            session.commit()
            print(f"Inserting {len(records)} rows into {model.__tablename__}...")
            session.execute(model.__table__.insert(), records)
            session.commit()

    print("Done.")


if __name__ == "__main__":
    main()
