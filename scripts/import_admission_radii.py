"""One-time/periodic offline import of MODELLED school admission
radius estimates into the `school_admission_radii` table.

NOT run by the deployed app - run manually from a dev machine. Needs
`pdfplumber` installed locally (not a production dependency - only
used here, for extracting tables from local authorities' own PDF
"last distance offered" publications, same situation as pyproj in
import_schools.py).

Unlike every other import script in this project, there is no single
source. Each English local authority publishes its own "how places
were offered" report in its own format (PDF table, prose, km, miles,
single or multi-year), at its own URL that typically changes every
year, with no central index. This script is a registry - one fetch
function per authority - meant to be extended authority by authority
over time, not filled in all at once. See SchoolAdmissionRadius's
docstring in app/models.py for what this data actually represents
(a modelled circle, not a real catchment boundary) and why.

To add another authority: write a fetch_<name>() function returning
[{"school_name": ..., "last_distance_miles": ...}, ...], then add it
to _AUTHORITIES below with the authority name (must exactly match
SchoolDetail.local_authority for that area) and academic year label.
"""
import difflib
import io
import os
import re
import sys

import httpx
import pdfplumber

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.db import Base, _get_engine  # noqa: E402
from app.models import School, SchoolAdmissionRadius, SchoolDetail  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

HEADERS = {"User-Agent": "Mozilla/5.0"}
_DISTANCE_RE = re.compile(r"last distance[^\d]*([\d.]+)", re.IGNORECASE)

# Common school-name abbreviations that don't fuzzy-match well
# character-for-character against GIAS's spelled-out EstablishmentName
# (e.g. "Feltham Hill I&N" vs "Feltham Hill Infant and Nursery School").
_ABBREVIATIONS = {
    r"\bI&N\b": "Infant and Nursery",
    r"\bJun\b": "Junior",
    r"\bInf\b": "Infant",
    r"\bJnr\b": "Junior",
    r"\bJMI\b": "Junior Mixed Infant",
    r"\bC of E\b": "Church of England",
    r"\bCE\b": "Church of England",
    r"\bRC\b": "Roman Catholic",
    r"\bRd\b": "Road",
}

# Confidence floor for fuzzy name matching - below this, skip the row
# rather than guess. A wrong match here would draw the wrong school's
# admission circle on the wrong school, which is worse than drawing
# nothing.
_MATCH_CUTOFF = 0.72


