"""How each English council decides planning applications, from MHCLG's
planning live tables, into a small local file.

Source: https://www.gov.uk/government/statistical-data-sets/
        live-tables-on-planning-application-statistics
Tables: P134 (applications received, decided, granted and delegated, by
        local planning authority, year ending June 2026) for the council
        figures, and P120 (the England quarterly series) for the two
        statutory-period percentages, which are published nationally and
        not by authority.
Refresh: quarterly, about three months after the quarter ends. The file
         name on the page changes each time, so this script reads the
         page and takes the current Tables_P120_to_P138 link.

Why this is worth having: "could I extend it" is one of the first
questions about a house that needs work, and part of the answer is the
council rather than the house. Nothing else in the report says whether
this authority grants nine applications in ten or seven, or how often
it has to ask for more time.

England only. Wales, Scotland and Northern Ireland publish their own
planning statistics and are not in these tables, so the card says so.

    .venv/Scripts/python.exe scripts/import_planning_decisions.py

Writes app/data/planning_decisions.json. Re-runnable: the file is replaced.
"""
import datetime as dt
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PAGE = ("https://www.gov.uk/government/statistical-data-sets/"
        "live-tables-on-planning-application-statistics")
LINK = re.compile(r'https://assets\.publishing\.service\.gov\.uk/media/[0-9a-f]+/'
                  r'Tables_P120_to_P138[^"]*\.ods')
OUT = os.path.join(ROOT, "app", "data", "planning_decisions.json")
UA = {"User-Agent": "UKPropertyInsight import (support@ukpropertyinsight.co.uk)"}
NS = {"table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
      "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0"}

# The rows above the councils in P134: England, the nine regions and the
# National Parks line, which is an aggregate rather than an authority.
NOT_AN_AUTHORITY = re.compile(r"^(England|East Midlands|East of England|London|National Parks|"
                              r"North East|North West|South East|South West|West Midlands|"
                              r"Yorkshire and the Humber)$")


def latest_ods() -> str:
    page = httpx.get(PAGE, headers=UA, timeout=120, follow_redirects=True)
    page.raise_for_status()
    found = LINK.search(page.text)
    if not found:
        raise SystemExit("the planning live tables page no longer links a Tables_P120_to_P138 file")
    return found.group(0)


def sheets(url: str) -> dict:
    print(f"Downloading {url}")
    body = httpx.get(url, headers=UA, timeout=300, follow_redirects=True).content
    root = ET.parse(io.BytesIO(zipfile.ZipFile(io.BytesIO(body)).read("content.xml"))).getroot()
    out = {}
    for sheet in root.iter("{%s}table" % NS["table"]):
        rows = []
        for row in sheet.findall("table:table-row", NS):
            line = []
            for cell in row.findall("table:table-cell", NS):
                repeat = int(cell.get("{%s}number-columns-repeated" % NS["table"], 1))
                value = cell.get("{%s}value" % NS["office"])
                if value is None:
                    value = "".join(cell.itertext()).strip()
                line.extend([value] * min(repeat, 40))
            if any(line):
                rows.append(line)
        out[sheet.get("{%s}name" % NS["table"])] = rows
    return out


def number(value):
    """A float, or an int where the publisher gave a whole number: P120's
    speed percentages are published as 19 and 39, and "19.0%" on the card
    would claim a decimal place the table does not have."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return int(value) if value == int(value) else value


def share(part, whole):
    if not whole or part is None:
        return None
    return round(100 * part / whole, 1)


def read_p134(rows):
    """Every authority row of P134, keyed by its ONS code."""
    title = rows[0][0]
    period = title.split(",")[-1].strip()      # "year ending June 2026"
    head = next(row for row in rows if row and row[0].startswith("Planning authority"))
    columns = {name: index for index, name in enumerate(head)}

    def pick(row, name):
        index = columns.get(name)
        return number(row[index]) if index is not None and index < len(row) else None

    england, authorities = None, {}
    for row in rows[rows.index(head) + 1:]:
        name, code = row[0].strip(), (row[1].strip() if len(row) > 1 else "")
        decisions = pick(row, "Total number of decisions")
        if not name or decisions is None:
            continue
        granted = pick(row, "Total number of decisions granted")
        entry = {
            "name": name,
            "received": int(pick(row, "Total number of applications received") or 0),
            "decisions": int(decisions),
            "granted": int(granted or 0),
            "granted_pct": share(granted, decisions),
            "extension_pct": share(pick(row, "Decisions where an extension of time agreement was made"),
                                   decisions),
            "delegated_pct": round(pick(row, "Percentage of decisions delegated to officers") or 0, 1),
        }
        if name == "England":
            england = entry
        elif not NOT_AN_AUTHORITY.match(name) and code.startswith("E"):
            authorities[code] = entry
    if england is None or len(authorities) < 250:
        raise SystemExit(f"P134 did not read as expected: {len(authorities)} authorities")
    return period, england, authorities


def read_p120(rows):
    """The latest England quarter: the two statutory-period percentages,
    which are published nationally and never by authority."""
    head = next(row for row in rows if row and row[0] == "Year")
    columns = {name: index for index, name in enumerate(head)}
    major = next(name for name in columns if "major applications decided within" in name)
    minor = next(name for name in columns if "minor applications decided within" in name)
    last = [row for row in rows[rows.index(head) + 1:] if len(row) > 1 and row[1].strip()][-1]
    return {
        "quarter": last[1].strip(),
        "major_in_time_pct": number(last[columns[major]]),
        "minor_in_time_pct": number(last[columns[minor]]),
        "granted_pct": number(last[columns["% of decisions granted [note 2]"]]),
        "major_weeks": 13,
        "minor_weeks": 8,
    }


def main():
    url = latest_ods()
    book = sheets(url)
    period, england, authorities = read_p134(book["LT_P134"])
    national = read_p120(book["LT_P120"])
    payload = {
        "about": ("How each English local planning authority decided planning applications over a "
                  "year: how many it decided, how many it granted, how often it agreed an extension "
                  "of time, and how much it leaves to officers rather than a committee. Council "
                  "figures are MHCLG's table P134. The two statutory-period percentages are "
                  "published for England as a whole, never by authority, and are table P120's "
                  "latest quarter."),
        "source": "MHCLG planning application statistics",
        "page": PAGE,
        "file": url,
        "tables": ["P134", "P120"],
        "period": period,
        "read_on": dt.date.today().isoformat(),
        "coverage": ["England"],
        "coverage_note": ("Wales, Scotland and Northern Ireland publish their own planning "
                          "statistics and are not in these tables."),
        "england": england,
        "national_speed": national,
        "authorities": authorities,
    }
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    print(f"Wrote {OUT}")
    print(f"  {period}, {len(authorities)} local planning authorities")
    print(f"  England: {england['decisions']:,} decisions, {england['granted_pct']}% granted, "
          f"{england['extension_pct']}% with an extension of time")
    print(f"  {national['quarter']}: {national['major_in_time_pct']}% of major and "
          f"{national['minor_in_time_pct']}% of minor decided in the statutory period")


if __name__ == "__main__":
    main()
