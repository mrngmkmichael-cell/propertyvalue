"""One-time/periodic offline import of English schools + Ofsted
ratings into the `schools` table.

NOT run by the deployed app - run manually from a dev machine when
the data needs refreshing (schools/ratings change slowly; a fresh
import every few months is plenty). Needs `pyproj` installed
locally (not a production dependency - only used here, for
converting British National Grid Easting/Northing to lat/lon).

Sources:
- GIAS (DfE "Get Information About Schools") establishment data,
  regenerated daily at a date-stamped URL:
  https://ea-edubase-api-prod.azurewebsites.net/edubase/downloads/public/edubasealldata{YYYYMMDD}.csv
- Ofsted state-funded school inspection outcomes, republished
  monthly at a URL that changes each time (find the current one at
  https://www.gov.uk/government/statistical-data-sets/monthly-management-information-ofsteds-school-inspections-outcomes
  and update OFSTED_URL below before re-running).

Ofsted's four-point scale: 1 Outstanding, 2 Good, 3 Requires
improvement, 4 Inadequate. Only the latest *graded* (OEIF) inspection
is used - schools with only an ungraded inspection, or none yet,
get no rating rather than a guessed/normalized one.
"""
import csv
import io
import os
import sys
from datetime import date, datetime

import httpx
from pyproj import Transformer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.db import Base, _get_engine  # noqa: E402
from app.models import School  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

GIAS_URL = (
    "https://ea-edubase-api-prod.azurewebsites.net/edubase/downloads/public/"
    f"edubasealldata{date.today():%Y%m%d}.csv"
)
# Update this before re-running - Ofsted republishes at a new URL each month.
OFSTED_URL = (
    "https://assets.publishing.service.gov.uk/media/6a54efeba6586e258d371d9c/"
    "Management_information_-_state-funded_schools_-_latest_inspections_as_at_30_June_2026.csv"
)

RATING_LABELS = {1: "Outstanding", 2: "Good", 3: "Requires improvement", 4: "Inadequate"}

_bng_to_wgs84 = Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value or value == "NULL":
        return None
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        return None


def fetch_ofsted_ratings() -> dict[int, tuple[int, date | None]]:
    print(f"Downloading Ofsted ratings from {OFSTED_URL}")
    resp = httpx.get(OFSTED_URL, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    text = _decode(resp.content)

    ratings: dict[int, tuple[int, date | None]] = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        urn_raw = row.get("URN", "").strip()
        rating_raw = (row.get("Latest OEIF overall effectiveness") or "").strip()
        if not urn_raw.isdigit() or not rating_raw.isdigit():
            continue
        rating = int(rating_raw)
        if rating not in RATING_LABELS:
            continue
        inspection_date = _parse_date(row.get("Publication date of latest OEIF graded inspection", ""))
        ratings[int(urn_raw)] = (rating, inspection_date)

    print(f"  {len(ratings)} schools with a graded Ofsted rating")
    return ratings


def fetch_gias_rows() -> list[dict]:
    print(f"Downloading GIAS establishment data from {GIAS_URL}")
    resp = httpx.get(GIAS_URL, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    text = _decode(resp.content)

    rows = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        if row.get("EstablishmentStatus (name)", "").strip() != "Open":
            continue
        urn_raw = row.get("URN", "").strip()
        easting = row.get("Easting", "").strip()
        northing = row.get("Northing", "").strip()
        if not urn_raw.isdigit() or not easting.isdigit() or not northing.isdigit():
            continue
        rows.append({
            "urn": int(urn_raw),
            "name": row.get("EstablishmentName", "").strip(),
            "phase": row.get("PhaseOfEducation (name)", "").strip(),
            "type_name": row.get("TypeOfEstablishment (name)", "").strip(),
            "postcode": row.get("Postcode", "").strip(),
            "easting": int(easting),
            "northing": int(northing),
        })

    print(f"  {len(rows)} open schools")
    return rows


def build_school_records(gias_rows: list[dict], ratings: dict[int, tuple[int, date | None]]) -> list[dict]:
    records = []
    for row in gias_rows:
        lon, lat = _bng_to_wgs84.transform(row["easting"], row["northing"])
        rating, inspection_date = ratings.get(row["urn"], (None, None))
        records.append({
            "urn": row["urn"],
            "name": row["name"],
            "phase": row["phase"],
            "type_name": row["type_name"],
            "postcode": row["postcode"],
            "latitude": lat,
            "longitude": lon,
            "ofsted_rating": rating,
            "ofsted_rating_label": RATING_LABELS.get(rating, ""),
            "ofsted_inspection_date": inspection_date,
        })
    return records


def load_into_db(records: list[dict]) -> None:
    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[School.__table__])
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        print("Clearing existing schools table...")
        session.query(School).delete()
        session.commit()

        print(f"Inserting {len(records)} schools...")
        batch_size = 2000
        for i in range(0, len(records), batch_size):
            session.execute(School.__table__.insert(), records[i:i + batch_size])
            session.commit()
            print(f"  {min(i + batch_size, len(records))}/{len(records)}")


def main():
    ratings = fetch_ofsted_ratings()
    gias_rows = fetch_gias_rows()
    records = build_school_records(gias_rows, ratings)
    rated = sum(1 for r in records if r["ofsted_rating"])
    print(f"Built {len(records)} school records ({rated} with a rating)")
    load_into_db(records)
    print("Done.")


if __name__ == "__main__":
    main()