def _normalize_school_name(name: str) -> str:
    for pattern, replacement in _ABBREVIATIONS.items():
        name = re.sub(pattern, replacement, name, flags=re.IGNORECASE)
    name = re.sub(r"\bschool\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^a-z0-9 ]", "", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def fetch_hounslow() -> list[dict]:
    """London Borough of Hounslow's "How primary places were offered"
    PDF - republished each autumn at a new file ID, so find the
    current one via hounslow.gov.uk's own site search
    ("last distance offered") before re-running, and update the URL
    below if it 404s.
    """
    url = (
        "https://www.hounslow.gov.uk/downloads/file/11494/"
        "how-primary-places-were-offered-in-september-2025"
    )
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or len(row) < 6 or not row[0] or not row[5]:
                    continue
                match = _DISTANCE_RE.search(row[5].replace("\n", " "))
                if match:
                    records.append({"school_name": row[0].strip(), "last_distance_miles": float(match.group(1))})
    return records


_METRES_PER_MILE = 1609.34

# Matches a heading-style line - ALL CAPS, allowing digits/punctuation
# a school name could contain - used by the text-scan parsers below
# for councils that don't publish a clean table (school name as a
# standalone heading line, followed by free-form prose/mini-tables
# ending in a distance figure).
_HEADING_RE = re.compile(r"^[A-Z0-9][A-Z0-9\s&',.\-]{4,60}$")
_HEADING_SKIP_WORDS = ("OFFICIAL", "PAGE", "ADMISSIONS", "APRIL", "SCHOOL PLACES", "ALLOCATED", "OFFERED", "COUNCIL")
_METRES_OR_MILES_RE = re.compile(r"([\d,]+\.?\d*)\s*(metres|miles|km|m|mi)\b", re.IGNORECASE)


def _scan_headings_for_distance(full_text: str, unit_to_miles: float) -> list[dict]:
    """Generic parser for the "ALL CAPS SCHOOL NAME heading, then
    free-form text/mini-table ending in a distance figure, next
    heading" layout (first seen in Wandsworth's PDF, likely to recur -
    several other authorities format theirs the same way). Takes the
    LAST distance-shaped number before the next heading, since these
    documents put the final "furthest distance offered" figure at the
    end of each school's section."""
    lines = [ln.strip() for ln in full_text.split("\n") if ln.strip()]
    records = []
    current = None
    buffer: list[str] = []

    def _flush():
        if not current or not buffer:
            return
        text = " ".join(buffer)
        matches = list(_METRES_OR_MILES_RE.finditer(text))
        if matches:
            value = float(matches[-1].group(1).replace(",", ""))
            records.append({"school_name": current, "last_distance_miles": value / unit_to_miles})

    for line in lines:
        is_heading = _HEADING_RE.match(line) and not any(w in line for w in _HEADING_SKIP_WORDS)
        if is_heading:
            _flush()
            current = line
            buffer = []
        else:
            buffer.append(line)
    _flush()
    return records


def fetch_wandsworth() -> list[dict]:
    """London Borough of Wandsworth's "How places were offered at
    each Wandsworth school" PDF - republished each spring at a new
    URL each year (find the current one via wandsworth.gov.uk's own
    site search, "how places were allocated"). Distances published
    in metres; each school's name is a standalone heading line rather
    than a table column, so this uses the generic heading-scan parser.
    """
    url = "https://wandsworth.gov.uk/media/xonfunuu/how_places_were_allocated_for_primary_schools_2025.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    full_text = ""
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"
    return _scan_headings_for_distance(full_text, _METRES_PER_MILE)


def fetch_brent() -> list[dict]:
    """London Borough of Brent's "How places were offered - reception"
    PDF - republished each spring at a new file ID (find the current
    one via brent.gov.uk's own search). Table has one row per school
    with a multi-line cell listing every admission criterion (sibling,
    staff, distance, etc.) each with its own distance figure - this
    takes the largest (i.e. the true "furthest anyone got in" figure,
    not just the distance-criterion row) rather than guessing which
    line is which criterion from the flattened cell text.
    """
    url = "https://www.brent.gov.uk/media/16421130/how-places-were-offered-reception-2024.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or len(row) < 3 or not row[0] or not row[2]:
                    continue
                name = row[0].split("\n")[0].strip()
                distances = [float(d) for d in re.findall(r"[\d.]+", row[2])]
                if name and distances:
                    records.append({"school_name": name, "last_distance_miles": max(distances) / _METRES_PER_MILE})
    return records


def fetch_bristol() -> list[dict]:
    """Bristol City Council's "furthest distance table" - a multi-year
    time series (one column per year, 'D' where distance wasn't the
    deciding criterion that year) rather than a single latest-year
    figure. Takes the most recent column with a real value, per
    school, since a 'D' year doesn't mean no distance was recorded -
    it means the school wasn't oversubscribed enough for distance to
    matter that year, which isn't informative for this estimate.
    Republished periodically at bristol.gov.uk - re-check the file ID
    if this URL 404s.
    """
    url = "https://www.bristol.gov.uk/files/documents/3382-furthest-distance-table/file"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or not row[0] or row[0].strip().lower().startswith("name"):
                    continue
                name = row[0].strip()
                # Columns after the name are years, most recent last.
                for cell in reversed(row[1:]):
                    if cell and cell.strip().upper() != "D":
                        try:
                            records.append({"school_name": name, "last_distance_miles": float(cell.strip())})
                        except ValueError:
                            continue
                        break
    return records


def fetch_leeds() -> list[dict]:
    """Leeds City Council's primary school cut-off distances PDF -
    republished each spring at a new URL (find the current one via
    leeds.gov.uk's own search, "cut off distances"). Already in
    miles, one clean row per school - the most directly parseable
    format found so far. Rows reading "All applicants admitted" have
    no distance (not oversubscribed) and are correctly skipped.
    """
    url = "https://www.leeds.gov.uk/sites/default/files/2025-04/primary%20school%20cut%20off%20distances.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or len(row) < 3 or not row[1] or not row[2]:
                    continue
                match = re.match(r"[\d.]+", row[2].strip())
                if match:
                    name = row[1].replace("\n", " ").strip()
                    records.append({"school_name": name, "last_distance_miles": float(match.group(0))})
    return records


def fetch_kirklees() -> list[dict]:
    """Kirklees Council's "Reception by preference and criteria" PDF
    (Huddersfield/Dewsbury area, West Yorkshire) - republished each
    spring at a new file ID (find the current one via kirklees.gov.uk
    admissions pages). Cleanest format found so far: one row per
    school, last column is literally "Distance of the last on-time
    place allocated (metres)".
    """
    url = "https://www.kirklees.gov.uk/beta/admissions/pdf/reception-by-preference-and-criteria-25.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or len(row) < 2 or not row[0]:
                    continue
                distance_cell = (row[-1] or "").strip()
                try:
                    distance = float(distance_cell)
                except ValueError:
                    continue
                name = row[0].replace("\n", " ").strip()
                records.append({"school_name": name, "last_distance_miles": distance / _METRES_PER_MILE})
    return records


_EALING_DISTANCE_RE = re.compile(r"([\d.]+)\s*(of a mile|miles?)", re.IGNORECASE)
_EALING_EXCLUDE_PREFIXES = ("criteria", "number", "places allocated", "no supplementary")


def fetch_ealing() -> list[dict]:
    """London Borough of Ealing's "Primary school on-time offers"
    PDF - republished each spring at a new file ID (find the current
    one via ealing.gov.uk's own search). Already in miles. Most
    schools are one clean row; a handful of faith schools break their
    admissions down by sub-criterion across several rows - this
    tracks the most recent school-name row seen and attaches the
    distance figure from whichever row it actually appears on
    (usually the last oversubscribed criterion), rather than assuming
    a fixed column position that only holds for the simple schools.
    """
    url = "https://www.ealing.gov.uk/download/downloads/id/18843/primary_school_on-time_offers_2025.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            current_name = None
            for row in table:
                cells = [c for c in row if c]
                if not cells:
                    continue
                first = cells[0].strip()
                if not first.lower().startswith(_EALING_EXCLUDE_PREFIXES) and "primary school" in first.lower():
                    current_name = first
                match = _EALING_DISTANCE_RE.search(" ".join(cells))
                if match and current_name:
                    records.append({"school_name": current_name, "last_distance_miles": float(match.group(1))})
                    current_name = None  # one distance per school - stop after the first hit
    return records


def fetch_hackney() -> list[dict]:
    """London Borough of Hackney's "How Places Were Offered - Reception"
    PDF - republished each spring at a new URL each year (find the
    current one via hackney.gov.uk/education's own search). Cleanest
    format found yet: one row per school, second-to-last column is
    literally "Max Distance (last child offered in miles)", already
    in miles.
    """
    url = "https://education.hackney.gov.uk/sites/default/files/document/How%20Places%20Were%20Offered%20-%20Reception%202025.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or len(row) < 12 or not row[0] or not row[-2]:
                    continue
                try:
                    distance = float(row[-2].strip())
                except ValueError:
                    continue
                records.append({"school_name": row[0].strip(), "last_distance_miles": distance})
    return records


def fetch_solihull() -> list[dict]:
    """Solihull Council's "How Reception Places Were Offered" PDF - a
    3-year time series (like Bristol's), republished each spring at a
    new URL (find the current one via solihull.gov.uk's own search).
    Takes the most recent year's figure ("All offered"/N/A means not
    oversubscribed that year, so falls back to the next most recent
    year with a real value).
    """
    url = "https://www.solihull.gov.uk/sites/default/files/2025-04/How-Reception-Places-Were-Offered-23-24-25.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or not row[0] or row[0].strip().lower() in ("", "school"):
                    continue
                name = row[0].strip()
                # Distance columns are at indices 2, 4, 6 (most recent first).
                for idx in (2, 4, 6):
                    if idx >= len(row) or not row[idx]:
                        continue
                    try:
                        records.append({"school_name": name, "last_distance_miles": float(row[idx].strip())})
                        break
                    except ValueError:
                        continue
    return records


def fetch_harrow() -> list[dict]:
    """London Borough of Harrow's "How places were allocated at
    Harrow Schools" PDF - republished each spring at a new file ID
    (find the current one via harrow.gov.uk's own search). One clean
    row per school; "FURTHEST DISTANCE OFFERED IN MILES..." is the
    third-from-last column, already in miles.
    """
    url = "https://www.harrow.gov.uk/downloads/file/33045/Reception_2025___How_places_were_allocated_at_Harrow_Schools.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or len(row) < 3 or not row[0] or not row[-3]:
                    continue
                try:
                    distance = float(row[-3].strip())
                except ValueError:
                    continue
                records.append({"school_name": row[0].replace("\n", " ").strip(), "last_distance_miles": distance})
    return records


def fetch_gloucestershire() -> list[dict]:
    """Gloucestershire County Council's "Primary allocation day
    statistics" PDF - republished each spring at a new file ID (find
    the current one via gloucestershire.gov.uk's own search). One
    clean row per school; "Furthest distance allocated (miles)" is
    column index 5, already in miles. A county council rather than a
    single city/borough - broader geographic spread than the mostly
    urban authorities covered so far.
    """
    url = "https://www.gloucestershire.gov.uk/media/ctgfusdi/primary-allocation-day-statistics-2025-1.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or len(row) < 6 or not row[1] or not row[5]:
                    continue
                try:
                    distance = float(row[5].strip())
                except ValueError:
                    continue
                records.append({"school_name": row[1].replace("\n", " ").strip(), "last_distance_miles": distance})
    return records


# (local authority - must exactly match SchoolDetail.local_authority,
#  academic year label, fetch function)
_AUTHORITIES = [
    ("Hounslow", "2025/26", fetch_hounslow),
    ("Wandsworth", "2024/25", fetch_wandsworth),
    ("Brent", "2023/24", fetch_brent),
    ("Bristol, City of", "varies", fetch_bristol),
    ("Leeds", "2024/25", fetch_leeds),
    ("Kirklees", "2024/25", fetch_kirklees),
    ("Ealing", "2024/25", fetch_ealing),
    ("Hackney", "2024/25", fetch_hackney),
    ("Solihull", "varies", fetch_solihull),
    ("Harrow", "2024/25", fetch_harrow),
    ("Gloucestershire", "2024/25", fetch_gloucestershire),
]


def _match_urn(school_name: str, candidates: dict[str, int]) -> int | None:
    normalized = _normalize_school_name(school_name)
    matches = difflib.get_close_matches(normalized, candidates.keys(), n=1, cutoff=_MATCH_CUTOFF)
    return candidates[matches[0]] if matches else None


def build_records(session) -> list[dict]:
    records = []
    for authority, academic_year, fetch_fn in _AUTHORITIES:
        print(f"Fetching {authority}...")
        rows = fetch_fn()
        print(f"  {len(rows)} schools with a distance figure in the source file")

        schools_in_la = session.execute(
            select(School.urn, School.name)
            .join(SchoolDetail, SchoolDetail.urn == School.urn)
            .where(SchoolDetail.local_authority == authority)
        ).all()
        candidates = {_normalize_school_name(name): urn for urn, name in schools_in_la}

        matched = 0
        unmatched = []
        for row in rows:
            urn = _match_urn(row["school_name"], candidates)
            if urn is None:
                unmatched.append(row["school_name"])
                continue
            records.append({
                "urn": urn,
                "academic_year": academic_year,
                "last_distance_miles": row["last_distance_miles"],
                "source_authority": authority,
            })
            matched += 1

        print(f"  matched {matched}/{len(rows)} to a school in our database (before de-duplication)")
        if unmatched:
            print(f"  unmatched, skipped rather than guessed: {unmatched}")

    # A school can appear twice within one authority's own source file
    # (e.g. split across a page boundary in the raw PDF text) - keep
    # the first occurrence per URN rather than let a duplicate insert
    # crash the whole import.
    by_urn: dict[int, dict] = {}
    duplicates = 0
    for r in records:
        if r["urn"] in by_urn:
            duplicates += 1
            continue
        by_urn[r["urn"]] = r
    if duplicates:
        print(f"Dropped {duplicates} duplicate URN(s) (same school matched more than once)")

    return list(by_urn.values())


def load_into_db(records: list[dict]) -> None:
    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[SchoolAdmissionRadius.__table__])
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        print("Clearing existing school_admission_radii table...")
        session.query(SchoolAdmissionRadius).delete()
        session.commit()

        print(f"Inserting {len(records)} rows...")
        if records:
            session.execute(SchoolAdmissionRadius.__table__.insert(), records)
            session.commit()


def main():
    engine = _get_engine()
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        records = build_records(session)
    load_into_db(records)
    print("Done.")


if __name__ == "__main__":
    main()
