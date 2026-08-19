"""One-time/periodic offline import computing a *modelled* catchment
radius for every Primary/Secondary school - a fallback for schools
whose council hasn't published a real admission-distance figure (see
app/models.py's SchoolCatchmentEstimate docstring).

Method: local age-appropriate population density (LsoaChildDensity,
from scripts/import_lsoa_child_density.py - run that first) divided
into the school's approximate per-year-group intake
(SchoolDetail.school_capacity / number of year groups), converted to
a circle radius via Area = intake / density. The same class of
technique competitor sites like Locrating use as their default
catchment indicator, since nobody outside a school/council has access
to real pupil home addresses (protected personal data) - explicitly
NOT the same as a real published admission-distance figure, and never
presented as one.

NOT run by the deployed app - run manually from a dev machine, after
scripts/import_lsoa_child_density.py. Static data (schools open/close
occasionally, but capacity/location don't change often enough to
warrant re-running more than yearly alongside the other school data
imports).
"""
import math
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import Base, _get_engine  # noqa: E402
from app.models import LsoaChildDensity, School, SchoolCatchmentEstimate, SchoolDetail  # noqa: E402
from app.services.schools_db import _phase_group  # noqa: E402

POSTCODES_IO_BULK_URL = "https://api.postcodes.io/postcodes"
BULK_BATCH_SIZE = 100
MILES_PER_KM = 0.621371


def _fetch_candidate_schools(session) -> list[dict]:
    rows = session.execute(
        select(
            School.urn, School.latitude, School.longitude, School.phase,
            SchoolDetail.school_capacity, SchoolDetail.age_low, SchoolDetail.age_high,
        ).join(SchoolDetail, SchoolDetail.urn == School.urn)
    ).all()

    candidates = []
    for urn, lat, lon, phase, capacity, age_low, age_high in rows:
        group = _phase_group(phase)
        if group not in ("Primary", "Secondary"):
            continue
        if not capacity or age_low is None or age_high is None or age_high < age_low:
            continue
        if lat is None or lon is None:
            continue
        candidates.append({
            "urn": urn, "lat": lat, "lon": lon, "phase_group": group,
            "intake_per_year": capacity / (age_high - age_low + 1),
        })
    return candidates


def _bulk_reverse_geocode(client: httpx.Client, schools: list[dict]) -> dict[int, str]:
    """URN -> LSOA code, for a batch of up to BULK_BATCH_SIZE schools."""
    payload = {"geolocations": [{"longitude": s["lon"], "latitude": s["lat"], "limit": 1} for s in schools]}
    resp = client.post(POSTCODES_IO_BULK_URL, json=payload, timeout=30)
    resp.raise_for_status()
    results = resp.json()["result"]

    lsoa_by_urn = {}
    for school, entry in zip(schools, results):
        matches = entry.get("result")
        if not matches:
            continue
        lsoa_by_urn[school["urn"]] = matches[0]["codes"]["lsoa"]
    return lsoa_by_urn


def build_records() -> list[dict]:
    engine = _get_engine()
    Session = sessionmaker(bind=engine)
    with Session() as session:
        candidates = _fetch_candidate_schools(session)
        print(f"{len(candidates)} Primary/Secondary schools with a usable capacity + age range")

        density_by_lsoa = {
            row.lsoa_code: row for row in session.scalars(select(LsoaChildDensity))
        }
    print(f"{len(density_by_lsoa)} LSOAs with child-density data available")

    lsoa_by_urn: dict[int, str] = {}
    with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}) as client:
        for i in range(0, len(candidates), BULK_BATCH_SIZE):
            batch = candidates[i:i + BULK_BATCH_SIZE]
            try:
                lsoa_by_urn.update(_bulk_reverse_geocode(client, batch))
            except httpx.HTTPError as e:
                print(f"  batch at offset {i} failed ({e}), skipping")
            if (i // BULK_BATCH_SIZE) % 20 == 0:
                print(f"  reverse-geocoded {i + len(batch)}/{len(candidates)}")
            time.sleep(0.1)

    print(f"Resolved LSOA for {len(lsoa_by_urn)}/{len(candidates)} schools")

    records = []
    for school in candidates:
        lsoa_code = lsoa_by_urn.get(school["urn"])
        density_row = density_by_lsoa.get(lsoa_code) if lsoa_code else None
        if not density_row or not density_row.area_km2:
            continue

        band_count = density_row.age_5_9 if school["phase_group"] == "Primary" else density_row.age_10_14
        if not band_count:
            continue

        density_per_km2 = (band_count / 5) / density_row.area_km2  # /5: 5-year band -> single year of age
        if density_per_km2 <= 0:
            continue

        radius_km = math.sqrt(school["intake_per_year"] / (math.pi * density_per_km2))
        records.append({
            "urn": school["urn"],
            "radius_miles": round(radius_km * MILES_PER_KM, 2),
            "lsoa_code": lsoa_code,
        })

    print(f"Computed an estimate for {len(records)} schools")
    return records


def main():
    records = build_records()
    if not records:
        print("No records built - aborting without touching the database.")
        return

    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[SchoolCatchmentEstimate.__table__])
    Session = sessionmaker(bind=engine)
    with Session() as session:
        session.query(SchoolCatchmentEstimate).delete()
        session.bulk_insert_mappings(SchoolCatchmentEstimate, records)
        session.commit()
    print(f"Done. {len(records)} modelled catchment-estimate rows written.")


if __name__ == "__main__":
    main()
