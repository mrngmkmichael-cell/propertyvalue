"""Import GP practice pressure and A&E four-hour performance.

Sources, all Open Government Licence:
- NHS England Digital, "Patients Registered at a GP Practice", monthly:
  gp-reg-pat-prac-all.csv (practice code, postcode, patients on the list
  on the first of the month) and gp-reg-pat-prac-map.csv (name, primary
  care network, integrated care board).
- NHS England Digital, "General Practice Workforce", monthly, the
  practice-level detailed CSV: TOTAL_GP_FTE (every GP, trainees and locums
  included) and TOTAL_GP_EXTG_FTE (fully qualified, trainees excluded, the
  national headline measure), with GP_SOURCE saying whether the practice
  reported or the figure is estimated.
- NHS England, "A&E Attendances and Emergency Admissions", monthly, the
  provider CSV (attendances and four-hour breaches by department type) and
  its ICB mapping spreadsheet (which integrated care board each provider
  belongs to).
- postcodes.io for each practice's coordinates.

Kept: app.models.GpPractice (one row per practice with a list size) and
app.models.AeTrust (one row per provider with Type 1, that is consultant-led
24-hour, A&E attendances), plus app/data/health_context.json with the
dates and the national figures the page compares against.

    python scripts/import_health_services.py            # finds the latest files
    python scripts/import_health_services.py --gp-all URL --gp-map URL --gpw URL --ae URL --ae-map URL

Re-runnable: replaces both tables. Refresh monthly; the publications land
in the second half of each month. Needs xlrd (pip install xlrd) for the
mapping spreadsheet, which NHS England still publishes as .xls.
"""
import csv
import datetime
import io
import json
import os
import re
import statistics
import sys
import time
import zipfile

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from app import db  # noqa: E402
from app.models import AeTrust, GpPractice  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXT_PATH = os.path.join(ROOT, "app", "data", "health_context.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) UKPropertyInsight-import/1.0"}
GP_INDEX = "https://digital.nhs.uk/data-and-information/publications/statistical/patients-registered-at-a-gp-practice"
GPW_INDEX = "https://digital.nhs.uk/data-and-information/publications/statistical/general-and-personal-medical-services"
AE_INDEX = "https://www.england.nhs.uk/statistics/statistical-work-areas/ae-waiting-times-and-activity/"
MONTHS = {m: i for i, m in enumerate(("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"), 1)}


def _get(client: httpx.Client, url: str) -> httpx.Response:
    r = client.get(url, headers=UA, timeout=120, follow_redirects=True)
    r.raise_for_status()
    return r


def discover(client: httpx.Client, args: dict) -> dict:
    """The latest file URLs, from the publication index pages, unless
    given on the command line."""
    urls = dict(args)
    if not (urls.get("gp_all") and urls.get("gp_map")):
        index = _get(client, GP_INDEX).text
        m = re.search(r'href="(/data-and-information/publications/statistical/patients-registered-at-a-gp-practice/[a-z]+-\d{4})"', index)
        page = _get(client, "https://digital.nhs.uk" + m.group(1)).text
        urls.setdefault("gp_all", re.search(r'(https://files\.digital\.nhs\.uk/[^"]+/gp-reg-pat-prac-all\.zip)', page).group(1))
        urls.setdefault("gp_map", re.search(r'(https://files\.digital\.nhs\.uk/[^"]+/gp-reg-pat-prac-map\.zip)', page).group(1))
    if not urls.get("gpw"):
        index = _get(client, GPW_INDEX).text
        m = re.search(r'href="(/data-and-information/publications/statistical/general-and-personal-medical-services/\d{1,2}-[a-z]+-\d{4})"', index)
        page = _get(client, "https://digital.nhs.uk" + m.group(1)).text
        urls["gpw"] = re.search(r'(https://files\.digital\.nhs\.uk/[^"]+/GPWPracticeCSV[^"]*\.zip)', page).group(1)
    if not (urls.get("ae") and urls.get("ae_map")):
        index = _get(client, AE_INDEX).text
        years = re.findall(r'href="(https://www\.england\.nhs\.uk/statistics/statistical-work-areas/ae-waiting-times-and-activity/ae-attendances-and-emergency-admissions-(\d{4})-\d{2}/)"', index)
        year_url = max(years, key=lambda y: y[1])[0]
        page = _get(client, year_url).text
        monthly = re.findall(r'(https://www\.england\.nhs\.uk/statistics/wp-content/uploads/[^"]+/([A-Z][a-z]+)-(\d{4})-CSV-[A-Za-z0-9]+\.csv)', page)
        latest = max(monthly, key=lambda x: (int(x[2]), MONTHS.get(x[1], 0)))
        urls.setdefault("ae", latest[0])
        urls.setdefault("ae_map", re.search(r'(https://www\.england\.nhs\.uk/statistics/wp-content/uploads/[^"]+/System-Mapping[^"]*\.xls)', page).group(1))
    return urls


