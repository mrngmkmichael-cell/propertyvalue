"""Build app/data/council_finance.json: each English council's Band D bill
over the years, its Exceptional Financial Support from government, and
the section 114 notices it has issued.

Sources, all official:
- MHCLG, "Live tables on Council Tax", the Band D file (Table 5, "Area_CT":
  the Band D bill a household actually pays in each billing authority,
  including county, police, fire and Greater London precepts), every year
  since 1993-94, keyed by ONS code. Open Government Licence.
  https://www.gov.uk/government/statistical-data-sets/live-tables-on-council-tax
- MHCLG, "Exceptional financial support for local authorities", the
  guidance page for each year from 2020-21, which tables the councils the
  government agreed to help set a balanced budget and the sums.
  https://www.gov.uk/government/collections/exceptional-financial-support-for-local-authorities
- Section 114 notices: the councils' own published notices (a chief finance
  officer's statement that the council cannot balance its budget). Listed
  here by hand with their dates because no national register exists;
  add to S114 when a council issues one.

    python scripts/import_council_finance.py

Re-runnable; refresh each April when the Band D table and the EFS list
for the new year appear. Needs no key.
"""
import datetime
import html
import io
import json
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "app", "data", "council_finance.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) UKPropertyInsight-import/1.0"}
LIVE_TABLES = "https://www.gov.uk/government/statistical-data-sets/live-tables-on-council-tax"
EFS_PAGE = "https://www.gov.uk/guidance/exceptional-financial-support-for-local-authorities-for-{year}"
EFS_YEARS = ("2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26", "2026-27")
YEARS_KEPT = 8
T = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
P = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p"

# Section 114 notices since 2018, from the councils' own notices. A council
# appears once per notice. Dates are the notice dates.
S114 = {
    "E10000021": [("2018-02-02", "First notice"), ("2018-07-24", "Second notice")],       # Northamptonshire County Council (abolished 2021)
    "E09000008": [("2020-11-11", "First notice"), ("2020-12-02", "Second notice"), ("2022-11-22", "Third notice")],  # Croydon
    "E06000039": [("2021-07-02", "Notice")],                                                # Slough
    "E06000018": [("2021-12-08", "Notice on the housing revenue account"), ("2023-11-29", "Notice")],  # Nottingham
    "E06000034": [("2022-12-19", "Notice")],                                                # Thurrock
    "E07000217": [("2023-06-07", "Notice")],                                                # Woking
    "E08000025": [("2023-09-05", "First notice"), ("2023-09-22", "Second notice")],         # Birmingham
}
S114_AS_OF = "2026-09-07"


def _get(client: httpx.Client, url: str) -> httpx.Response:
    r = client.get(url, headers=UA, timeout=120, follow_redirects=True)
    r.raise_for_status()
    return r


def _sheet(root, name: str) -> list[list[str]]:
    for t in root.iter(T + "table"):
        if t.get(T + "name") != name:
            continue
        rows = []
        for r in t.findall(T + "table-row"):
            cells = []
            for c in r.findall(T + "table-cell"):
                rep = int(c.get(T + "number-columns-repeated", "1"))
                txt = "".join(p.text or "" for p in c.iter(P)).strip()
                cells.extend([txt] * min(rep, 120))
            rows.append(cells)
        return rows
    raise SystemExit(f"sheet {name} not found")


def band_d_history(client: httpx.Client) -> dict[str, dict]:
    page = _get(client, LIVE_TABLES).text
    m = re.search(r'(https://assets\.publishing\.service\.gov\.uk/media/[^"]+/Band_D_[^"]+\.ods)', page)
    if not m:
        raise SystemExit("could not find the Band D ODS on the live tables page")
    ods = _get(client, m.group(1)).content
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(ods)).read("content.xml"))
    rows = _sheet(root, "Area_CT")
    header_i = next(i for i, r in enumerate(rows) if r[:2] == ["Code", "ONS Code"])
    header = rows[header_i]
    year_cols = [(i, h) for i, h in enumerate(header) if re.match(r"^\d{4} to \d{4}$", h)]
    out = {}
    for r in rows[header_i + 1:]:
        if len(r) < 6 or not r[1].startswith(("E", "W")):
            continue
        history = {}
        for i, h in year_cols:
            if i < len(r):
                try:
                    history[h.replace(" to ", "-")] = float(r[i].replace(",", ""))
                except ValueError:
                    pass
        if not history:
            continue
        years = sorted(history)[-YEARS_KEPT:]
        rises = {}
        for a, b in zip(years, years[1:]):
            if history[a]:
                rises[b] = round((history[b] / history[a] - 1) * 100, 1)
        out[r[1]] = {
            "name": r[2],
            "current": r[3] == "YES",
            "class": r[4],
            "band_d": {y: history[y] for y in years},
            "rises": rises,
        }
    return out


