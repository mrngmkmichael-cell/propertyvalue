"""One-time/periodic offline import of MODELLED school admission
radius estimates into the `school_admission_radii` table.

NOT run by the deployed app - run manually from a dev machine. Needs
`pdfplumber`, `openpyxl`, and `python-docx` installed locally (not
production dependencies - only used here, for extracting tables from
local authorities' own PDF/Excel/Word "last distance offered"
publications, same situation as pyproj in import_schools.py).

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

import docx
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
    r"\bJ I & N\b": "Junior Infant and Nursery",
    r"\bJ & I\b": "Junior and Infant",
    r"\bI & N\b": "Infant and Nursery",
    r"\bJun\b": "Junior",
    r"\bInf\b": "Infant",
    r"\bJnr\b": "Junior",
    r"\bJMI\b": "Junior Mixed Infant",
    r"\bC of E\b": "Church of England",
    r"\bCEVCP\b": "Church of England Voluntary Controlled Primary",
    r"\bCE\b": "Church of England",
    r"\bVC\b": "Voluntary Controlled",
    r"\bVA\b": "Voluntary Aided",
    r"\bRC\b": "Roman Catholic",
    r"\bR\.C\.(?=\s|$)": "Roman Catholic",
    r"\bRd\b": "Road",
    r"\bCP\b": "Community Primary",
    r"\bC\.P\.(?=\s|$)": "Community Primary",
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


_DERBY_URLS = [
    "https://www.derby.gov.uk/media/derbycitycouncil/content/documents/education/schooladmissions/"
    "primary-admissions-handbook-2026-27.pdf",
    "https://www.derby.gov.uk/media/derbycitycouncil/content/documents/education/schooladmissions/"
    "secondary-school-admissions-handbook2026-27.pdf",
]
_DERBY_POSTCODE_RE = re.compile(r"\bDE\d[A-Z\d]?\s?\d[A-Z]{2}\b")
_DERBY_DISTANCE_RE = re.compile(r"Furthest distance offered:\s*([\d.]+)\s*miles")


def fetch_derby() -> list[dict]:
    """Derby City Council's "Admissions Handbook" PDFs (Primary +
    Secondary) - a prose school-by-school prospectus, republished each
    year at a new URL under /media/derbycitycouncil/.../schooladmissions/
    (find the current ones via derby.gov.uk's admissions pages). Each
    school's profile ends with "Admissions Limit: N Furthest distance
    offered: X.XXX miles". The school name heading is detected as the
    line immediately before one containing "Telephone:" and a Derby
    postcode (DEx x-xx) - a few schools have a second subtitle line
    between the name and address (e.g. "Now incorporating <Nursery>")
    which this can't distinguish from the real name, so a handful of
    records get a wrong/partial name and are correctly dropped by the
    fuzzy-match cutoff rather than mismatched.
    """
    records = []
    for url in _DERBY_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=60, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        full_text = ""
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"

        lines = full_text.split("\n")
        current = None
        for i, line in enumerate(lines):
            if i + 1 < len(lines) and "Telephone:" in lines[i + 1] and _DERBY_POSTCODE_RE.search(lines[i + 1]):
                current = line.strip()
                continue
            match = _DERBY_DISTANCE_RE.search(line)
            if match and current:
                records.append({"school_name": current, "last_distance_miles": float(match.group(1))})
                current = None
    return records


_LEICESTER_URLS = [
    "https://www.leicester.gov.uk/sites/default/files/2026-02/"
    "breakdown-of-allocations-for-leicester-primary-and-infant-schools-as-of-16-april-2025.pdf",
    "https://schools.leicester.gov.uk/media/8524/"
    "breakdown-of-allocations-for-leicester-city-secondary-schools-as-of-1-march-2023.pdf",
]


def fetch_leicester() -> list[dict]:
    """Leicester City Council's "Breakdown of allocations" PDFs
    (Primary and Infant + Secondary) - republished at a new URL each
    round (find the current ones via leicester.gov.uk's admissions
    pages; the secondary one found is from 2023, the most recent
    working link located - still real published data, just an older
    round than the 2025 primary one). Multiple summary tables appear
    before the real one; only the one whose header contains "Furthest
    Distance" has the data, at whatever column index that table
    happens to place it (found dynamically per table rather than
    hardcoded, since the two files have slightly different column
    sets before it).
    """
    records = []
    for url in _LEICESTER_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                header = [h.replace("\n", " ") if h else "" for h in table[0]]
                dist_idx = next((i for i, h in enumerate(header) if "Furthest" in h and "Distance" in h), None)
                if dist_idx is None:
                    continue
                for row in table[1:]:
                    if not row or not row[0] or dist_idx >= len(row) or not row[dist_idx]:
                        continue
                    try:
                        distance = float(row[dist_idx].strip())
                    except ValueError:
                        continue
                    records.append({"school_name": row[0].replace("\n", " ").strip(), "last_distance_miles": distance})
    return records


_SANDWELL_URLS = [
    "https://www.sandwell.gov.uk/downloads/file/388/primary-statistics",
    "https://www.sandwell.gov.uk/downloads/file/389/secondary-statistics",
]


def fetch_sandwell() -> list[dict]:
    """Sandwell Council publishes its admission statistics as a Word
    (.docx) document (not PDF or HTML) - one for primary, one for
    secondary, both at a stable /downloads/file/<id>/ URL (find the
    current ids via sandwell.gov.uk if these ever change). Contains
    several tables; the one we want has the header row "School Name,
    Distance <year1>, Distance <year2>, Distance <year3>" (multiple
    years of history, "N/A" for years the school wasn't oversubscribed
    on distance) - this takes the most recent non-"N/A" year, working
    backwards from the last column, same "most recent real value"
    approach used for Bristol/Solihull's multi-year PDFs.
    """
    records = []
    for url in _SANDWELL_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        doc = docx.Document(io.BytesIO(resp.content))
        for table in doc.tables:
            if not table.rows:
                continue
            header = [c.text.strip() for c in table.rows[0].cells]
            if not header or header[0] != "School Name" or not any("Distance" in h for h in header[1:]):
                continue
            for row in table.rows[1:]:
                cells = [c.text.strip() for c in row.cells]
                if not cells or not cells[0]:
                    continue
                for value in reversed(cells[1:]):
                    try:
                        distance = float(value)
                    except ValueError:
                        continue
                    records.append({"school_name": cells[0], "last_distance_miles": distance})
                    break
    return records


_DUDLEY_NAME_CLEAN_RE = re.compile(r"\s*PAN\s*\d+/\d+\s*[–-]?\s*\d*")


def fetch_dudley() -> list[dict]:
    """Dudley Council's "Primary School Allocations Breakdown" PDF -
    a 3-year time series (2023/2024/2025, most recent first per
    school), republished periodically at a new URL (find the current
    one via dudley.gov.uk's "primary-reception-intake" page). Only the
    first row of each school's 3-row block has the name (a merged
    cell also containing "PAN <year> - <n>", stripped off here); only
    that first row is taken, so this is always the most recent (2025)
    year without needing explicit year comparison. "Furthest Distance
    Admitted" has no unit in the header but is unambiguously in
    metres, not miles (values run into the thousands - a value like
    "8299" is nonsensical as miles for a Dudley primary school but a
    perfectly normal 5.16-mile catchment as metres).
    """
    url = ("https://www.dudley.gov.uk/media/clanjdgu/"
           "dudley-primary-school-allocations-breakdown-from-september-2023-to-september-2025-intakes.pdf")
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table[1:]:
                if not row or not row[0] or len(row) < 12:
                    continue
                name = _DUDLEY_NAME_CLEAN_RE.sub("", row[0].replace("\n", " ")).strip()
                cell = row[11]
                if not cell or cell in ("-", "N/A"):
                    continue
                try:
                    metres = float(cell.replace(",", ""))
                except ValueError:
                    continue
                records.append({"school_name": name, "last_distance_miles": metres / _METRES_PER_MILE})
    return records


def _fetch_afc_borough(url: str) -> list[dict]:
    """Shared parser for the "STARTING PRIMARY SCHOOL... ALLOCATION OF
    PLACES" PDFs used by both Richmond and Kingston upon Thames (both
    administered by the same "Achieving for Children" admissions
    service, hosted on the same rackcdn CDN as RBWM - same underlying
    platform, near-identical layout). Clean table: School, Places,
    EHCP, LAC, Social/medical, Sibling, Child of staff, Distance,
    "Distance of last child offered under criterion 5 in metres" -
    rows reading "All preferences met" or "Overseas" have no number
    and are correctly skipped.
    """
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
                try:
                    metres = float(row[-1].strip())
                except ValueError:
                    continue
                records.append({"school_name": row[0].replace("\n", " ").strip(), "last_distance_miles": metres / _METRES_PER_MILE})
    return records


def fetch_richmond_upon_thames() -> list[dict]:
    """London Borough of Richmond upon Thames - see
    _fetch_afc_borough() for the shared AfC-platform format. Find the
    current URL via Achieving for Children's site (kr.afcinfo.org.uk)
    or by searching "LBR Notes to parents following Primary <year>
    allocation" - republished each year at a new rackcdn attachment
    ID.
    """
    url = ("https://5f2fe3253cd1dfa0d089-bf8b2cdb6a1dc2999fecbc372702016c.ssl.cf3.rackcdn.com/"
           "uploads/ckeditor/attachments/17717/LBR_Notes_to_parents_following_Primary_2025_allocation.pdf")
    print(f"  Downloading {url}")
    return _fetch_afc_borough(url)


def fetch_kingston_upon_thames() -> list[dict]:
    """Royal Borough of Kingston upon Thames - see
    _fetch_afc_borough() for the shared AfC-platform format (same as
    Richmond). Find the current URL by searching "RBK Notes to parents
    following Primary <year> allocation" - republished each year at a
    new rackcdn attachment ID.
    """
    url = ("https://5f2fe3253cd1dfa0d089-bf8b2cdb6a1dc2999fecbc372702016c.ssl.cf3.rackcdn.com/"
           "uploads/ckeditor/attachments/17727/RBK_Notes_to_parents_following_Primary_2025_allocation.docx.pdf")
    print(f"  Downloading {url}")
    return _fetch_afc_borough(url)


def fetch_southwark() -> list[dict]:
    """London Borough of Southwark's "Allocation of community school
    places by criteria" PDF - republished each year at a new URL
    under /sites/default/files/YYYY-MM/ (find the current one via
    southwark.gov.uk's primary-admissions "admissions-criteria" page -
    note the page itself is served from services.southwark.gov.uk but
    the PDF asset only resolves under www.southwark.gov.uk). Clean
    table, "Furthest distance (metres) offered a school place" at a
    fixed column index; rows reading "ALL APPLICANTS OFFERED A PLACE"
    have no number there and are correctly skipped. Southwark's own
    names are abbreviated to just the distinctive part (e.g. "Crampton"
    for "Crampton Primary School") - too short for the fuzzy matcher to
    find reliably on its own, so " Primary School" is appended before
    matching.
    """
    url = ("https://www.southwark.gov.uk/sites/default/files/2026-03/"
           "Allocation-of-community-school-places-by-criteria-Sept-2025.pdf")
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table[1:]:
                if not row or not row[0] or len(row) < 11 or not row[10]:
                    continue
                try:
                    metres = float(row[10].strip())
                except ValueError:
                    continue
                records.append({
                    "school_name": row[0].strip() + " Primary School",
                    "last_distance_miles": metres / _METRES_PER_MILE,
                })
    return records


_LAMBETH_URLS = [
    "https://www.lambeth.gov.uk/schools-and-education/school-admissions-and-appeals/primary-school-admissions/"
    "how-offers-were-made-lambeth-primary-schools-national-offer-day-16-april-2025/lambeth-voluntary-aided-schools",
    "https://www.lambeth.gov.uk/schools-and-education/school-admissions-and-appeals/primary-school-admissions/"
    "how-offers-were-made-lambeth-primary-schools-national-offer-day-16-april-2025/lambeth-academies",
    "https://www.lambeth.gov.uk/schools-and-education/school-admissions-and-appeals/primary-school-admissions/"
    "how-offers-were-made-lambeth-primary-schools-national-offer-day-16-april-2025/lambeth-foundation-schools",
]
_LAMBETH_HEADING_RE = re.compile(r"<h3[^>]*>(.*?)</h3>")
_LAMBETH_DISTANCE_RE = re.compile(r"Distance for last child[^:]{0,60}:\s*(\d+\.?\d*)")


def fetch_lambeth() -> list[dict]:
    """London Borough of Lambeth publishes its "how offers were made"
    results as plain HTML prose pages (not PDFs), split by admission-
    authority type (voluntary aided / academies / foundation schools -
    community schools apparently not published this way, or not
    oversubscribed), each republished at the same URL pattern each
    year with just the date in the path changing (find current ones
    via lambeth.gov.uk's primary-admissions pages). Uses "&nbsp;"
    entities instead of literal spaces throughout, which breaks any
    regex expecting whitespace unless replaced first. Some banded
    schools (Foundation/Open places) report two separate "Distance for
    last child" figures - this takes the larger, consistent with the
    same choice made for other multi-criterion authorities (Brent,
    Surrey). One occurrence (Iqra Primary School) is mislabelled
    "miles" in the source when the value is unambiguously in metres
    (consistent with every other entry's magnitude, and no school
    admits from 1,303 miles away) - all values are therefore always
    treated as metres regardless of the literal unit word.
    """
    records = []
    for url in _LAMBETH_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        text = resp.text.replace("&nbsp;", " ")
        current = None
        for chunk in re.split(r"(<h3[^>]*>.*?</h3>)", text):
            heading_match = _LAMBETH_HEADING_RE.match(chunk)
            if heading_match:
                current = re.sub("<[^>]+>", "", heading_match.group(1)).strip()
                continue
            if current:
                distances = [float(d) for d in _LAMBETH_DISTANCE_RE.findall(chunk)]
                if distances:
                    records.append({"school_name": current, "last_distance_miles": max(distances) / _METRES_PER_MILE})
                current = None
    return records


def fetch_waltham_forest() -> list[dict]:
    """London Borough of Waltham Forest's "Starting Primary School"
    brochure PDF - a large prospectus, republished each year at a new
    URL (find the current one via walthamforest.gov.uk's primary
    admissions page). Most of the document uses rotated column
    headers that extract as reversed/garbled text, but the specific
    "Cut off distances for the past 3 years" table (found by header
    text "School, <year1>, <year2>, <year3>") is a clean simple table,
    already in miles - takes the most recent non-blank year, working
    backwards from the last column, same "most recent real value"
    approach used for other multi-year sources (Bristol, Solihull,
    Sandwell).
    """
    url = "https://www.walthamforest.gov.uk/sites/default/files/2025-08/1012501%20-%20WF_PRIMARY%20BROCHURE%202026%20-%20WEB.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table or not table[0] or table[0][0] != "School":
                continue
            for row in table[1:]:
                if not row or not row[0]:
                    continue
                for value in reversed(row[1:]):
                    if value and value.strip():
                        try:
                            records.append({"school_name": row[0].strip(), "last_distance_miles": float(value.strip())})
                        except ValueError:
                            pass
                        break
    return records


def fetch_lewisham() -> list[dict]:
    """London Borough of Lewisham's "Applying to start primary
    school" brochure PDF - republished each year at the same stable
    URL (find via lewisham.gov.uk's primary admissions page if it
    ever moves). Most of the document's tables use rotated column
    headers that extract as vertically-garbled text, but the "Name of
    school" table (only 2 pages, community schools) is otherwise
    clean - the decisive metres figure is found via the same
    decimal-cell-detection approach as Stockport/Peterborough rather
    than a fixed column index. Lewisham's own names are abbreviated
    (e.g. "Adamsrill" for "Adamsrill Primary School"), so " Primary
    School" is appended before matching, same fix as Southwark.
    """
    url = ("https://lewisham.gov.uk/-/media/services/education/schools/primary-school/"
           "applying-to-start-primary-school-in-september.pdf")
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table or not table[0] or table[0][0] != "Name of school":
                continue
            for row in table[1:]:
                if not row or not row[0]:
                    continue
                distance = None
                for cell in row[1:]:
                    if cell and _STOCKPORT_DECIMAL_RE.match(cell.strip()):
                        distance = float(cell.strip())
                        break
                if distance is not None:
                    records.append({
                        "school_name": row[0].strip() + " Primary School",
                        "last_distance_miles": distance / _METRES_PER_MILE,
                    })
    return records


def fetch_merton() -> list[dict]:
    """London Borough of Merton's "Admissions and appeals data for
    recent years" PDF - a single large document going back several
    years, republished at a stable URL (find current one via
    merton.gov.uk's "recent-years" admissions page if it moves). Most
    recent secondary round is page index 2 (dated 3/3/25, right after
    the "Transfer to secondary school" section heading); most recent
    primary round is pages 9-11 (dated 16/4/2024 - the primary round
    is a full year further behind than secondary in this document,
    genuinely the latest published at time of writing). Each row can
    show two distance figures (initial + "second round" after
    appeals/late applications, sometimes "TBC" if not yet published);
    this takes the max of whichever are real numbers, same policy as
    other multi-round authorities. Merton's own primary names are
    abbreviated (e.g. "Hillcross" for "Hillcross Primary School"), so
    " Primary School" is appended only for the primary pages (the
    secondary page's names are already complete).
    """
    url = "https://www.merton.gov.uk/system/files/admissions_and_appeals_data_for_recent_years_-_table.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page_index, suffix in [(2, ""), (9, " Primary School"), (10, " Primary School"), (11, " Primary School")]:
            table = pdf.pages[page_index].extract_table()
            if not table:
                continue
            for row in table:
                if not row or not row[0]:
                    continue
                name = row[0].replace("\n", " ").strip()
                distances = []
                for cell in row[1:]:
                    if cell and "." in cell:
                        try:
                            distances.append(float(cell.strip()))
                        except ValueError:
                            pass
                if distances:
                    records.append({"school_name": name + suffix, "last_distance_miles": max(distances) / _METRES_PER_MILE})
    return records


_GREENWICH_DISTANCE_RE = re.compile(r"([\d.]+)")


def fetch_greenwich() -> list[dict]:
    """Royal Borough of Greenwich's "Primary School admissions data
    and statistics" Excel workbook - republished each year at a new
    URL under /sites/default/files/YYYY-MM/ (find the current one via
    royalgreenwich.gov.uk's downloads page, ID 1191 as of writing - no
    secondary equivalent was found). Uniquely among every source in
    this registry, it publishes the school's own URN directly (column
    "URN & DFE Numbers", formatted "<URN> & <DfE No>") rather than
    relying on name matching - so records here carry a "urn" key that
    build_records() uses directly (still checked against this
    authority's real URNs rather than trusted blindly). "Last distance
    offered in metres" is sometimes annotated (e.g. "21437.68 (Open
    Band)" for faith schools with banded admission) - only the leading
    number is taken. Section-header rows ("Planning Area 1") have no
    PAN and are naturally skipped.
    """
    url = "https://www.royalgreenwich.gov.uk/sites/default/files/2025-04/Primary_Stats_for_Website_2024.xlsx"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    wb = openpyxl.load_workbook(io.BytesIO(resp.content), data_only=True)
    ws = wb[wb.sheetnames[0]]

    records = []
    for row in ws.iter_rows(min_row=3, values_only=True):
        if not row or not row[1] or not row[2] or not row[20]:
            continue
        urn_match = re.match(r"\s*(\d+)", str(row[1]))
        distance_match = _GREENWICH_DISTANCE_RE.search(str(row[20]))
        if urn_match and distance_match:
            records.append({
                "school_name": (row[0] or "").strip(),
                "urn": int(urn_match.group(1)),
                "last_distance_miles": float(distance_match.group(1)) / _METRES_PER_MILE,
            })
    return records


_SUTTON_PRIMARY_URL = (
    "https://www.sutton.gov.uk/schools-and-learning/school-admissions/"
    "national-offer-day-primary-schools/primary-school-allocation-information"
)
_SUTTON_SECONDARY_URL = "https://www.sutton.gov.uk/sites/default/files/2026-04/TSS%20Guidance%20Booklet%202026v2.pdf"
_SUTTON_ROW_RE = re.compile(r"<tr><td>(.*?)</td><td>[^<]*</td><td>[^<]*</td><td>([^<]*)</td></tr>")
_SUTTON_NAME_SUFFIX_RE = re.compile(r"\s*\((Academy|Foundation|Voluntary-aided|Voluntary-controlled)\)\s*$")
_SUTTON_DECIMAL_RE = re.compile(r"[\d,]+\.\d+")


def fetch_sutton() -> list[dict]:
    """London Borough of Sutton publishes its primary allocation
    figures as a live HTML table (not a PDF) on its "primary-school-
    allocation-information" page, and its secondary figures inside the
    "Transfer to Secondary School" guidance booklet PDF, republished
    each year at a new URL under /sites/default/files/YYYY-MM/ (find
    the current one via sutton.gov.uk's secondary transfer page).
    Names carry a trailing admission-authority-type annotation (e.g.
    "Avenue Primary (Academy)") stripped before matching. Distances
    use comma thousands-separators ("2,697.92") which float() can't
    parse directly; a school with two sites (e.g. "Cheam High School
    (Worcester Park Places)") reports two figures on separate lines
    within one cell - takes the larger, consistent with other
    multi-figure sources.
    """
    records = []

    print(f"  Downloading {_SUTTON_PRIMARY_URL}")
    resp = httpx.get(_SUTTON_PRIMARY_URL, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    for name, cell in _SUTTON_ROW_RE.findall(resp.text):
        name = _SUTTON_NAME_SUFFIX_RE.sub("", name).strip()
        matches = _SUTTON_DECIMAL_RE.findall(cell.replace(",", ""))
        if matches:
            records.append({"school_name": name, "last_distance_miles": max(float(m) for m in matches) / _METRES_PER_MILE})

    print(f"  Downloading {_SUTTON_SECONDARY_URL}")
    resp = httpx.get(_SUTTON_SECONDARY_URL, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table or not table[0] or "Distance" not in str(table[0][-1]):
                continue
            for row in table[1:]:
                if not row or not row[0]:
                    continue
                matches = _SUTTON_DECIMAL_RE.findall((row[-1] or "").replace(",", ""))
                if matches:
                    name = row[0].replace("\n", " ").strip()
                    records.append({"school_name": name, "last_distance_miles": max(float(m) for m in matches) / _METRES_PER_MILE})
    return records


_HARINGEY_URLS = [
    "https://haringey.gov.uk/schools-learning/schools/school-admissions/how-school-place-offers-were-made/"
    "cutoff-distance-school-last-child-offered-place/"
    "primary-schools-distance-school-last-child-offered-place-national-offer-day",
    "https://haringey.gov.uk/schools-learning/schools/school-admissions/how-school-place-offers-were-made/"
    "cutoff-distance-school-last-child-offered-place/"
    "distance-school-last-child-offered-place-1-september",
]
_HARINGEY_ROW_RE = re.compile(r"<tr>\s*<td>(.*?)</td>((?:\s*<td>.*?</td>)+)\s*</tr>", re.DOTALL)
_HARINGEY_CELL_RE = re.compile(r"<td>(.*?)</td>")


def fetch_haringey() -> list[dict]:
    """Haringey Council publishes live HTML tables (not PDFs) of
    "Distance (in miles) from school of last child offered a place" -
    one for primary (national offer day, columns newest year first)
    and one for secondary (1 September, i.e. after the summer's late
    applications/appeals - same newest-first column order), both
    already in miles across several years. Takes the first (most
    recent) non-"N/A" column per row rather than always the newest
    year, since some schools' most recent year has no distance figure
    (not oversubscribed that year).
    """
    records = []
    for url in _HARINGEY_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        text = resp.text.replace("&nbsp;", " ")
        start = text.find("<tbody>")
        end = text.find("</tbody>")
        if start < 0 or end < 0:
            continue
        for name, cells in _HARINGEY_ROW_RE.findall(text[start:end]):
            clean_name = re.sub("<[^>]+>", "", name).strip()
            for value in _HARINGEY_CELL_RE.findall(cells):
                value = value.strip()
                if value and value.upper() not in ("N/A", "ALL"):
                    try:
                        records.append({"school_name": clean_name, "last_distance_miles": float(value)})
                    except ValueError:
                        pass
                    break
    return records


_CALDERDALE_URLS = [
    "https://dataworks.calderdale.gov.uk/download/vq9q6/t8y/Primary%20Preferences-Allocations%20by%20School%202026.csv",
    "https://dataworks.calderdale.gov.uk/download/v8357/127/Secondary%20Preferences-Allocations%20by%20School%202026.csv",
]
_CALDERDALE_DISTANCE_RE = re.compile(r"([\d.]+)")


def fetch_calderdale() -> list[dict]:
    """Calderdale Council publishes real open-data CSVs (Primary +
    Secondary preferences/allocations by school) on its "Calderdale
    Data Works" open data portal, one file per year since 2015 -
    republished each year at a new download ID (find the current ones
    via the dataset pages: dataworks.calderdale.gov.uk/dataset/vq9q6
    and .../v8357). "Distance of furthest pupil allocated a place (in
    miles)" is already in miles; a few rows carry an annotation like
    "0.351 (within catchment)" - only the leading number is taken.
    Blank cells (school not oversubscribed on distance) are correctly
    skipped.
    """
    records = []
    for url in _CALDERDALE_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        text = resp.content.decode("utf-8-sig", errors="replace")
        for line in text.splitlines()[1:]:
            if not line.strip():
                continue
            cells = next(csv.reader([line]))
            if len(cells) < 10 or not cells[0] or not cells[9].strip():
                continue
            match = _CALDERDALE_DISTANCE_RE.search(cells[9])
            if match:
                records.append({"school_name": cells[0].strip(), "last_distance_miles": float(match.group(1))})
    return records


_WALSALL_URLS = [
    "https://go.walsall.gov.uk/sites/default/files/2026-04/Reception%20Offers%202026_0.pdf",
    "https://go.walsall.gov.uk/sites/default/files/2026-04/Year%203%20Offers%202026_Nicholas%20Gill.pdf",
]


def fetch_walsall() -> list[dict]:
    """Walsall Council's "Reception Offers" and "Year 3 Offers" PDFs -
    republished each year at a new URL under /sites/default/files/
    YYYY-MM/ (find the current ones via go.walsall.gov.uk's primary
    admissions page). Rotated "Furthest Distance" column header
    extracts as reversed text, and the two files have a different
    column count (Year 3 has no Pupil Premium column), so the decisive
    figure is found via the same decimal-cell-detection approach as
    Stockport/Peterborough rather than a fixed index. Already in
    miles; "N/A"/"n/a" rows (not oversubscribed on distance) are
    naturally skipped.
    """
    records = []
    for url in _WALSALL_URLS:
        print(f"  Downloading {url}")
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                for row in table:
                    if not row or not row[0] or not isinstance(row[0], str) or not row[1] or not row[1].isdigit():
                        continue
                    distance = None
                    for cell in row[2:]:
                        if cell and _STOCKPORT_DECIMAL_RE.match(cell.strip()):
                            distance = float(cell.strip())
                            break
                    if distance is not None:
                        records.append({"school_name": row[0].replace("\n", " ").strip(), "last_distance_miles": distance})
    return records


def fetch_oldham() -> list[dict]:
    """Oldham Council's "Primary admissions summary of last place
    offered" PDF - republished each year at a new URL (find the
    current one via oldham.gov.uk, no secondary equivalent found).
    Clean table: School (prefixed "(Oldham) ", stripped here), PAN,
    Criteria, Distance - already in miles. "*" marks schools not
    oversubscribed on distance and is correctly skipped.
    """
    url = "https://www.oldham.gov.uk/download/downloads/id/8161/2025_primary_admissions_summary_of_last_place_offered.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
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
                name = row[0].replace("\n", " ").replace("(Oldham)", "").strip()
                records.append({"school_name": name, "last_distance_miles": distance})
    return records


_WIGAN_INDEX_URLS = [
    "https://www.wigan.gov.uk/resident/education/schools/school-admissions/primary-schools.aspx",
    "https://www.wigan.gov.uk/resident/education/schools/School-Admissions/Secondary-Schools.aspx",
]
_WIGAN_LINK_RE = re.compile(r'href="(/Docs/PDF/Resident/Education/Schools/Admissions/[^"]+\.pdf)"')
_WIGAN_DISTANCE_RE = re.compile(r"living\s*([\d.]+)\s*miles from the school")


def fetch_wigan() -> list[dict]:
    """Wigan Council publishes one individual "how places were
    allocated" PDF per school (both primary and secondary), listed on
    two index pages that this crawls for the current list rather than
    hardcoding them (find the current index pages via wigan.gov.uk's
    "school-admissions" section if the URL slugs change; excludes the
    generic booklets and appeals-info PDF that also appear in the same
    link list). Prose format: school name is the document's first
    line, and the decisive line reads "...living X.XXX miles from the
    school." Schools not oversubscribed have no such sentence and are
    naturally skipped.
    """
    records = []
    for index_url in _WIGAN_INDEX_URLS:
        print(f"  Downloading index {index_url}")
        resp = httpx.get(index_url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        paths = [p for p in set(_WIGAN_LINK_RE.findall(resp.text))
                 if "Booklet" not in p and "Appeals" not in p]
        for path in paths:
            url = f"https://www.wigan.gov.uk{path}"
            print(f"  Downloading {url}")
            try:
                pdf_resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
                pdf_resp.raise_for_status()
            except httpx.HTTPError:
                continue
            with pdfplumber.open(io.BytesIO(pdf_resp.content)) as pdf:
                full_text = pdf.pages[0].extract_text() or ""
            lines = full_text.split("\n")
            if not lines:
                continue
            name = lines[0].strip()
            match = _WIGAN_DISTANCE_RE.search(full_text)
            if match:
                records.append({"school_name": name, "last_distance_miles": float(match.group(1))})
    return records


def fetch_tameside() -> list[dict]:
    """Tameside's Primary and Secondary "allocation statistics" PDFs
    both publish the school's own real URN directly (Primary: a "URN"
    column; Secondary: a "School unique ref number" column) - matched
    directly like Greenwich, bypassing fuzzy name matching entirely
    (name cells are frequently garbled by pdfplumber merging wrapped
    multi-line cells, but the URN + Distance columns stay aligned
    regardless). One secondary row has its URN corrupted to "E 106270"
    by a line-wrap artifact - handled by extracting the digit run
    rather than trusting the whole cell.
    """
    records = []

    primary_url = "https://www.tameside.gov.uk/documents/d/guest/primary-full-stats-2024_1-pdf"
    resp = httpx.get(primary_url, timeout=60, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if len(row) < 18 or not row[2] or not row[2].isdigit() or not row[17]:
                    continue
                try:
                    distance = float(row[17])
                except ValueError:
                    continue
                records.append({"urn": int(row[2]), "school_name": row[3] or "", "last_distance_miles": distance})

    secondary_url = "https://www.tameside.gov.uk/documents/d/guest/secondary-2025-pdf"
    resp = httpx.get(secondary_url, timeout=60, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if len(row) < 21 or not row[1] or not row[20]:
                    continue
                urn_match = re.search(r"\d{5,7}", row[1])
                if not urn_match:
                    continue
                try:
                    distance = float(row[20])
                except ValueError:
                    continue
                records.append({"urn": int(urn_match.group()), "school_name": row[0] or "", "last_distance_miles": distance})

    return records


def fetch_gloucestershire() -> list[dict]:
    """Gloucestershire's Primary, Junior and Secondary "allocation day
    statistics" PDFs - clean tables with "Furthest distance allocated
    (miles)" as a fixed column. Rows that weren't oversubscribed show
    "N/A" (skipped); distances beyond 20 miles are deliberately
    redacted by the council to ">20" for data-protection reasons
    (skipped rather than guessed at). The "School DfE no." column is
    the LOCAL 4-digit establishment number, not the national URN, so
    matching is by (fuzzy) name like most other authorities.
    """
    urls = [
        "https://www.gloucestershire.gov.uk/media/ctgfusdi/primary-allocation-day-statistics-2025-1.pdf",
        "https://gloucestershire.gov.uk/media/0rzhtukd/junior-allocation-day-statistics-2025.pdf",
        "https://www.gloucestershire.gov.uk/media/v1jdwr0r/secondary-allocation-day-statistics-2025.pdf",
    ]
    records = []
    for url in urls:
        resp = httpx.get(url, timeout=60, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                for row in table:
                    if len(row) < 6 or not row[1] or not row[5]:
                        continue
                    try:
                        distance = float(row[5])
                    except ValueError:
                        continue
                    records.append({"school_name": row[1], "last_distance_miles": distance})
    return records


_WARWICKSHIRE_URLS = [
    "https://api.warwickshire.gov.uk/documents/WCCC-1990003847-4065",  # Reception 2025
    "https://api.warwickshire.gov.uk/documents/WCCC-1990003847-4066",  # Junior 2025
    "https://api.warwickshire.gov.uk/documents/WCCC-1990003847-3909",  # Secondary 2025
]


def fetch_warwickshire() -> list[dict]:
    """Warwickshire's Reception/Junior/Secondary "breakdown of offers"
    spreadsheets. Unlike most sources, EVERY row has a "Distance"
    figure regardless of whether that place was actually decided by
    proximity - undersubscribed schools that admitted a distant
    out-of-area applicant report that applicant's (irrelevant, huge)
    distance too, and the "Last Offer Made Criterion" text doesn't
    reliably distinguish this case (school-specific naming, and even
    some "In Priority Area" rows have 90+ mile outliers). Rather than
    trust or parse that free-text criterion column, distances over 20
    miles are treated as not representative of a real catchment and
    dropped (~7% of rows) - the same plausibility-cap approach used
    for other noisy sources (see Peterborough, Wokingham).
    """
    records = []
    for url in _WARWICKSHIRE_URLS:
        resp = httpx.get(url, timeout=60, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        wb = openpyxl.load_workbook(io.BytesIO(resp.content), data_only=True)
        ws = wb.worksheets[0]
        for row in ws.iter_rows(min_row=3, values_only=True):
            if not row[0] or row[4] is None:
                continue
            try:
                distance = float(row[4])
            except (TypeError, ValueError):
                continue
            if distance > 20:
                continue
            records.append({"school_name": row[0], "last_distance_miles": distance})
    return records


_STAFFORDSHIRE_URLS = [
    "https://www.staffordshire.gov.uk/schools-and-learning/school-admissions/admission-oversubscribed-schools/summary-september-2025-0",
    "https://www.staffordshire.gov.uk/schools-and-learning/school-admissions/admission-oversubscribed-schools/summary-september-2026",
]
_STAFFS_TABLE_RE = re.compile(r"<table.*?>(.*?)</table>", re.DOTALL)
_STAFFS_HEADER_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.DOTALL)
_STAFFS_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_STAFFS_CELL_RE = re.compile(r"<td>(.*?)</td>", re.DOTALL)


def _staffs_clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text).replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip()


def fetch_staffordshire() -> list[dict]:
    """Staffordshire's "summary of admission to oversubscribed
    schools" pages (Primary 2025 + Secondary 2026 - the most recent
    of each found), one HTML table per school with a "Furthest
    distance (miles)" column. The exact set of preceding criteria
    columns (Sibling/Catchment/Staff child/Pupil Premium/etc.) varies
    per school, so the distance column's index is located dynamically
    from each table's own header rather than assumed fixed. Schools
    not oversubscribed on distance show a blank or "N/A" cell there
    and are skipped.
    """
    records = []
    for url in _STAFFORDSHIRE_URLS:
        resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        for table_html in _STAFFS_TABLE_RE.findall(resp.text):
            headers = [_staffs_clean(h) for h in _STAFFS_HEADER_RE.findall(table_html)]
            dist_idx = next((i for i, h in enumerate(headers) if "furthest distance" in h.lower()), None)
            if dist_idx is None:
                continue
            for row in _STAFFS_ROW_RE.findall(table_html):
                cells = _STAFFS_CELL_RE.findall(row)
                if len(cells) != len(headers):
                    continue
                name = _staffs_clean(cells[0])
                dist_text = _staffs_clean(cells[dist_idx])
                if not name or not dist_text:
                    continue
                try:
                    distance = float(dist_text)
                except ValueError:
                    continue
                records.append({"school_name": name, "last_distance_miles": distance})
    return records


def fetch_kirklees() -> list[dict]:
    """Kirklees' Reception "by preference and criteria" PDF - several
    tables per document (Community/Voluntary Controlled schools, Own
    Admission Authority schools, and a preference-summary table with
    no distance column at all), with a differing number of criteria
    columns between the first two. Rather than assume a fixed layout,
    only tables whose header row mentions "distance" are used, and
    within those the PAN column (index 1) must be a plain digit to
    skip header/section-title rows. Distances are in metres. No
    secondary/Year 7 equivalent was found at a stable URL.
    """
    url = "https://www.kirklees.gov.uk/beta/admissions/pdf/reception-by-preference-and-criteria-25.pdf"
    resp = httpx.get(url, timeout=60, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                if not table or not any(h and "distance" in h.lower() for h in table[0]):
                    continue
                for row in table[1:]:
                    if len(row) < 2 or not row[1] or not row[1].isdigit():
                        continue
                    name, dist_text = row[0], row[-1]
                    if not name or not dist_text:
                        continue
                    try:
                        distance_m = float(dist_text)
                    except ValueError:
                        continue
                    records.append({"school_name": name, "last_distance_miles": distance_m / _METRES_PER_MILE})
    return records


_CHESHIRE_WEST_URLS = [
    "https://www.cheshirewestandchester.gov.uk/asset-library/cwc-primary-guide-2025-26-online-final-version-mar-20251.pdf",
    "https://www.cheshirewestandchester.gov.uk/asset-library/secondary-school-guide-2025-26-final-online12.pdf",
]


_CHESHIRE_WEST_POSTCODE_RE = re.compile(r"[A-Z]{1,2}\d[A-Z\d]?\s+\d[A-Z]{2}$")


def _cheshire_west_name(cell: str) -> str:
    lines = [line.strip() for line in cell.split("\n") if line.strip()]
    if not lines:
        return ""
    postcode_idx = next((i for i, line in enumerate(lines) if _CHESHIRE_WEST_POSTCODE_RE.search(line)), None)
    if not postcode_idx:
        return lines[0]
    # The address is 1 or 2 lines ending at the postcode line - a
    # 2-line address (street, then town + postcode) is told apart from
    # a 2-line school name by whether the preceding line has a comma
    # (an address fragment) rather than being part of the name, but
    # never mistake line 0 itself for an address line even if the
    # school's own name happens to contain a comma (e.g. "The County
    # High School, Leftwich").
    addr_start = postcode_idx
    if postcode_idx - 1 > 0 and "," in lines[postcode_idx - 1]:
        addr_start = postcode_idx - 1
    return " ".join(lines[:addr_start]) or lines[0]


def fetch_cheshire_west_and_chester() -> list[dict]:
    """Cheshire West and Chester's Primary and Secondary admissions
    guide PDFs have a per-school table row with a "Lowest criteria"
    and "Furthest distance" column - but pdfplumber extracts this
    section of every page as right-to-left, so BOTH the criteria label
    ("Distance" -> "ecnatsiD") and the distance figure itself
    (e.g. "1.049" -> "940.1") come out character-reversed, not just the
    column headers (the more common case elsewhere in this script).
    Only rows whose reversed criteria label is exactly "Distance" are
    used; other rows (a named criterion number, "N/A", or blank) mean
    the school wasn't oversubscribed on distance and are skipped. The
    school name can wrap across 1-2 lines before the address line
    (identified by the following "Tel:" line), so both are joined.
    """
    records = []
    for url in _CHESHIRE_WEST_URLS:
        resp = httpx.get(url, timeout=90, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                table = page.extract_table()
                if not table:
                    continue
                for row in table:
                    if len(row) < 3:
                        continue
                    criterion = (row[-3] or "").replace("\n", "").strip()
                    if criterion.lower() != "ecnatsid":
                        continue
                    value = (row[-2] or "").strip()
                    if not value:
                        continue
                    try:
                        distance = float(value[::-1])
                    except ValueError:
                        continue
                    name = _cheshire_west_name(row[1] or "")
                    if name:
                        records.append({"school_name": name, "last_distance_miles": distance})
    return records


def fetch_bristol() -> list[dict]:
    """Bristol's "furthest distance table" - one row per primary
    school with a column per year back to 2020 (most recent last,
    2026), distance in kilometres. Non-numeric markers ("D" = not
    needed as a tie-break, "O" = faith/random allocation rather than
    distance, "N" = not open) mean that year isn't usable, so this
    scans backwards from the most recent year and takes the first
    column that parses as a plain number. A handful of cells in older
    year-columns are corrupted by an overlapping-text PDF rendering
    glitch (e.g. "0.68 6", "D7") - these simply fail to parse and are
    skipped in the backwards scan like any other non-numeric year.
    """
    url = "https://www.bristol.gov.uk/files/documents/3382-furthest-distance-table/file"
    resp = httpx.get(url, timeout=60, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or not row[0] or len(row) < 2:
                    continue
                name = row[0].strip()
                if not name or name.lower().startswith("name of school"):
                    continue
                for cell in reversed(row[1:]):
                    if not cell:
                        continue
                    try:
                        distance_km = float(cell.strip())
                    except ValueError:
                        continue
                    records.append({"school_name": name, "last_distance_miles": distance_km / 1.60934})
                    break
    return records


_NORTH_YORKSHIRE_URLS = [
    "https://hub.datanorthyorkshire.org/dataset/8e128b63-0967-43bb-929d-a4058e30f1e3/resource/94bf88ea-6c2f-4b69-80a5-09e990b2429b/download/2025-26-primary-statistics-school-admissions.xlsx",
    "https://hub.datanorthyorkshire.org/dataset/8e128b63-0967-43bb-929d-a4058e30f1e3/resource/33e95fc2-8373-4fe0-80cd-dfccbddcfaa7/download/2025-26-secondary-statistics-school-admissions.xlsx",
]
_NORTH_YORKSHIRE_DISTANCE_RE = re.compile(r"([\d.]+)")


def fetch_north_yorkshire() -> list[dict]:
    """North Yorkshire's open-data Primary/Secondary admissions
    spreadsheets (Data North Yorkshire hub) - the distance column is
    free text like "Out of Catchment Distance - 3.433 miles" rather
    than a plain number, so the first decimal number in the cell is
    extracted. "N/A" (not oversubscribed) and "Contact the school"
    (own admission authority) rows have no number and are skipped.
    """
    records = []
    for url in _NORTH_YORKSHIRE_URLS:
        resp = httpx.get(url, timeout=60, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        wb = openpyxl.load_workbook(io.BytesIO(resp.content), data_only=True)
        ws = wb.worksheets[0]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row[1] or not row[8]:
                continue
            match = _NORTH_YORKSHIRE_DISTANCE_RE.search(str(row[8]))
            if not match:
                continue
            records.append({"school_name": row[1], "last_distance_miles": float(match.group(1))})
    return records


_READING_PRIMARY_URL = "https://brighterfuturesforchildren.org/wp-content/uploads/2025/04/Primary-Junior-Allocations-for-All-Schools-2025.pdf"
_READING_SECONDARY_URL = "https://brighterfuturesforchildren.org/wp-content/uploads/2025/03/Secondary-Places-2025-Table.pdf"
_READING_BRACKET_DISTANCE_RE = re.compile(r"\((\d+\.\d+)\)")
_READING_SECONDARY_NAME_RE = re.compile(r"([A-Z][^\n]{2,80}?)\n(?:Category )?Admission Number")
_READING_SECONDARY_DISTANCE_RE = re.compile(r"([\d.]+) miles from")


def fetch_reading() -> list[dict]:
    """Reading's (published by its outsourced children's-services
    trust, Brighter Futures for Children) Primary/Junior allocations
    table embeds the tie-break distance in brackets within whichever
    oversubscription-category cell it happened to occur in (e.g.
    "17 (0.331)", vs a non-distance bracket like "5 (1)" for an EYPP
    count) - searching the whole row for a bracketed DECIMAL number is
    the only reliable way to find it, since decimals never appear for
    plain headcounts. The Secondary document is a different, prose
    layout with no distinct table per school (short entries are
    packed onto shared pages) - schools are split apart by matching
    the school-name line that immediately precedes "Admission Number",
    then a "<X.XXX> miles from" sentence is searched for within each
    school's block of text.
    """
    records = []

    resp = httpx.get(_READING_PRIMARY_URL, timeout=60, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or not row[0]:
                    continue
                name = row[0].replace("\n", " ").strip()
                if not name or "oversubscription" in name.lower() or "admissions to" in name.lower() or name.lower() == "name of school":
                    continue
                joined = " ".join(cell for cell in row if cell)
                matches = _READING_BRACKET_DISTANCE_RE.findall(joined)
                if matches:
                    records.append({"school_name": name, "last_distance_miles": float(matches[-1])})

    resp = httpx.get(_READING_SECONDARY_URL, timeout=60, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    full_text = ""
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            full_text += (page.extract_text() or "") + "\n"
    name_matches = list(_READING_SECONDARY_NAME_RE.finditer(full_text))
    for i, m in enumerate(name_matches):
        name = m.group(1).rstrip(".").strip()
        start = m.end()
        end = name_matches[i + 1].start() if i + 1 < len(name_matches) else len(full_text)
        distance_match = _READING_SECONDARY_DISTANCE_RE.search(full_text[start:end])
        if distance_match:
            records.append({"school_name": name, "last_distance_miles": float(distance_match.group(1))})

    return records


_BURY_URLS = [
    "https://www.bury.gov.uk/asset-library/historical-primary-20251.pdf",
    "https://www.bury.gov.uk/asset-library/historical-community-secondary-schools-allocation-information2.pdf",
]
_BURY_NAME_RE = re.compile(r"\n([A-Za-z][^\n]{2,60})\nPublished")


def fetch_bury() -> list[dict]:
    """Bury's "historical information" PDFs (Primary, and Community
    Secondary - the separate Voluntary Aided secondary one has no
    distance column at all, faith/preference-based only, so it's
    skipped) list 5-6 years per school, each year on its own line with
    a distance figure somewhere in it (its column position differs
    between the two documents), or "All"/"-" for a year not
    oversubscribed on distance. Schools are split apart by matching
    the name line immediately before a "Published" header line; within
    each school's block, the most recent year (scanning backwards)
    whose line contains an actual decimal number is used - this also
    naturally skips a row mangled by an unrelated PDF text-wrapping
    glitch (seen on one school, "The Derby") that dropped its real
    distance onto a separate line, since the visible integer-only
    remainder has no decimal for this to match. Primary school names
    in that document are bare ("Cams Lane"), so " Primary School" is
    appended before matching - secondary names are already given in
    full ("Hazel Wood" -> "Hazel Wood High School" fuzzy-matches fine
    without one).
    """
    records = []
    for url in _BURY_URLS:
        is_primary = url is _BURY_URLS[0]
        resp = httpx.get(url, timeout=60, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        full_text = ""
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for page in pdf.pages:
                full_text += (page.extract_text() or "") + "\n"
        matches = list(_BURY_NAME_RE.finditer(full_text))
        for i, m in enumerate(matches):
            name = m.group(1).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
            block = full_text[start:end]
            for year in range(2026, 2020, -1):
                year_match = re.search(rf"^{year}\s+(.*)$", block, re.MULTILINE)
                if not year_match:
                    continue
                # Distance is the only decimal-formatted value on the line (PAN,
                # preference/category counts, appeals and totals are all plain
                # integers) - its column position differs between the Primary and
                # Secondary documents, so this searches for the decimal rather than
                # assuming an index. This also naturally excludes a row mangled by
                # a PDF text-wrapping glitch (seen on one school, "The Derby") that
                # dropped its real distance onto a separate line, since the
                # visible integer-only remainder has no "." for this to match.
                distance_match = re.search(r"(\d+\.\d+)", year_match.group(1))
                if not distance_match:
                    continue
                full_name = f"{name} Primary School" if is_primary else name
                records.append({"school_name": full_name, "last_distance_miles": float(distance_match.group(1))})
                break
    return records


_BOLTON_CATEGORY_URLS = [
    "https://www.bolton.gov.uk/directory/4/school-directory/category/12",  # Primary
    "https://www.bolton.gov.uk/directory/4/school-directory/category/15",  # Secondary
]
_BOLTON_ROW_RE = re.compile(
    r"<td>last distance offered</td>((?:\s*<td[^>]*>[^<]*</td>){2,6})", re.IGNORECASE
)
_BOLTON_CELL_RE = re.compile(r"<td[^>]*>([^<]*)</td>")
_BOLTON_DISTANCE_RE = re.compile(r"([\d.]+)\s*miles")


def fetch_bolton() -> list[dict]:
    """Bolton's school directory has an individual HTML profile page
    per school (crawled from the paginated Primary and Secondary
    category listings, since the exact page count shifts as schools
    open/close) with a "last distance offered" table row giving 5
    years of figures side by side (oldest first) - the rightmost
    non-blank cell is the most recent oversubscribed year. School name
    comes from the page's own link text on the category listing page
    (already the full official name), not re-derived from the profile
    page itself.
    """
    urls: dict[str, str] = {}
    for category_url in _BOLTON_CATEGORY_URLS:
        page = 1
        while True:
            url = category_url if page == 1 else f"{category_url}/{page}"
            resp = httpx.get(url, timeout=30, follow_redirects=True, headers=HEADERS)
            resp.raise_for_status()
            links = re.findall(
                r'<a class="[^"]*list__link[^"]*" href="(/directory-record/[^"]+)">([^<]+)</a>', resp.text
            )
            if not links:
                break
            for path, name in links:
                urls[path] = name.replace("&#039;", "'").replace("&amp;", "&").strip()
            if f"{category_url}/{page + 1}" not in resp.text:
                break
            page += 1

    records = []
    for path, name in urls.items():
        resp = httpx.get(f"https://www.bolton.gov.uk{path}", timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        row_match = _BOLTON_ROW_RE.search(resp.text)
        if not row_match:
            continue
        cells = _BOLTON_CELL_RE.findall(row_match.group(1))
        for cell in reversed(cells):
            distance_match = _BOLTON_DISTANCE_RE.search(cell)
            if distance_match:
                records.append({"school_name": name, "last_distance_miles": float(distance_match.group(1))})
                break
    return records


_SALFORD_PRIMARY_URL = "https://www.salford.gov.uk/media/1x4phafq/primary-schools-2025-intake.pdf"
_SALFORD_SECONDARY_URL = (
    "https://www.salford.gov.uk/schools-and-learning/schools-admissions/secondary/"
    "how-school-places-were-allocated-last-year/secondary-school-allocation-history-on-offer-day-1-march-2025/"
)
_SALFORD_DISTANCE_RE = re.compile(r"distance of ([\d.]+) miles")
_SALFORD_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.DOTALL)
_SALFORD_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)


def fetch_salford() -> list[dict]:
    """Salford's Primary intake PDF and Secondary allocation-history
    webpage (not a PDF - the HTML page itself is a plain table) both
    describe the decisive distance in prose ("All applicants in
    categories 1-6 to a distance of 0.906 miles") rather than a
    dedicated numeric column, alongside undersubscribed rows ("All
    applicants offered places") or faith-school rows with no distance
    criterion at all ("Please contact school for finer detail") -
    searched for generically rather than parsed positionally, since
    both sources use the same prose phrasing.
    """
    records = []

    resp = httpx.get(_SALFORD_PRIMARY_URL, timeout=60, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table:
                continue
            for row in table:
                if not row or not row[0]:
                    continue
                joined = " ".join(cell for cell in row if cell)
                match = _SALFORD_DISTANCE_RE.search(joined)
                if match:
                    name = row[0].replace("\n", " ").strip()
                    records.append({"school_name": name, "last_distance_miles": float(match.group(1))})

    resp = httpx.get(_SALFORD_SECONDARY_URL, timeout=30, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    for row_html in _SALFORD_ROW_RE.findall(resp.text):
        cells = _SALFORD_CELL_RE.findall(row_html)
        if len(cells) < 5:
            continue
        joined = " ".join(cells)
        match = _SALFORD_DISTANCE_RE.search(joined)
        if not match:
            continue
        name = re.sub(r"<[^>]+>", "", cells[0]).strip()
        if name:
            records.append({"school_name": name, "last_distance_miles": float(match.group(1))})

    return records


_KNOWSLEY_INDEX_URLS = [
    "https://www.knowsley.gov.uk/education-and-schools/school-admissions/apply-primary-school-september-2026/oversubscribed-primary",
    "https://www.knowsley.gov.uk/education-and-schools/school-admissions/apply-secondary-school-september-2026/secondary-school",
]
_KNOWSLEY_DISTANCE_RE = re.compile(r"lived ([\d.]+)\s*(?:miles\s*)?from the school")


def fetch_knowsley() -> list[dict]:
    """Knowsley crawls two index pages (Primary/Secondary "oversubscribed
    schools") for individual per-school "oversubscription breakdown"
    PDFs, one per oversubscribed school (the set changes yearly).
    School name is the first line of the PDF's own text; the decisive
    distance is prose ("...lived 0.275 miles from the school (as the
    crow flies)") - the Primary documents sometimes omit the word
    "miles" from that sentence while Secondary always includes it, so
    it's made optional in the pattern.
    """
    records = []
    for index_url in _KNOWSLEY_INDEX_URLS:
        resp = httpx.get(index_url, timeout=30, follow_redirects=True, headers=HEADERS)
        resp.raise_for_status()
        pdf_urls = set(re.findall(r'href="(https://www\.knowsley\.gov\.uk/sites/default/files/[^"]+\.pdf)"', resp.text))
        for pdf_url in pdf_urls:
            pdf_resp = httpx.get(pdf_url, timeout=30, follow_redirects=True, headers=HEADERS)
            pdf_resp.raise_for_status()
            with pdfplumber.open(io.BytesIO(pdf_resp.content)) as pdf:
                text = pdf.pages[0].extract_text() or ""
            name = text.split("\n")[0].strip()
            match = _KNOWSLEY_DISTANCE_RE.search(text)
            if name and match:
                records.append({"school_name": name, "last_distance_miles": float(match.group(1))})
    return records


def fetch_sefton() -> list[dict]:
    """Sefton Council's "Schools Admissions Information Guide" - a
    huge (200+ page) composite prospectus with a per-school profile
    section, republished each year at a new URL (find the current one
    via sefton.gov.uk's "startingschool" page). The school name is the
    first line of text on its profile's first page; "Table 2: How
    places were allocated" has an "If oversubscribed furthest distance
    (miles)" column (most other header cells on this table extract as
    reversed text, but this one doesn't, so it's used as the anchor)
    with one row per recent year, most recent first - takes that first
    row's value. Faith/academy schools that set their own admissions
    aren't included in this table and are naturally skipped.
    """
    url = "https://www.sefton.gov.uk/media/oavhszda/sefton-schools-admissions-information-guide-2026.pdf"
    print(f"  Downloading {url}")
    resp = httpx.get(url, timeout=60, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()

    records = []
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        for page in pdf.pages:
            table = page.extract_table()
            if not table or not any("furthest distance" in str(c).lower() for c in table[0]):
                continue
            if len(table) < 2 or not table[1] or not table[1][-1]:
                continue
            try:
                distance = float(table[1][-1].strip())
            except ValueError:
                continue
            text = page.extract_text() or ""
            name = text.split("\n")[0].strip()
            if name:
                records.append({"school_name": name, "last_distance_miles": distance})
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
    ("Derby", "2026/27", fetch_derby),
    ("Leicester", "varies", fetch_leicester),
    ("Sandwell", "varies", fetch_sandwell),
    ("Dudley", "2025", fetch_dudley),
    ("Richmond upon Thames", "2024/25", fetch_richmond_upon_thames),
    ("Kingston upon Thames", "2024/25", fetch_kingston_upon_thames),
    ("Southwark", "2025/26", fetch_southwark),
    ("Lambeth", "2024/25", fetch_lambeth),
    ("Waltham Forest", "varies", fetch_waltham_forest),
    ("Lewisham", "2025/26", fetch_lewisham),
    ("Merton", "varies", fetch_merton),
    ("Greenwich", "2024/25", fetch_greenwich),
    ("Sutton", "2025/26", fetch_sutton),
    ("Haringey", "varies", fetch_haringey),
    ("Calderdale", "2026/27", fetch_calderdale),
    ("Walsall", "2025/26", fetch_walsall),
    ("Oldham", "2025/26", fetch_oldham),
    ("Wigan", "2025/26", fetch_wigan),
    ("Sefton", "2025/26", fetch_sefton),
    ("Tameside", "varies", fetch_tameside),
    ("Gloucestershire", "2025/26", fetch_gloucestershire),
    ("Warwickshire", "2025/26", fetch_warwickshire),
    ("Staffordshire", "varies", fetch_staffordshire),
    ("Kirklees", "2025/26", fetch_kirklees),
    ("Cheshire West and Chester", "2025/26", fetch_cheshire_west_and_chester),
    ("Bristol, City of", "varies", fetch_bristol),
    ("North Yorkshire", "2025/26", fetch_north_yorkshire),
    ("Reading", "2025/26", fetch_reading),
    ("Bury", "varies", fetch_bury),
    ("Bolton", "varies", fetch_bolton),
    ("Salford", "2025/26", fetch_salford),
    ("Knowsley", "2026/27", fetch_knowsley),
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
        valid_urns = {urn for urn, _ in schools_in_la}

        matched = 0
        unmatched = []
        for row in rows:
            # A source that publishes its own URN (e.g. Greenwich) is matched directly rather than
            # by fuzzy name, but still checked against this authority's real URNs rather than trusted blindly.
            urn = row["urn"] if row.get("urn") in valid_urns else _match_urn(row["school_name"], candidates)
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
