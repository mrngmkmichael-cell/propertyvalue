"""One-time/periodic offline import of MODELLED school admission
radius estimates into the `school_admission_radii` table.

NOT run by the deployed app - run manually from a dev machine. Needs
`pdfplumber` and `openpyxl` installed locally (not production
dependencies - only used here, for extracting tables from local
authorities' own PDF/Excel "last distance offered" publications, same
situation as pyproj in import_schools.py).

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
import csv
import difflib
import io
import os
import re
import sys

import httpx
import openpyxl
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
    r"\bCEVCP\b": "Church of England Voluntary Controlled Primary",
    r"\bCE\b": "Church of England",
    r"\bRC\b": "Roman Catholic",
    r"\bRd\b": "Road",
    r"\bCP\b": "Community Primary",
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


_BIRMINGHAM_DISTANCE_RE = re.compile(r"([\d,]+\.?\d*)\s*metres", re.IGNORECASE)


def fetch_birmingham() -> list[dict]:
    """Birmingham City Council's "Primary offers" PDF - republished
    each year at a new file ID (find the current one via
    birmingham.gov.uk's own search, "breakdown of primary school
    offers"). One row per school; the last column is "Cut Off
    Distance YYYY (where applicable)" - sometimes a plain "676
    metres", sometimes "Faith (4166 metres)" (still a real distance,
    just alongside the admitting criterion) - a regex extracts the
    number regardless of what text surrounds it, and rows reading
    "All Applicants" or similar with no metres figure are correctly
    skipped. England's second-largest city by population.
    """
    url = "https://www.birmingham.gov.uk/download/downloads/id/29456/primary_offers_2024.pdf"
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
                if not row or not row[0] or not row[-1]:
                    continue
                match = _BIRMINGHAM_DISTANCE_RE.search(row[-1].replace(",", ""))
                if match:
                    name = row[0].replace("\n", " ").strip()
                    records.append({"school_name": name, "last_distance_miles": float(match.group(1)) / _METRES_PER_MILE})
    return records


_NEWCASTLE_RECEPTION_ROW_RE = re.compile(
    r'<p id="([^"]+)"><strong>[^<]+</strong></p>.*?'
    r'Farthest distance from school</td><td[^>]*>([^<]*)</td>',
    re.DOTALL,
)
_NEWCASTLE_TRANSFER_SCHOOL_RE = re.compile(r"<h3>([^<]+)</h3>(.*?)(?=<h3>|\Z)", re.DOTALL)
_NEWCASTLE_DISTANCE_MILES_RE = re.compile(r"([\d.]+)\s*miles?", re.IGNORECASE)


def fetch_newcastle() -> list[dict]:
    """Newcastle City Council publishes its "how places were allocated"
    results as plain HTML pages (not PDFs) - one for Reception
    (primary) and one for Transfer (secondary), both republished each
    year at a new URL suffix (find the current ones via
    newcastle.gov.uk's own admissions pages). Reception page is one
    big table with a "Farthest distance from school" row per school,
    already in miles; Transfer page uses an "<h3>School Name</h3>"
    heading per school followed by a "Last distance offered in
    Category N: X.XXX miles" paragraph. Schools reading "School not
    filled on distance criteria" have no distance and are correctly
    skipped.
    """
    records = []

    reception_url = "https://newcastle.gov.uk/services/how-reception-places-were-allocated-2026"
    print(f"  Downloading {reception_url}")
    resp = httpx.get(reception_url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    for name, value in _NEWCASTLE_RECEPTION_ROW_RE.findall(resp.text):
        match = _NEWCASTLE_DISTANCE_MILES_RE.search(value)
        if match:
            records.append({"school_name": name.strip(), "last_distance_miles": float(match.group(1))})

    transfer_url = (
        "https://www.newcastle.gov.uk/services/schools-learning-and-childcare/"
        "apply-school-place/information-about-how-reception-and-6"
    )
    print(f"  Downloading {transfer_url}")
    resp = httpx.get(transfer_url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    for name, section in _NEWCASTLE_TRANSFER_SCHOOL_RE.findall(resp.text):
        match = _NEWCASTLE_DISTANCE_MILES_RE.search(section)
        if match:
            records.append({"school_name": name.strip(), "last_distance_miles": float(match.group(1))})

    return records


_SURREY_SCHOOL_RE = re.compile(
    r"^([A-Za-z][^\n]{2,90})\nDfE No: [\d/]+\n(.*?)(?=\n[A-Za-z][^\n]{2,90}\nDfE No: |\Z)",
    re.MULTILINE | re.DOTALL,
)
_SURREY_DISTANCE_RE = re.compile(r"Distance[^=\n]{0,25}=\s*([\d.]+)\s*km")

# One PDF per district (plus one for Junior schools, one for
# Secondary) - Surrey is a county council (like Gloucestershire) so
# GIAS's local_authority for all of these is simply "Surrey", but the
# council itself only publishes allocation figures broken down by
# district, hence the long URL list.
_SURREY_URLS = [
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0010/521389/FINAL-Elmbridge-Primary-allocation-figures-September-2026-V1.pdf",
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0020/521390/FINAL-Epsom-and-Ewell-Primary-allocation-figures-September-2026-V1.pdf",
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0003/521391/FINAL-Guildford-Primary-allocation-figures-September-2026-V1.pdf",
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0004/521392/FINAL-Mole-Valley-Primary-allocation-figures-September-2026-V1.pdf",
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0005/521393/FINAL-Reigate-and-Banstead-Primary-allocation-figures-September-2026-V1.pdf",
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0006/521394/FINAL-Runnymede-Primary-allocation-figures-September-2026-V1.pdf",
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0007/521395/FINAL-Spelthorne-Primary-allocation-figures-September-2026-V1.pdf",
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0008/521396/FINAL-Surrey-Heath-Primary-allocation-figures-September-2026-V1.pdf",
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0009/521397/FINAL-Tandridge-Primary-allocation-figures-September-2026-V1.pdf",
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0010/521398/FINAL-Waverley-Primary-allocation-figures-September-2026-V1.pdf",
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0011/521399/FINAL-Woking-Primary-allocation-figures-September-2026-V1.pdf",
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0009/521388/FINAL-Junior-allocation-figures-September-2026-V1.pdf",
    "https://www.surreycc.gov.uk/__data/assets/pdf_file/0003/516684/FINAL-Secondary-allocation-figures-September-2026-V2.pdf",
]


def _parse_surrey_pdf(full_text: str) -> list[dict]:
    records = []
    for name, section in _SURREY_SCHOOL_RE.findall(full_text):
        # "Distance to Nodal Point" is a distance to a fixed
        # reference point used as a tie-breaker on some split
        # catchments, not a home-to-school distance - excluded so it
        # doesn't get mistaken for the admission radius.
        distances = [
            float(m.group(1))
            for m in _SURREY_DISTANCE_RE.finditer(section)
            if "nodal" not in m.group(0).lower()
        ]
        if distances:
            records.append({"school_name": name.strip(), "last_distance_miles": max(distances) / 1.60934})
    return records


def fetch_surrey() -> list[dict]:
    """Surrey County Council's "Allocation of places" PDFs - one per
    district (Elmbridge, Epsom and Ewell, Guildford, Mole Valley,
    Reigate and Banstead, Runnymede, Spelthorne, Surrey Heath,
    Tandridge, Waverley, Woking) plus separate Junior and Secondary
    documents, republished each year at new file IDs under the same
    /__data/assets/pdf_file/ path pattern (find the current ones via
    surreycc.gov.uk's own "arrangements-and-outcomes/previous-years"
    page). Format is prose, not a table: each school is a heading line
    followed by "DfE No: .../....", then free text ending in
    "Distance = X.XXXkm" (already scoped to the last-filled criterion
    per the document's own key) - already in km, converted to miles.
    """
    records = []
    for url in _SURREY_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        full_text = ""
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"
        records.extend(_parse_surrey_pdf(full_text))
    return records


_HERTS_NAME_RE = re.compile(r"^(.+?)\s+Total Applications:\s*\d+", re.MULTILINE)
_HERTS_DISTANCE_RE = re.compile(r"distance of most distant child admitted\s*([\d,]+\.?\d*)\s*m\b")

_HERTS_URLS = [
    "https://www.hertfordshire.gov.uk/doc/sch/adm/stats/primary-allocation-summary-reports-26-27.pdf",
    "https://www.hertfordshire.gov.uk/doc/sch/adm/stats/junior-and-middle-school-allocation-summary-reports-26-27.pdf",
    "https://www.hertfordshire.gov.uk/media-library/documents/schools-and-education/admissions/"
    "previous-years-stats/25-26/allocation-summary-reports.pdf",
]


def fetch_hertfordshire() -> list[dict]:
    """Hertfordshire County Council's "School Allocation Summary
    Report" PDFs - one for Primary, one for Junior/Middle, one for
    Secondary/Upper (the secondary one is still on last year's 25-26
    URL since 26-27's hadn't been published yet at time of writing -
    check hertfordshire.gov.uk's "previous years' statistics" page for
    a newer one). One page per school (a large multi-hundred-page
    document, unlike every other authority so far), always titled
    "<School Name> Total Applications: N", with the actual metric
    being "Home to school distance of most distant child admitted X m"
    - can appear more than once per school (e.g. separate "Nearest
    School"/"Not Nearest School" or "In Priority Area" rows), so this
    takes the largest, consistent with the same choice made for other
    authorities with multiple distance-shaped rows per school (e.g.
    Brent, Surrey). Already in metres.
    """
    records = []
    for url in _HERTS_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=60, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                name_match = _HERTS_NAME_RE.search(text)
                if not name_match:
                    continue
                distances = [float(d.replace(",", "")) for d in _HERTS_DISTANCE_RE.findall(text)]
                if distances:
                    records.append({
                        "school_name": name_match.group(1).strip(),
                        "last_distance_miles": max(distances) / _METRES_PER_MILE,
                    })
    return records


_OXFORDSHIRE_DISTANCE_RE = re.compile(r"([\d.]+)\s*miles?", re.IGNORECASE)


def fetch_oxfordshire() -> list[dict]:
    """Oxfordshire County Council publishes its "last offer" data as a
    plain CSV (not PDF!) - "Last place offered at schools that had
    refusals", republished each allocation round at a new file name
    (find the current one via oxfordshire.gov.uk's own
    "allocation-reports-and-vacancies/primary-allocation" page - the
    "P<year>-NOD<n>-LastOffer.csv" naming pattern is likely to recur).
    Establishment name has a trailing "(DfE No)" suffix stripped
    before matching; distance is already in miles. Cleanest source
    format found across every authority in this registry so far.
    """
    url = "https://www.oxfordshire.gov.uk/sites/default/files/file/place-allocations/P26-NOD1-LastOffer.csv"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    text = resp.content.decode("utf-8-sig", errors="replace")

    records = []
    for line in text.splitlines()[2:]:
        if not line.strip():
            continue
        cells = next(csv.reader([line]))
        if len(cells) < 6 or not cells[0]:
            continue
        name = re.sub(r"\s*\(\d+/\d+\)\s*$", "", cells[0]).strip()
        match = _OXFORDSHIRE_DISTANCE_RE.search(cells[5])
        if match:
            records.append({"school_name": name, "last_distance_miles": float(match.group(1))})
    return records


_BUCKS_MILES_RE = re.compile(r"([\d.]+)\s*miles?", re.IGNORECASE)

# (label, URL, "table" = row-per-school with its own distance column,
#  "prose" = row-per-school with a free-text description ending in a
#  distance figure - the Reception/Junior workbooks use the former,
#  the Secondary one the latter)
_BUCKS_FILES = [
    ("table", "https://www.buckinghamshire.gov.uk/documents/41709/"
              "How_Places_Were_Allocated_Into_Reception_September_2026_as_at_20_May_2026.xlsx"),
    ("table", "https://www.buckinghamshire.gov.uk/documents/41714/"
              "How_Places_Were_Allocated_Into_Junior_School_September_2026_as_at_20_May_2026.xlsx"),
    ("prose", "https://www.buckinghamshire.gov.uk/documents/41705/"
              "Allocation_Profile_2026_-_Secondary_Second_Round_-_20_May.xlsx"),
]


def fetch_buckinghamshire() -> list[dict]:
    """Buckinghamshire Council publishes its "How places were
    allocated" results as Excel workbooks (not PDFs), republished each
    year at a new document ID (find the current ones via
    buckinghamshire.gov.uk's "school-place-allocation-statistics"
    page). Reception and Junior workbooks are a clean table with a
    "Distance of last allocated child" column already in miles.
    Secondary is a coarser "school name, free-text description of how
    places were allocated" layout (grammar/upper/all-ability schools
    grouped under section-header rows with no distance, which are
    naturally skipped since they don't match the distance regex) -
    text ends with "...to X.XXX miles." when the school was
    oversubscribed enough for distance to matter.
    """
    records = []
    for kind, url in _BUCKS_FILES:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        wb = openpyxl.load_workbook(io.BytesIO(resp.content), data_only=True)
        ws = wb[wb.sheetnames[0]]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not row[0] or not isinstance(row[0], str):
                continue
            name = row[0].strip()
            if kind == "table":
                cell = row[2] if len(row) > 2 else None
            else:
                cell = row[1] if len(row) > 1 else None
            if not cell or not isinstance(cell, str):
                continue
            match = _BUCKS_MILES_RE.search(cell)
            if match:
                records.append({"school_name": name, "last_distance_miles": float(match.group(1))})
    return records


_CAMBS_DISTANCE_RE = re.compile(r"([\d.]+)")

# Only the "round 1" Reception and Junior PDFs - the cleanest of
# Cambridgeshire's several allocation-round documents (one table,
# "School Name, PAN, Offered, Criterion, Distance (miles)"). Round 2,
# Middle, and Year 7/9 documents use inconsistent/wider layouts not
# worth the extra parsing complexity for the schools they'd add.
_CAMBS_URLS = [
    "https://cambridgeshire.gov.uk/asset-library/Reception-allocation-sheet-2026-round-1.pdf",
    "https://cambridgeshire.gov.uk/asset-library/Junior-data-sheet-2026-round-1.pdf",
]


def fetch_cambridgeshire() -> list[dict]:
    """Cambridgeshire County Council's Reception and Junior "round 1"
    allocation PDFs - republished each year at a new URL under
    /asset-library/ (find the current ones via cambridgeshire.gov.uk's
    "school-allocation-information" page). Clean table: School Name,
    PAN, Offered, Criterion Allocated to, Distance (miles) - already
    in miles, last column.
    """
    records = []
    for url in _CAMBS_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                for row in table:
                    if not row or len(row) < 5 or not row[0] or not row[4]:
                        continue
                    match = _CAMBS_DISTANCE_RE.search(row[4])
                    if match:
                        records.append({"school_name": row[0].strip(), "last_distance_miles": float(match.group(1))})
    return records


def _wokingham_distance_column(header: list) -> int | None:
    """Wokingham's PDF renders rotated column headers as
    line-reversed text (pdfplumber artifact of a 90-degree-rotated
    header cell) - e.g. "Distance of child allocated last" comes out
    as "tsal detacolla dlihc fo ecnatsiD" split across lines. Reversing
    each line back locates the column - except in the widest
    (17-column) table, where merged header cells throw off the
    header/data column alignment by one, so that layout's distance
    index (9, confirmed by inspecting its data rows directly) is
    hardcoded instead."""
    if len(header or []) == 17:
        return 9
    for i, cell in enumerate(header or []):
        if not cell:
            continue
        for line in cell.split("\n"):
            if "distance" in line[::-1].lower():
                return i
    return None


def fetch_wokingham() -> list[dict]:
    """Wokingham Borough Council's "Allocation breakdown for admission
    to Primary School" PDF - republished each spring at a new URL
    under /sites/wokingham/files/YYYY-MM/ (find the current one via
    wokingham.gov.uk's "key-dates-and-statistics" page). No secondary
    equivalent is published. Two different table layouts appear across
    pages (a wide one for schools using the council's own coordinated
    criteria, a narrower one for schools that set their own admission
    criteria) - the distance column index differs between them, so
    _wokingham_distance_column() locates it per-table rather than
    assuming a fixed position. Distances over 20 miles are dropped as
    an extraction glitch (this table has cases where a missing cell
    shifts an unrelated large number, like an application count, into
    the distance column) - Wokingham is a small borough where a
    genuine cross-borough admission would never plausibly reach that
    far, unlike Surrey's 47km faith-school outlier which was a
    legitimate published figure in a clean, unambiguous column.
    """
    url = "https://www.wokingham.gov.uk/sites/wokingham/files/2026-04/Primary%20school%20allocation%20statistics%202026.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table or len(table) < 2:
                continue
            dist_idx = _wokingham_distance_column(table[0])
            if dist_idx is None:
                continue
            for row in table[1:]:
                if not row or not row[0] or dist_idx >= len(row) or not row[dist_idx]:
                    continue
                name = re.sub(r"\*+$", "", row[0]).strip()
                try:
                    distance = float(row[dist_idx].strip())
                except ValueError:
                    continue
                if 0 < distance <= 20:
                    records.append({"school_name": name, "last_distance_miles": distance})
    return records


_COVENTRY_ROW_RE = re.compile(r'<a[^>]*>([^<]+?)</a>.*?</tr>', re.DOTALL)
_COVENTRY_DISTANCE_RE = re.compile(r"distance of\s*([\d.]+)\s*(of a mile|miles?)", re.IGNORECASE)

_COVENTRY_URLS = [
    "https://www.coventry.gov.uk/school-admissions/primary-school-admissions/4",
    "https://coventry.gov.uk/school-admissions/secondary-school-admissions/7",
]


def fetch_coventry() -> list[dict]:
    """Coventry City Council publishes its allocation results as plain
    HTML tables (not PDFs) - "How primary school places were
    allocated" and "Allocation of Coventry secondary school places",
    republished each year at the same URL (just the year in the page
    text changes, so these URLs are likely stable). One row per
    school: name is a link to its admissions policy, last column is
    prose like "...some offered up to a distance of 1.83 miles" or
    "...0.408 of a mile" - both phrasings handled. Schools that
    weren't oversubscribed have no distance mentioned and are
    correctly skipped.
    """
    records = []
    for url in _COVENTRY_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        for name, row in [(m.group(1), m.group(0)) for m in _COVENTRY_ROW_RE.finditer(resp.text)]:
            match = _COVENTRY_DISTANCE_RE.search(row.replace("&nbsp;", " "))
            if match:
                records.append({"school_name": name.strip(), "last_distance_miles": float(match.group(1))})
    return records


_MK_DISTANCE_RE = re.compile(r"distance\s*(?:to the [\w\s]+ campus)?\s*of\s*([\d.]+)\s*miles?", re.IGNORECASE)

_MK_URLS = [
    "https://www.milton-keynes.gov.uk/sites/default/files/2025-04/Allocation%20Profile%20Starting%20School%2016%20April%202025.pdf",
    "https://www.milton-keynes.gov.uk/sites/default/files/2025-02/Allocation%20Profile%203%20March%202025.pdf",
]


def fetch_milton_keynes() -> list[dict]:
    """Milton Keynes City Council's "Allocation Profile" PDFs - one
    for Starting/Primary School, one for Year 7/Secondary, republished
    each year at a new URL under /sites/default/files/YYYY-MM/ (find
    the current ones via milton-keynes.gov.uk's admissions pages).
    One clean row per school: name, then prose ending "...to a
    distance of X.XXX miles" (occasionally "...to the <campus name>
    campus" for split-site schools, handled by the regex's optional
    middle clause), vacancy flag.
    """
    records = []
    for url in _MK_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                for row in table[1:]:
                    if not row or not row[0] or not row[1]:
                        continue
                    match = _MK_DISTANCE_RE.search(row[1].replace("\n", " "))
                    if match:
                        name = row[0].replace("\n", " ").strip()
                        records.append({"school_name": name, "last_distance_miles": float(match.group(1))})
    return records


_RBWM_DISTANCE_RE = re.compile(r"Furthest distance met:\s*([\d.]+)\s*miles")

_RBWM_URLS = [
    "https://5f2fe3253cd1dfa0d089-bf8b2cdb6a1dc2999fecbc372702016c.ssl.cf3.rackcdn.com/"
    "uploads/ckeditor/attachments/17730/Allocation_information_for_RBWM_primary_schools_September_2025_V1.pdf",
    "https://5f2fe3253cd1dfa0d089-bf8b2cdb6a1dc2999fecbc372702016c.ssl.cf3.rackcdn.com/"
    "uploads/ckeditor/attachments/17544/FINAL_NOD_afc_middle_secondary_and_upper_school_allocation_information_2025_v2.pdf",
]


def fetch_windsor_and_maidenhead() -> list[dict]:
    """Royal Borough of Windsor and Maidenhead's "Allocation
    information" PDFs (Primary + Middle/Secondary/Upper) - republished
    each year at a new attachment ID on a CDN host (find the current
    ones via rbwm.gov.uk's school admissions pages). Prose layout, one
    block per school: "<School Name>", "Type: ... Number of places
    offered: N", "DfE Ref: .../... Number of divert offers: N",
    "Furthest distance met: X.XXX miles ...". The school name is
    always exactly two lines above the "DfE Ref:" line, which is a
    more reliable anchor than scanning forward from the name (some
    names get split across a stray hyperlink line in extraction).
    """
    records = []
    for url in _RBWM_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        full_text = ""
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"
        lines = full_text.split("\n")
        for i, line in enumerate(lines):
            if not line.strip().startswith("DfE Ref:") or i < 2 or not lines[i - 1].strip().startswith("Type:"):
                continue
            name = lines[i - 2].strip()
            for j in range(i, min(i + 3, len(lines))):
                match = _RBWM_DISTANCE_RE.search(lines[j])
                if match:
                    records.append({"school_name": name, "last_distance_miles": float(match.group(1))})
                    break
    return records


_STOCKPORT_URLS = [
    "https://assets.ctfassets.net/ii3xdrqc6nfw/5wDH7mjJwVyUmBxuKNKGyW/68b5a6974d1c750554e2352e180ed271/"
    "Allocation_of_places_at_primary_schools_in_Stockport_for_September_2026.pdf",
    "https://assets.ctfassets.net/ii3xdrqc6nfw/4EAb1hwybiJ3vKTqKigTMQ/87e71c0afd97857f5f6125fd0f95503b/"
    "Allocation_of_places_at_secondary_schools_in_Stockport_for_September_2026.pdf",
]
_STOCKPORT_DECIMAL_RE = re.compile(r"^\d+\.\d+$")
_STOCKPORT_INT_RE = re.compile(r"^\d+$")


def fetch_stockport() -> list[dict]:
    """Stockport Council's "Allocation of places" PDFs (Primary +
    Secondary) - republished each year at a new content-hash URL under
    assets.ctfassets.net (find the current ones via
    stockport.gov.uk/documents/school-allocation). Extremely wide,
    sparse tables (40-58 columns, mostly empty, with rotated/wrapped
    header text split across several rows) make a fixed column index
    unreliable - instead, per row, the school name is the first
    non-empty cell that isn't a plain integer, and the distance is the
    one cell matching a decimal number (every other numeric column in
    these tables is a plain integer count, so this is unambiguous).
    Header/label rows naturally have no decimal cell and are skipped.
    """
    records = []
    for url in _STOCKPORT_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                for row in table:
                    if not row:
                        continue
                    cells = [c.strip() if c else None for c in row]
                    non_empty = [c for c in cells if c]
                    name = None
                    distance = None
                    for c in non_empty:
                        if _STOCKPORT_DECIMAL_RE.match(c):
                            distance = float(c)
                        elif name is None and not _STOCKPORT_INT_RE.match(c) and c.lower() != "miles":
                            name = c
                    if name and distance:
                        records.append({"school_name": name, "last_distance_miles": distance})
    return records


_PORTSMOUTH_ROW_RE = re.compile(r"<tr>\s*<td[^>]*>([^<]+)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>", re.DOTALL)
_PORTSMOUTH_DISTANCE_RE = re.compile(r"lived\s*([\d.]+)\s*miles away", re.IGNORECASE)

_PORTSMOUTH_URLS = [
    "https://www.portsmouth.gov.uk/services/schools-and-learning/schools/admissions/"
    "waiting-list-and-appeals-for-a-school-place/starting-school-important-information/",
    "https://www.portsmouth.gov.uk/services/schools-learning-and-childcare/schools/admissions/"
    "waiting-list-and-appeals-for-a-school-place/important-information-for-secondary-transfer-applicants/",
]


def fetch_portsmouth() -> list[dict]:
    """Portsmouth City Council publishes an "Oversubscribed schools"
    HTML table (not a PDF) on its own admissions pages - one for
    starting/primary school, one for secondary transfer, both
    seemingly stable URLs updated in place each year (though note the
    two use slightly different URL path segments -
    "schools-and-learning" vs "schools-learning-and-childcare" -
    re-check both if either 404s). Each row is "<school name>, prose
    ending '...lived X.XXX miles away from the school.'" - schools
    filled without reaching the distance criterion aren't listed at
    all, so every row here already had a distance decide the last
    place.
    """
    records = []
    for url in _PORTSMOUTH_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        for name, cell in _PORTSMOUTH_ROW_RE.findall(resp.text):
            match = _PORTSMOUTH_DISTANCE_RE.search(cell)
            if match:
                records.append({"school_name": name.strip(), "last_distance_miles": float(match.group(1))})
    return records


_PETERBOROUGH_URLS = [
    "https://peterborough.gov.uk/asset-library/reception-allocations-sheet-2026-website.pdf",
    "https://peterborough.gov.uk/asset-library/junior-allocations-sheet-2026-website.pdf",
    "https://peterborough.gov.uk/asset-library/1st-round-allocation-stats-02.03.26-website.pdf",
]


def fetch_peterborough() -> list[dict]:
    """Peterborough City Council's Reception, Junior, and Year 7
    "1st round" allocation PDFs - republished each year at new URLs
    under /asset-library/ (find the current ones via
    peterborough.gov.uk's "school-allocation-information" page).
    Reception/Junior share one table layout (last column "Distance
    (miles)"); Year 7 uses a wider layout with the distance column at
    a different position - rather than hardcode two different column
    indices, this reuses the same decimal-cell-detection approach as
    Stockport (every other numeric column across all three files is a
    plain integer count or "N/A", so the one cell matching a decimal
    number is unambiguous). One row (John Clare Primary School) reads
    "206.404" - a genuine typo in the council's own PDF, not an
    extraction glitch (Peterborough is a small unitary; no primary
    admission is 200 miles), so implausible values (>30 miles) are
    dropped rather than propagated as a real "circle" onto the map.
    """
    records = []
    for url in _PETERBOROUGH_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                for row in table:
                    if not row or not row[0] or not isinstance(row[0], str):
                        continue
                    name = row[0].replace("\n", " ").strip()
                    distance = None
                    for cell in row[1:]:
                        if cell and _STOCKPORT_DECIMAL_RE.match(cell.strip()):
                            distance = float(cell.strip())
                            break
                    if distance is not None and distance <= 30:
                        records.append({"school_name": name, "last_distance_miles": distance})
    return records


_NORTH_SOMERSET_INDEX_URL = (
    "https://n-somerset.gov.uk/my-services/schools-learning/school-admissions/"
    "oversubscribed-schools/primary-allocations-2024-25"
)
_NORTH_SOMERSET_PDF_RE = re.compile(r'href="([^"]+\.pdf)"')
_NORTH_SOMERSET_NAME_RE = re.compile(r"Allocation sheet for\s+(.*?)\s*\d{4}/\d{2}", re.DOTALL)
_NORTH_SOMERSET_DISTANCE_RE = re.compile(
    r"distance between home and school for the last child offered a place was\s*([\d.]+)\s*miles", re.IGNORECASE
)


def fetch_north_somerset() -> list[dict]:
    """North Somerset Council publishes one individual "Allocation
    sheet" PDF per oversubscribed primary school (not one bulk
    document) - this crawls the year's index page for the current
    list of PDF links rather than hardcoding them individually, since
    which schools were oversubscribed (and thus which PDFs exist)
    changes every year (find the current index page via
    n-somerset.gov.uk's "oversubscribed-schools/previous-primary-
    allocations" page - the "primary-allocations-YYYY-YY" URL pattern
    is likely to recur). Only ~13 schools a year (a small unitary), but
    a clean, unambiguous prose format: "The distance between home and
    school for the last child offered a place was X.XXX miles, as
    measured in a direct line."
    """
    print(f"  Downloading index {_NORTH_SOMERSET_INDEX_URL}")
    resp = httpx.get(_NORTH_SOMERSET_INDEX_URL, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    pdf_urls = [u for u in _NORTH_SOMERSET_PDF_RE.findall(resp.text) if "allocation" in u.lower()]

    records = []
    for url in pdf_urls:
        print(f"  Downloading {url}")
        pdf_resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        pdf_resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(pdf_resp.content)) as pdf:
            full_text = (pdf.pages[0].extract_text() or "")
        name_match = _NORTH_SOMERSET_NAME_RE.search(full_text)
        distance_match = _NORTH_SOMERSET_DISTANCE_RE.search(full_text)
        if name_match and distance_match:
            name = name_match.group(1).replace("\n", " ").strip()
            records.append({"school_name": name, "last_distance_miles": float(distance_match.group(1))})
    return records


_SOMERSET_URLS = [
    "https://somersetcc.sharepoint.com/:b:/s/SCCPublic/"
    "IQAmnq1_wHcJRayL6PFb9cfLAYPlkUMlIjVAMyhRFmP_Fng?e=sFNh7V&download=1",
    "https://somersetcc.sharepoint.com/:b:/s/SCCPublic/"
    "IQAs4cxF933FT6CTjdVlHM51AZxGnuma-esY5uYFvCNptJA?e=Ls4sqa&download=1",
]
_SOMERSET_SCHOOL_RE = re.compile(r"Allocation Summary for\s+(.+?)\n")
_SOMERSET_DISTANCE_LINE_RE = re.compile(r"(?:^|\n)\d+\s+\S.*?\s(\d+)\s+([\d.]+)\s*(?=\n|$)")


def fetch_somerset() -> list[dict]:
    """Somerset County Council's "Allocation Summaries" PDFs (First
    Admissions/Primary + Secondary), hosted on SharePoint share links
    rather than the council's own domain - the share URL only returns
    a raw PDF with "&download=1" appended (otherwise it 200s with an
    HTML viewer page instead of the file), republished each year at a
    new share link (find the current ones via somerset.gov.uk's
    "school-place-allocation-summaries" page, inside the year's
    accordion section). One page per school: "Allocation Summary for
    <Name>", then a "Criterion | Number of places offered | Max
    distance (miles)" table where only the one decisive
    (oversubscribed) criterion row ends with two numbers ("<places>
    <distance>") - every other criterion row ends with just the
    places-offered count, so a line-level regex for a trailing
    "int float" pair unambiguously finds the real distance without
    needing pdfplumber's unreliable table extraction on this
    heavily-wrapped layout.
    """
    records = []
    for url in _SOMERSET_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=60, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        full_text = ""
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"

        sections = _SOMERSET_SCHOOL_RE.split(full_text)[1:]  # alternating name, body, name, body...
        for name, body in zip(sections[0::2], sections[1::2]):
            matches = _SOMERSET_DISTANCE_LINE_RE.findall(body.split("This information was correct")[0])
            if matches:
                records.append({"school_name": name.strip(), "last_distance_miles": float(matches[-1][1])})
    return records


_DORSET_INDEX_URL = "https://www.dorsetcouncil.gov.uk/w/school-allocations"
_DORSET_LINK_RE = re.compile(r'href="(/documents/d/guest/[^"]+)"')
_DORSET_NAME_RE = re.compile(r"On Time Applications\n(.+?)\n")
_DORSET_DISTANCE_RE = re.compile(r"([\d.]+)\s*Miles\.")


def fetch_dorset() -> list[dict]:
    """Dorset Council publishes one individual "information sheet" PDF
    per oversubscribed school (primary "on-time"/"tr4-ot" and
    secondary "tr7"/"tr9" transfer, both "on-time" and "late" rounds)
    rather than a bulk document - this crawls the index page for the
    current list of documents rather than hardcoding them, filtering
    out "late"-round duplicates of schools already covered by the
    "on-time" round document (find the current index page via
    dorsetcouncil.gov.uk's "school-allocations" page). Clean prose:
    "<School Name>", then "<Criterion> X.XXX Miles." A handful of
    documents (seen for at least one secondary school) are scanned
    images with no extractable text layer - these are silently
    skipped rather than guessed at via OCR.
    """
    print(f"  Downloading index {_DORSET_INDEX_URL}")
    resp = httpx.get(_DORSET_INDEX_URL, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    paths = [p for p in set(_DORSET_LINK_RE.findall(resp.text)) if "late" not in p.lower()]

    records = []
    for path in paths:
        url = f"https://www.dorsetcouncil.gov.uk{path}"
        print(f"  Downloading {url}")
        try:
            pdf_resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
            pdf_resp.raise_for_status()
        except httpx.HTTPError:
            continue
        with pdfplumber.open(io.BytesIO(pdf_resp.content)) as pdf:
            full_text = pdf.pages[0].extract_text() or ""
        name_match = _DORSET_NAME_RE.search(full_text)
        distance_match = _DORSET_DISTANCE_RE.search(full_text)
        if name_match and distance_match:
            records.append({
                "school_name": name_match.group(1).strip(),
                "last_distance_miles": float(distance_match.group(1)),
            })
    return records


_WORCESTERSHIRE_URLS = [
    "https://www.worcestershire.gov.uk/sites/default/files/2026-05/"
    "first_primary_schools_allocation_day_statistics_2026.pdf",
    "https://www.worcestershire.gov.uk/sites/default/files/2026-04/"
    "middle_schools_allocation_day_statistics_2026.pdf",
    "https://www.worcestershire.gov.uk/sites/default/files/2026-02/"
    "high_school_allocation_day_statistics_2026.pdf",
]


def fetch_worcestershire() -> list[dict]:
    """Worcestershire County Council's "Allocation day statistics"
    PDFs (First/Primary + Middle + High), republished each year at new
    URLs under /sites/default/files/YYYY-MM/ (find the current ones
    via worcestershire.gov.uk's "allocation-day-statistics-adjudicator
    -reports-and-resources" page). "District, Name of School, DfE
    Number, PAN, Oversubscribed, Refused, Criterion, Distance (miles)"
    table - column count/order shifts very slightly between the three
    files (a rotated-header artifact, same family of issue as
    Wokingham's), so this uses the same decimal-cell-detection
    approach as Stockport/Peterborough for the distance rather than a
    fixed index; school name is reliably column 1 (District is
    column 0, sometimes blank on continuation rows for the same
    district) in all three.
    """
    records = []
    for url in _WORCESTERSHIRE_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                for row in table:
                    if not row or len(row) < 2 or not row[1] or not isinstance(row[1], str):
                        continue
                    distance = None
                    for cell in row:
                        if cell and _STOCKPORT_DECIMAL_RE.match(cell.strip()):
                            distance = float(cell.strip())
                            break
                    if distance is not None:
                        records.append({"school_name": row[1].replace("\n", " ").strip(), "last_distance_miles": distance})
    return records


def fetch_cheshire_east() -> list[dict]:
    """Cheshire East Council's "Breakdown of oversubscription criteria
    allocated" PDF - republished each year at a new URL (find the
    current one via cheshireeast.gov.uk's "previous-allocations.aspx"
    page). Clean table: DfE Number, School, PAN, Total Allocated,
    Lowest Criteria Allocated, Furthest Distance - column header says
    "(miles)" but a subset of rows are actually recorded in metres
    (e.g. "1489", "309", "92.912") rather than miles - confirmed by
    checking what those figures become when divided by 1609.34: each
    one lands on a small, entirely plausible mile value for a
    small/lightly-oversubscribed local primary school, whereas taken
    literally as miles (92, 309, 1489 miles) they're physically
    impossible for a non-boarding UK primary school. Any value over 15
    (a generous upper bound - no legitimate Cheshire primary
    catchment approaches that) is therefore treated as metres and
    converted, rather than either trusted at face value or discarded.
    """
    url = "https://www.cheshireeast.gov.uk/pdf/schools/admissions/2026-criteria-allocated-analysis-primary-lowest-criteria-only.pdf"
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
                if not row or len(row) < 6 or not row[1] or not row[-1]:
                    continue
                try:
                    value = float(row[-1].strip())
                except ValueError:
                    continue
                distance = value / _METRES_PER_MILE if value > 15 else value
                records.append({"school_name": row[1].replace("\n", " ").strip(), "last_distance_miles": distance})
    return records


_SUFFOLK_URLS = [
    "https://www.suffolk.gov.uk/asset-library/school-admissions/Directory-of-Schools-in-Suffolk-Primary-2026-2027-1.pdf",
    "https://www.suffolk.gov.uk/asset-library/school-admissions/Directory-of-Schools-in-Suffolk-Secondary-2026-2027-1.pdf",
]
_SUFFOLK_HEADING_RE = re.compile(r"^([A-Za-z][^,\n]{2,80}),.*[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\s*$")
_SUFFOLK_DISTANCE_RE = re.compile(r"Criterion under which last child admitted:.*?([\d.]+)\s*miles", re.IGNORECASE)


def fetch_suffolk() -> list[dict]:
    """Suffolk County Council's "Directory of Schools" PDFs (Primary +
    Secondary) - a prose directory (school-by-school profile, not a
    table), republished each year at a new URL under /asset-library/
    school-admissions/ (find the current ones via suffolk.gov.uk's
    primary/secondary "apply for a school place" pages). Each school's
    entry is headed by its "<Name>, <Address>, <Postcode>" line
    (detected via a UK postcode regex, since there's no other marker),
    and the decisive line reads "Criterion under which last child
    admitted: <criterion> X.XXX miles" - this scans line-by-line,
    tracking the most recently seen heading so the distance line can
    be attached to the right school even though (unlike every other
    authority in this registry) the layout is continuous prose, not
    row-per-school.
    """
    records = []
    for url in _SUFFOLK_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        full_text = ""
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"

        current = None
        for line in full_text.split("\n"):
            heading_match = _SUFFOLK_HEADING_RE.match(line.strip())
            if heading_match:
                current = heading_match.group(1).strip()
                continue
            distance_match = _SUFFOLK_DISTANCE_RE.search(line)
            if distance_match and current:
                records.append({"school_name": current, "last_distance_miles": float(distance_match.group(1))})
                current = None
    return records


_EAST_SUSSEX_URLS = [
    "https://www.eastsussex.gov.uk/education-learning/schools/apply-for-a-school-place/"
    "apply-for-a-primary-or-junior-school/detailed-school-information-primary-and-junior?print=true",
    "https://www.eastsussex.gov.uk/education-learning/schools/apply-for-a-school-place/"
    "apply-for-a-secondary-school/detailed-school-information-secondary?print=true",
]
_EAST_SUSSEX_TIEBREAK_CAPTION = "Tie-breaker - furthest child allocated a place at the school"
_EAST_SUSSEX_ROW_RE = re.compile(
    r'<td class="govuk-table__cell clean-rich-text" colspan="1">\s*(.+?)\s*</td>\s*'
    r'<td class="govuk-table__cell clean-rich-text govuk-table__cell--numeric[^"]*"\s*colspan="1">'
    r"(.*?)</td>",
    re.DOTALL,
)
_EAST_SUSSEX_DISTANCE_RE = re.compile(r"(\d+)m in category")


def fetch_east_sussex() -> list[dict]:
    """East Sussex County Council publishes its "detailed school
    information" as HTML pages (Primary/Junior + Secondary), each
    split into several alphabetical-range sub-pages in the normal
    view - but the "?print=true" query parameter renders every
    sub-page's content concatenated onto one page, which is far easier
    to scrape than crawling each range separately (find the current
    base URLs via eastsussex.gov.uk's "apply-for-a-primary-or-junior-
    school"/"apply-for-a-secondary-school" sections if this stops
    working). Each alphabetical range has its own "Tie-breaker -
    furthest child allocated a place at the school" table with rows
    like "<School Name> ... XXXXm in category N" - already in metres.
    Rows for schools that weren't oversubscribed just say "All
    preferences allocated" with no metres figure and are naturally
    skipped.
    """
    records = []
    for url in _EAST_SUSSEX_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        for block in resp.text.split(_EAST_SUSSEX_TIEBREAK_CAPTION)[1:]:
            end = block.find("</tbody>")
            section = block[:end] if end > 0 else block[:8000]
            for name, cell in _EAST_SUSSEX_ROW_RE.findall(section):
                match = _EAST_SUSSEX_DISTANCE_RE.search(cell)
                if match:
                    clean_name = name.replace("&nbsp;", "").strip()
                    records.append({
                        "school_name": clean_name,
                        "last_distance_miles": float(match.group(1)) / _METRES_PER_MILE,
                    })
    return records


_WEST_SUSSEX_URLS = [
    "https://www.westsussex.gov.uk/media/e3fn402p/starting_school_stats_2026.pdf",
    "https://www.westsussex.gov.uk/media/p1mlihto/secondary_school_allocation_day_statistics_2026.pdf",
]


def fetch_west_sussex() -> list[dict]:
    """West Sussex County Council's "allocation day statistics" PDFs
    (Starting School/Primary + Secondary) - republished each year at
    new URLs (find the current ones via westsussex.gov.uk's "starting-
    school-places"/"secondary-school-places" pages). Clean table:
    School Name, Places, Offers, Last criteria, Distance (metres) -
    schools that weren't oversubscribed have a long "All applicants...
    were offered a place" sentence instead of a number in the last
    column, which fails the float parse and is correctly skipped.
    """
    records = []
    for url in _WEST_SUSSEX_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                for row in table[1:]:
                    if not row or not row[0] or not row[-1]:
                        continue
                    try:
                        distance = float(row[-1].strip())
                    except ValueError:
                        continue
                    name = row[0].replace("\n", " ").strip()
                    records.append({"school_name": name, "last_distance_miles": distance / _METRES_PER_MILE})
    return records


_DURHAM_URLS = [
    "https://durham.gov.uk/article/27947/Primary-school-admissions-individual-school-and-academy-intake-information",
    "https://durham.gov.uk/article/27950/Secondary-school-admissions-individual-school-and-academy-intake-information",
]
_DURHAM_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_DURHAM_CELL_RE = re.compile(r"<td>(.*?)</td>", re.DOTALL)
_DURHAM_DISTANCE_RE = re.compile(r"([\d.]+)\s*[Mm]iles")


def fetch_durham() -> list[dict]:
    """Durham County Council publishes "individual school and academy
    intake information" as plain HTML tables (Primary + Secondary),
    seemingly stable URLs updated in place each year. Each row's last
    cell has prose that differs in exact wording between the two
    documents ("<X.XXX> miles in the distance criterion..." for
    primary, "Last person offered a place lived <X.XXX> miles away
    ..." for secondary, with inconsistent capitalisation of "Miles" in
    a few rows) - rather than handle both phrasings, this just takes
    the first "<number> miles" anywhere in that cell, school name is
    always the second cell. Schools with their own admission
    arrangements ("contact the school directly") have no such number
    and are correctly skipped.
    """
    records = []
    for url in _DURHAM_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        for row in _DURHAM_ROW_RE.findall(resp.text):
            cells = _DURHAM_CELL_RE.findall(row)
            if len(cells) < 2:
                continue
            name = re.sub(r"<[^>]+>", " ", cells[1]).replace("&nbsp;", " ").strip()
            name = re.sub(r"\s+", " ", name)
            if not name:
                continue
            match = _DURHAM_DISTANCE_RE.search(cells[-1])
            if match:
                records.append({"school_name": name, "last_distance_miles": float(match.group(1))})
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
    ("Birmingham", "2023/24", fetch_birmingham),
    ("Newcastle upon Tyne", "varies", fetch_newcastle),
    ("Surrey", "2026/27", fetch_surrey),
    ("Hertfordshire", "varies", fetch_hertfordshire),
    ("Oxfordshire", "2026/27", fetch_oxfordshire),
    ("Buckinghamshire", "2026/27", fetch_buckinghamshire),
    ("Cambridgeshire", "2026/27", fetch_cambridgeshire),
    ("Wokingham", "2025/26", fetch_wokingham),
    ("Coventry", "varies", fetch_coventry),
    ("Milton Keynes", "2025/26", fetch_milton_keynes),
    ("Windsor and Maidenhead", "2025/26", fetch_windsor_and_maidenhead),
    ("Stockport", "2026/27", fetch_stockport),
    ("Portsmouth", "varies", fetch_portsmouth),
    ("Peterborough", "2026/27", fetch_peterborough),
    ("North Somerset", "2024/25", fetch_north_somerset),
    ("Somerset", "2025/26", fetch_somerset),
    ("Dorset", "2025/26", fetch_dorset),
    ("Worcestershire", "2026/27", fetch_worcestershire),
    ("Cheshire East", "2025/26", fetch_cheshire_east),
    ("Suffolk", "2026/27", fetch_suffolk),
    ("East Sussex", "varies", fetch_east_sussex),
    ("West Sussex", "2025/26", fetch_west_sussex),
    ("County Durham", "varies", fetch_durham),
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