def efs_lists(client: httpx.Client) -> dict[str, list[tuple[str, str]]]:
    """year -> [(council name, amount text)] from each guidance page's table."""
    out = {}
    for year in EFS_YEARS:
        try:
            page = _get(client, EFS_PAGE.format(year=year)).text
        except httpx.HTTPError as exc:
            print(f"  {year}: {exc}")
            continue
        rows = []
        for tr in re.findall(r"<tr>(.*?)</tr>", page, re.S):
            cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S)]
            if len(cells) >= 2 and cells[0] and cells[0].lower() not in ("local authority", "council", "authority") and "£" in " ".join(cells[1:]) and not re.match(r"^\d{4}-\d{2}$", cells[0]):
                text = " ".join(c for c in cells[1:] if c)
                amount = re.search(r"£\s*[\d.,]+\s*(?:bn|m|k)?", text)
                rows.append((cells[0], amount.group(0).replace(" ", "") if amount else text[:40], "revised" in text.lower()))
        out[year] = rows
        print(f"  {year}: {len(rows)} councils")
    return out


def _norm(name: str) -> str:
    n = name.lower().replace("&", "and")
    n = re.sub(r"\b(royal borough of|london borough of|city of|borough of|council|city council|borough council|county council|district council|metropolitan|the|ua|cc|mbc|bc|dc|lb)\b", " ", n)
    n = re.sub(r"[^a-z ]", " ", n)
    return " ".join(n.split())


def main() -> int:
    with httpx.Client() as client:
        councils = band_d_history(client)
        print(f"{len(councils)} authorities with a Band D history")
        efs = efs_lists(client)
    by_name = {}
    for code, c in councils.items():
        by_name.setdefault(_norm(c["name"]), code)
    unmatched = []
    efs_by_name = {}
    for year, rows in efs.items():
        for name, amount, revised in rows:
            efs_by_name.setdefault(_norm(name), []).append({"year": year, "amount": amount, "revised": revised, "name": name})
            code = by_name.get(_norm(name))
            if code is None:
                # "Windsor and Maidenhead" vs "Windsor & Maidenhead": try a looser contains match.
                key = _norm(name)
                cands = [k for k in by_name if k == key or key in k or k in key]
                code = by_name[cands[0]] if len(cands) == 1 else None
            if code is None:
                unmatched.append((year, name))
                continue
            councils[code].setdefault("efs", []).append({"year": year, "amount": amount, "revised": revised})
    for code, notices in S114.items():
        if code in councils:
            councils[code]["s114"] = [{"date": d, "note": n} for d, n in notices]
    if unmatched:
        print("EFS councils not matched to a Band D row:", unmatched)
    latest = max(y for c in councils.values() for y in c["band_d"])
    national = [c["rises"][latest] for c in councils.values() if c.get("current") and latest in c["rises"] and c["class"] not in ("TE",)]
    national.sort()
    payload = {
        "as_of": datetime.date.today().isoformat(),
        "latest_year": latest,
        "efs_years": list(efs.keys()),
        "s114_as_of": S114_AS_OF,
        "median_rise_latest": national[len(national) // 2] if national else None,
        "councils": councils,
        # Counties and other bodies that are not billing authorities, matched by name at query time.
        "efs_by_name": efs_by_name,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, ensure_ascii=False)
    with_efs = sum(1 for c in councils.values() if c.get("efs"))
    print(f"written: {len(councils)} councils, {with_efs} with EFS, latest year {latest}, median rise {payload['median_rise_latest']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
