"""One-time/periodic offline import of English schools + Ofsted
ratings into the `schools` and `school_details` tables.

NOT run by the deployed app - run manually from a dev machine when
the data needs refreshing (schools/ratings change slowly; a fresh
import every few months is plenty). Needs `pyproj` installed
locally (not a production dependency - only used here, for
converting British National Grid Easting/Northing to lat/lon).

Sources:
- GIAS (DfE "Get Information About Schools") establishment data,
  regenerated daily at a date-stamped URL:
  https://ea-edubase-api-prod.azurewebsites.net/edubase/downloads/public/edubasealldata{YYYYMMDD}.csv
  Only ~5 of its ~150 columns were used until this pass added address,
  contact, admissions, capacity and trust fields - the rest (SEN
  provision detail, boarding, federation, etc.) still isn't captured;
  add more columns to _GIAS_FIELDS below if a future pass needs them.
- Ofsted state-funded school inspection outcomes, republished
  monthly at a URL that changes each time (find the current one at
  https://www.gov.uk/government/statistical-data-sets/monthly-management-information-ofsteds-school-inspections-outcomes
  and update OFSTED_URL below before re-running). Also only the
  overall rating was used until this pass added the per-category
  judgement breakdown (Quality of Education, Behaviour and Attitudes,
  Personal Development, Leadership and Management, Safeguarding,
  Early Years, Sixth Form) and the IDACI deprivation quintile - all
  present in the same file, previously discarded.

Ofsted's four-point scale: 1 Outstanding, 2 Good, 3 Requires
improvement, 4 Inadequate. Only the latest *graded* (OEIF) inspection
is used for the overall rating and category judgements - schools with
only an ungraded inspection, or none yet, get no rating rather than a
guessed/normalized one. The category judgement columns use "9" as
Ofsted's sentinel for "not applicable" (e.g. sixth form provision at
a school with no sixth form) - parsed as None, not a real grade.
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
from app.models import School, SchoolDetail  # noqa: E402
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


def _text(value: str) -> str:
    value = (value or "").strip()
    return "" if value == "NULL" else value


def _int(value: str) -> int | None:
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def _judgement(value: str) -> int | None:
    """A 1-4 Ofsted category grade, or None for blank/"9" (Ofsted's
    own "not applicable" sentinel, e.g. sixth form provision at a
    school with no sixth form - not a real grade to display."""
    n = _int(value)
    return n if n in RATING_LABELS else None


def fetch_ofsted_ratings() -> dict[int, dict]:
    print(f"Downloading Ofsted ratings from {OFSTED_URL}")
    resp = httpx.get(OFSTED_URL, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    text = _decode(resp.content)

    ratings: dict[int, dict] = {}
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        urn_raw = row.get("URN", "").strip()
        if not urn_raw.isdigit():
            continue
        urn = int(urn_raw)

        rating = _judgement(row.get("Latest OEIF overall effectiveness", ""))
        ratings[urn] = {
            "rating": rating,
            "inspection_date": _parse_date(row.get("Publication date of latest OEIF graded inspection", "")),
            "quality_of_education": _judgement(row.get("Latest OEIF quality of education", "")),
            "behaviour_attitudes": _judgement(row.get("Latest OEIF behaviour and attitudes", "")),
            "personal_development": _judgement(row.get("Latest OEIF personal development", "")),
            "leadership_management": _judgement(
                row.get("Latest OEIF effectiveness of leadership and management", "")
            ),
            "safeguarding_effective": _text(row.get("Latest OEIF  safeguarding is effective?", "")),
            "early_years_provision": _judgement(row.get("Latest OEIF early years provision (where applicable)", "")),
            "sixth_form_provision": _judgement(row.get("Latest OEIF sixth form provision (where applicable)", "")),
            "ungraded_inspection_date": _parse_date(row.get("Date of latest ungraded inspection", "")),
            "trust_name": _text(row.get("Multi-academy trust name", "")),
            "idaci_quintile": _int(row.get("The income deprivation affecting children index (IDACI) quintile", "")),
        }

    rated = sum(1 for r in ratings.values() if r["rating"])
    print(f"  {len(ratings)} schools in the Ofsted file, {rated} with a graded rating")
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
            "street": _text(row.get("Street", "")),
            "locality": _text(row.get("Locality", "")),
            "town": _text(row.get("Town", "")),
            "county": _text(row.get("County (name)", "")),
            "phone": _text(row.get("TelephoneNum", "")),
            "website": _text(row.get("SchoolWebsite", "")),
            "head_teacher": " ".join(
                p for p in (
                    _text(row.get("HeadTitle (name)", "")),
                    _text(row.get("HeadFirstName", "")),
                    _text(row.get("HeadLastName", "")),
                ) if p
            ),
            "gender": _text(row.get("Gender (name)", "")),
            "religious_character": _text(row.get("ReligiousCharacter (name)", "")),
            "age_low": _int(row.get("StatutoryLowAge", "")),
            "age_high": _int(row.get("StatutoryHighAge", "")),
            "admissions_policy": _text(row.get("AdmissionsPolicy (name)", "")),
            "has_sixth_form": _text(row.get("OfficialSixthForm (name)", "")),
            "school_capacity": _int(row.get("SchoolCapacity", "")),
            "number_on_roll": _int(row.get("NumberOfPupils", "")),
            "trust_name": _text(row.get("Trusts (name)", "")),
            "local_authority": _text(row.get("LA (name)", "")),
        })

    print(f"  {len(rows)} open schools")
    return rows


def build_records(gias_rows: list[dict], ratings: dict[int, dict]) -> tuple[list[dict], list[dict]]:
    school_records = []
    detail_records = []
    for row in gias_rows:
        lon, lat = _bng_to_wgs84.transform(row["easting"], row["northing"])
        r = ratings.get(row["urn"], {})

        school_records.append({
            "urn": row["urn"],
            "name": row["name"],
            "phase": row["phase"],
            "type_name": row["type_name"],
            "postcode": row["postcode"],
            "latitude": lat,
            "longitude": lon,
            "ofsted_rating": r.get("rating"),
            "ofsted_rating_label": RATING_LABELS.get(r.get("rating"), ""),
            "ofsted_inspection_date": r.get("inspection_date"),
        })

        detail_records.append({
            "urn": row["urn"],
            "street": row["street"],
            "locality": row["locality"],
            "town": row["town"],
            "county": row["county"],
            "phone": row["phone"],
            "website": row["website"],
            "head_teacher": row["head_teacher"],
            "gender": row["gender"],
            "religious_character": row["religious_character"],
            "age_low": row["age_low"],
            "age_high": row["age_high"],
            "admissions_policy": row["admissions_policy"],
            "has_sixth_form": row["has_sixth_form"],
            "school_capacity": row["school_capacity"],
            "number_on_roll": row["number_on_roll"],
            # GIAS's own Trusts field is blank for plenty of academies
            # that ARE in a trust per the Ofsted file - prefer
            # whichever source actually has a value.
            "trust_name": row["trust_name"] or r.get("trust_name", ""),
            "local_authority": row["local_authority"],
            "ofsted_quality_of_education": r.get("quality_of_education"),
            "ofsted_behaviour_attitudes": r.get("behaviour_attitudes"),
            "ofsted_personal_development": r.get("personal_development"),
            "ofsted_leadership_management": r.get("leadership_management"),
            "ofsted_safeguarding_effective": r.get("safeguarding_effective", ""),
            "ofsted_early_years_provision": r.get("early_years_provision"),
            "ofsted_sixth_form_provision": r.get("sixth_form_provision"),
            "ofsted_ungraded_inspection_date": r.get("ungraded_inspection_date"),
            "idaci_quintile": r.get("idaci_quintile"),
        })

    return school_records, detail_records


def load_into_db(school_records: list[dict], detail_records: list[dict]) -> None:
    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[School.__table__, SchoolDetail.__table__])
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        print("Clearing existing schools/school_details tables...")
        session.query(SchoolDetail).delete()
        session.query(School).delete()
        session.commit()

        batch_size = 2000
        print(f"Inserting {len(school_records)} schools...")
        for i in range(0, len(school_records), batch_size):
            session.execute(School.__table__.insert(), school_records[i:i + batch_size])
            session.commit()
            print(f"  {min(i + batch_size, len(school_records))}/{len(school_records)}")

        print(f"Inserting {len(detail_records)} school details...")
        for i in range(0, len(detail_records), batch_size):
            session.execute(SchoolDetail.__table__.insert(), detail_records[i:i + batch_size])
            session.commit()
            print(f"  {min(i + batch_size, len(detail_records))}/{len(detail_records)}")


def main():
    ratings = fetch_ofsted_ratings()
    gias_rows = fetch_gias_rows()
    school_records, detail_records = build_records(gias_rows, ratings)
    rated = sum(1 for r in school_records if r["ofsted_rating"])
    print(f"Built {len(school_records)} school records ({rated} with a rating)")
    load_into_db(school_records, detail_records)
    print("Done.")


if __name__ == "__main__":
    main()