def _csv_from_zip(content: bytes, pick: str) -> list[dict]:
    z = zipfile.ZipFile(io.BytesIO(content))
    name = next(n for n in z.namelist() if pick.lower() in n.lower())
    with z.open(name) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig", newline="")))


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def geocode(client: httpx.Client, postcodes: list[str]) -> dict[str, tuple[float, float]]:
    out = {}
    todo = sorted({p for p in postcodes if p})
    for i in range(0, len(todo), 100):
        batch = todo[i:i + 100]
        r = client.post("https://api.postcodes.io/postcodes", json={"postcodes": batch}, timeout=60)
        r.raise_for_status()
        for item in r.json().get("result", []):
            res = item.get("result")
            if res and res.get("latitude") is not None:
                out[item["query"]] = (res["latitude"], res["longitude"])
        time.sleep(0.2)
    return out


def read_mapping(content: bytes) -> dict[str, tuple[str, str]]:
    import xlrd  # script-time dependency only

    book = xlrd.open_workbook(file_contents=content)
    for sheet in book.sheets():
        header_row = None
        for r in range(min(sheet.nrows, 20)):
            cells = [str(sheet.cell_value(r, c)).strip().lower() for c in range(sheet.ncols)]
            if any(c in ("code", "org code", "organisation code") for c in cells) and any("icb" in c and "code" in c for c in cells):
                header_row = r
                break
        if header_row is None:
            continue
        cells = [str(sheet.cell_value(header_row, c)).strip().lower() for c in range(sheet.ncols)]
        i_org = next(i for i, c in enumerate(cells) if c in ("code", "org code", "organisation code"))
        i_icb = next(i for i, c in enumerate(cells) if "icb" in c and "code" in c)
        i_icb_name = next((i for i, c in enumerate(cells) if "icb" in c and "name" in c), None)
        out = {}
        for r in range(header_row + 1, sheet.nrows):
            org = str(sheet.cell_value(r, i_org)).strip()
            if org:
                out[org] = (str(sheet.cell_value(r, i_icb)).strip(), str(sheet.cell_value(r, i_icb_name)).strip() if i_icb_name is not None else "")
        return out
    raise SystemExit("could not find the org code / ICB columns in the mapping spreadsheet")


