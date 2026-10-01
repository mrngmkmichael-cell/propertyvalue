"""School admission appeals by council, from the Department for Education.

Source: "Admission appeals in England", Explore Education Statistics,
https://explore-education-statistics.service.gov.uk/find-statistics/admission-appeals-in-england
Published once a year, each August, for appeals heard by 1 September of
the previous year. Reporting year 2025 covers appeals decided by
1 September 2024, against places for September 2024 entry.

Refresh cadence: once a year, after the August release. Re-runnable: it
rewrites app/data/admission_appeals.json from the file it downloads.

    .venv/Scripts/python.exe scripts/import_admission_appeals.py
    .venv/Scripts/python.exe scripts/import_admission_appeals.py <local.zip>

What the figures are, and are not. The DfE publishes appeals by local
authority, by region and nationally. It does NOT publish them school by
school: appeals against community and voluntary controlled schools reach
it as council totals, and the school-level figures academies and
voluntary aided schools return are not released. So every number here
belongs to a council and the site must say so. Three phases are kept:
Primary, Secondary, and "Primary (infant classes)", which is a separate
legal test (an infant class appeal can only succeed on narrow grounds,
which is why its success rate is a fraction of the others) and is worth
showing for exactly that reason.
"""
import csv
import io
import json
import pathlib
import sys
import zipfile

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "data" / "admission_appeals.json"
# The "Download all data" file of the reporting year 2025 release. Find
# the next one on the release page: the link is on "Download all data
# (ZIP)" and carries the release's own id.
ZIP_URL = ("https://content.explore-education-statistics.service.gov.uk/api/releases/"
           "f3b24411-e24d-4c21-a5c0-08ddb9419082/files?fromPage=ReleaseDownloads")
RELEASE = "Admission appeals in England, reporting year 2025"
KEEP_YEARS = 5            # five years is a trend a reader can hold; ten is a spreadsheet
PHASES = ("Secondary", "Primary", "Primary (infant classes)")


def fetch(path: str | None) -> bytes:
    if path:
        return pathlib.Path(path).read_bytes()
    with httpx.Client(timeout=120, follow_redirects=True,
                      headers={"User-Agent": "UKPropertyInsight import (support@ukpropertyinsight.co.uk)"}) as client:
        response = client.get(ZIP_URL)
        response.raise_for_status()
        return response.content


def number(value: str) -> int | None:
    value = (value or "").replace(",", "").strip()
    if value in ("", "z", "x", "c", ":"):   # the DfE's suppression markers
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def rate(heard: int | None, won: int | None) -> float | None:
    """Of the appeals a panel actually heard, the share it allowed. The
    file carries its own percentage; this recomputes it so a rounded
    column can never disagree with the two counts printed beside it."""
    if not heard or won is None:
        return None
    return round(100 * won / heard, 1)


def main() -> int:
    raw = fetch(sys.argv[1] if len(sys.argv) > 1 else None)
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        name = next(n for n in bundle.namelist() if n.endswith("AdmissionsAppeals_LA.csv"))
        rows = list(csv.DictReader(io.StringIO(bundle.read(name).decode("utf-8-sig"))))
    years = sorted({int(r["time_period"]) for r in rows})[-KEEP_YEARS:]
    councils: dict[str, dict] = {}
    england: dict[str, dict] = {}
    for row in rows:
        year = int(row["time_period"])
        phase = row["school_phase"]
        if year not in years or phase not in PHASES:
            continue
        heard, won = number(row["appeals_heard_number"]), number(row["successful_appeals_number"])
        figures = {
            "admissions": number(row["admissions"]),
            "lodged": number(row["appeals_lodged_number"]),
            "heard": heard,
            "won": won,
            "won_pct": rate(heard, won),
        }
        if row["geographic_level"] == "National":
            england.setdefault(str(year), {})[phase] = figures
        elif row["geographic_level"] == "Local authority":
            councils.setdefault(row["la_name"], {}).setdefault(str(year), {})[phase] = figures

    if not councils or not england:
        print("the file held no council or national rows; has the release changed shape?")
        return 1
    OUT.write_text(json.dumps({
        "release": RELEASE,
        "source": "https://explore-education-statistics.service.gov.uk/find-statistics/admission-appeals-in-england",
        "appeals_decided_by": "1 September",
        "latest_year": str(years[-1]),
        "years": [str(y) for y in years],
        "england": england,
        "councils": councils,
    }, indent=1, sort_keys=True), encoding="utf-8")
    size = OUT.stat().st_size
    print(f"{len(councils)} councils, {len(years)} years ({years[0]} to {years[-1]}), "
          f"{size / 1024:.0f} KB written to {OUT.relative_to(ROOT)}")
    worst = min(councils.items(), key=lambda kv: (kv[1].get(str(years[-1]), {})
                                                  .get("Secondary", {}).get("won_pct") or 999))
    best = max(councils.items(), key=lambda kv: (kv[1].get(str(years[-1]), {})
                                                 .get("Secondary", {}).get("won_pct") or -1))
    print(f"  secondary appeals allowed, {years[-1]}: {best[0]} "
          f"{best[1][str(years[-1])]['Secondary']['won_pct']}%, {worst[0]} "
          f"{worst[1][str(years[-1])]['Secondary']['won_pct']}%, England "
          f"{england[str(years[-1])]['Secondary']['won_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
