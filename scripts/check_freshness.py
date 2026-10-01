"""Is what we hold still the newest the publisher has published?

check_sources.py asks whether a source answers when the report calls
it. This asks the other question, the one that has no symptom: whether
a dataset we imported months ago is still current. A pinned URL does
not fail, it just quietly serves last spring.

On 1 October 2026 two imports were found to have stopped following
their sources. The Ofsted import was pinned to the 30 June file, so
inspection outcomes were two months behind. The rent import was pinned
to the July edition of the ONS index, whose newest month was June.
Both looked perfectly healthy to every other check we run, because
every page rendered and every figure had a source. This script exists
so the next one is visible the week it happens.

Two kinds of check:

  exact   the publisher tells us its newest vintage cheaply, so we
          compare ours with theirs and the verdict is a fact.
  budget  the publisher does not, so we compare what we hold against
          today and allow the lag the series normally runs at. A
          budget breach is a prompt to go and look, not a diagnosis.

    .venv/Scripts/python.exe scripts/check_freshness.py

Exit code is 1 if anything is behind.
"""
import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv()

from app.db import get_session  # noqa: E402

UA = {"User-Agent": "UKPropertyInsight freshness check (support@ukpropertyinsight.co.uk)"}
TODAY = dt.date.today()
EES = "https://api.education.gov.uk/statistics/v1/data-sets/{}"

OFSTED_PAGE = ("https://www.gov.uk/government/statistical-data-sets/"
               "monthly-management-information-ofsteds-school-inspections-outcomes")
OFSTED_IMPORTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "import_schools.py")
AIR_IMPORTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "import_air_quality.py")
DEFRA_NO2 = "https://uk-air.defra.gov.uk/datastore/pcm/mapno2{}.csv"

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def month_number(name):
    """Ofsted writes the month as Jan, June, July, Sept or August, so the
    first three letters are the only part worth reading."""
    return MONTHS.get(name[:3].lower())


def scalar(sql):
    with get_session() as session:
        return session.execute(text(sql)).scalar()


def ees_latest(dataset_id):
    """The newest time period the DfE holds for one of its data sets."""
    reply = httpx.get(EES.format(dataset_id), headers=UA, timeout=60)
    reply.raise_for_status()
    return reply.json()["latestVersion"]["timePeriods"]["end"]


def ofsted_published():
    """The newest 'latest inspections as at' file Ofsted has published."""
    page = httpx.get(OFSTED_PAGE, headers=UA, timeout=90, follow_redirects=True)
    page.raise_for_status()
    dates = []
    for day, month, year in re.findall(r"latest_inspections_a[ts]_?(?:at_)?(\d{1,2})_([A-Za-z]+)_(\d{4})",
                                       page.text):
        number = month_number(month)
        if number:
            dates.append(dt.date(int(year), number, int(day)))
    if not dates:
        raise RuntimeError("no dated inspection files found on the Ofsted page")
    return max(dates)


def ofsted_pinned():
    """The file our importer is pinned to."""
    source = open(OFSTED_IMPORTER, encoding="utf-8").read()
    found = re.search(r"latest_inspections_a[ts]_?(?:at_)?(\d{1,2})_([A-Za-z]+)_(\d{4})", source)
    if not found:
        raise RuntimeError("no dated inspection file found in import_schools.py")
    day, month, year = found.groups()
    return dt.date(int(year), month_number(month), int(day))


def air_published():
    """The newest reference year DEFRA has mapped."""
    held = int(re.search(r"^YEAR\s*=\s*(\d{4})", open(AIR_IMPORTER, encoding="utf-8").read(),
                         re.M).group(1))
    year = held
    while year < TODAY.year:
        reply = httpx.get(DEFRA_NO2.format(year + 1), headers={**UA, "Range": "bytes=0-200"},
                          timeout=40, follow_redirects=True)
        if reply.status_code >= 400:
            break
        year += 1
    return year


def month_age(period):
    """Days from the first of a YYYY-MM period to today."""
    year, month = (int(part) for part in period.split("-"))
    return (TODAY - dt.date(year, month, 1)).days


def exact(name, held, published, source):
    behind = str(held) < str(published)
    return dict(name=name, held=str(held), publisher=str(published), source=source,
                verdict="BEHIND" if behind else "ok")


def budget(name, held, age_days, allowed, source):
    return dict(name=name, held=str(held), publisher=f"not stated, {age_days}d old (allow {allowed}d)",
                source=source, verdict="BEHIND" if age_days > allowed else "ok")


def checks():
    yield lambda: exact("Ofsted inspection outcomes", ofsted_pinned(), ofsted_published(),
                        "gov.uk monthly management information")
    yield lambda: exact("KS4 results", scalar("select max(academic_year) from ks4_results"),
                        ees_latest("19e39901-a96c-be76-b9c2-6af54ae076d2"), "DfE performance tables")
    yield lambda: exact("KS2 results", scalar("select max(academic_year) from ks2_results"),
                        ees_latest("019afee4-e5d0-72f9-9a8f-d7a1a56eac1d"), "DfE performance tables")
    yield lambda: exact("KS4 leaver destinations",
                        scalar("select max(academic_year) from school_destinations"),
                        ees_latest("019d4f41-22d1-71b2-a1a7-f3b91026815b"), "DfE destinations")
    yield lambda: exact("School census characteristics",
                        scalar("select max(academic_year) from school_characteristics"),
                        ees_latest("019e7403-4523-7749-b530-159f451dd83c"), "DfE school census")
    yield lambda: exact("Background air quality", scalar("select max(year) from air_quality"),
                        air_published(), "DEFRA pollution climate mapping")
    yield lambda: budget("Private rents", scalar("select max(period) from rental_price"),
                         month_age(scalar("select max(period) from rental_price")), 75,
                         "ONS Price Index of Private Rents")
    yield lambda: budget("Bus stop feed", scalar("select max(feed_date) from bus_stops"),
                         (TODAY - scalar("select max(feed_date) from bus_stops")).days, 150,
                         "Bus Open Data Service")
    yield lambda: budget("GP practice list sizes",
                         scalar("select max(patients_date) from gp_practices"),
                         (TODAY - scalar("select max(patients_date) from gp_practices")).days, 120,
                         "NHS Digital patients registered at a practice")


def main():
    rows = []
    for check in checks():
        try:
            rows.append(check())
        except Exception as problem:  # a publisher being down is not a staleness verdict
            rows.append(dict(name=getattr(check, "__name__", "check"), held="?",
                             publisher=f"could not ask: {str(problem)[:60]}", source="",
                             verdict="unknown"))
    width = max(len(row["name"]) for row in rows)
    print(f"Freshness as at {TODAY.isoformat()}")
    print("=" * (width + 58))
    print(f"{'dataset'.ljust(width)}  {'we hold'.ljust(10)}  {'publisher'.ljust(28)}  verdict")
    for row in rows:
        print(f"{row['name'].ljust(width)}  {row['held'].ljust(10)}  "
              f"{row['publisher'][:28].ljust(28)}  {row['verdict']}")
    behind = [row for row in rows if row["verdict"] == "BEHIND"]
    unknown = [row for row in rows if row["verdict"] == "unknown"]
    print()
    if behind:
        for row in behind:
            print(f"BEHIND: {row['name']} holds {row['held']}, {row['source']} has {row['publisher']}")
    else:
        print("nothing is behind its publisher")
    if unknown:
        print(f"{len(unknown)} check(s) could not reach their publisher, so they prove nothing")
    return 1 if behind else 0


if __name__ == "__main__":
    sys.exit(main())