def main(argv: list[str]) -> int:
    args = {}
    for flag in ("gp_all", "gp_map", "gpw", "ae", "ae_map"):
        opt = "--" + flag.replace("_", "-")
        if opt in argv:
            args[flag] = argv[argv.index(opt) + 1]
    with httpx.Client() as client:
        urls = discover(client, args)
        for k, v in urls.items():
            print(f"{k}: {v}")
        gp_all = _csv_from_zip(_get(client, urls["gp_all"]).content, "gp-reg-pat-prac-all")
        gp_map = _csv_from_zip(_get(client, urls["gp_map"]).content, "gp-reg-pat-prac-map")
        gpw = _csv_from_zip(_get(client, urls["gpw"]).content, "Detailed")
        ae_rows = list(csv.DictReader(io.StringIO(_get(client, urls["ae"]).text)))
        mapping = read_mapping(_get(client, urls["ae_map"]).content)

        practices = {}
        for r in gp_all:
            if r.get("TYPE", "GP") != "GP" or r.get("SEX", "ALL") != "ALL" or r.get("AGE", "ALL") != "ALL":
                continue
            practices[r["CODE"]] = {"code": r["CODE"], "postcode": (r.get("POSTCODE") or "").strip().upper(), "patients": _int(r.get("NUMBER_OF_PATIENTS"))}
        patients_date = datetime.date.fromisoformat(gp_all[0]["EXTRACT_DATE"]) if gp_all else None
        for r in gp_map:
            p = practices.get(r["PRACTICE_CODE"])
            if p:
                p.update(name=(r.get("PRACTICE_NAME") or "").strip().title()[:120], icb_code=(r.get("ICB_CODE") or "").strip(),
                         icb_name=(r.get("ICB_NAME") or "").strip()[:120], pcn_name=(r.get("PCN_NAME") or "").strip().title()[:120])
        workforce_date = None
        m = re.search(r"(\d{2})(\d{4})", os.path.basename(urls["gpw"]))
        if m:
            workforce_date = datetime.date(int(m.group(2)), int(m.group(1)), 1)
        for r in gpw:
            p = practices.get(r["PRAC_CODE"])
            if p:
                p.update(gp_fte=_float(r.get("TOTAL_GP_FTE")), qualified_gp_fte=_float(r.get("TOTAL_GP_EXTG_FTE")), gp_source=(r.get("GP_SOURCE") or "")[:60])
        coords = geocode(client, [p["postcode"] for p in practices.values()])
        for p in practices.values():
            lat_lon = coords.get(p["postcode"])
            p["latitude"], p["longitude"] = (lat_lon if lat_lon else (None, None))
            p.setdefault("name", ""); p.setdefault("icb_code", ""); p.setdefault("icb_name", ""); p.setdefault("pcn_name", "")
            p.setdefault("gp_fte", None); p.setdefault("qualified_gp_fte", None); p.setdefault("gp_source", "")
            p["patients_date"] = patients_date
            p["workforce_date"] = workforce_date
        located = sum(1 for p in practices.values() if p["latitude"] is not None)
        print(f"{len(practices):,} practices, {located:,} located, patients as at {patients_date}, workforce {workforce_date}")

        period = ""
        trusts = []
        for r in ae_rows:
            code = (r.get("Org Code") or "").strip()
            t1 = _int(r.get("A&E attendances Type 1"))
            if not code or t1 <= 0:
                continue
            period = period or (r.get("Period") or "")
            icb_code, icb_name = mapping.get(code, ("", ""))
            all_att = sum(_int(r.get(k)) for k in r if k and k.startswith("A&E attendances ") and "Booked" not in k)
            all_over = sum(_int(r.get(k)) for k in r if k and k.startswith("Attendances over 4hrs "))
            trusts.append({
                "org_code": code, "name": (r.get("Org name") or "").strip().title()[:160], "icb_code": icb_code, "icb_name": icb_name[:120],
                "period": period, "type1_attendances": t1, "type1_over_4h": _int(r.get("Attendances over 4hrs Type 1")),
                "all_attendances": all_att, "all_over_4h": all_over,
            })
        unmapped = sum(1 for t in trusts if not t["icb_code"])
        print(f"{len(trusts)} providers with Type 1 A&E attendances in {period}, {unmapped} without an ICB in the mapping")

    ratios = [p["patients"] / p["qualified_gp_fte"] for p in practices.values()
              if p.get("qualified_gp_fte") and p["qualified_gp_fte"] >= 0.5 and p.get("gp_source", "").startswith("Fully provided") and p["patients"] > 0]
    t1_att = sum(t["type1_attendances"] for t in trusts)
    t1_over = sum(t["type1_over_4h"] for t in trusts)
    context = {
        "patients_date": patients_date.isoformat() if patients_date else None,
        "workforce_date": workforce_date.isoformat() if workforce_date else None,
        "ae_period": period,
        "practices": len(practices),
        "median_patients_per_qualified_gp": round(statistics.median(ratios)) if ratios else None,
        "national_type1_within_4h_pct": round(100 * (1 - t1_over / t1_att), 1) if t1_att else None,
        "imported": datetime.date.today().isoformat(),
    }
    with open(CONTEXT_PATH, "w", encoding="utf-8") as f:
        json.dump(context, f, indent=1)
    print("context:", context)

    db.init_db()
    with db.get_session() as session:
        session.query(GpPractice).delete()
        session.query(AeTrust).delete()
        session.commit()
        rows = list(practices.values())
        for i in range(0, len(rows), 1000):
            session.execute(GpPractice.__table__.insert(), rows[i:i + 1000])
            session.commit()
        if trusts:
            session.execute(AeTrust.__table__.insert(), trusts)
            session.commit()
    print(f"{len(rows):,} practices and {len(trusts)} A&E providers written")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
