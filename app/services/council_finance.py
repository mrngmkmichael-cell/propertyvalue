"""A council's finances as a resident feels them: the Band D bill over
the years, Exceptional Financial Support from government, and section 114
notices. Read from app/data/council_finance.json, which
scripts/import_council_finance.py builds from MHCLG's live council tax
tables and its yearly exceptional financial support lists (both official,
Open Government Licence) and the councils' own section 114 notices.

Exceptional Financial Support is permission to borrow or to spend capital
receipts on day-to-day services, granted where a council could not
otherwise set a balanced budget, usually on condition of an external
review. A section 114 notice is the chief finance officer's declaration
that the council cannot balance its budget, which freezes new spending.
Neither changes a home; both tend to mean service cuts and the largest
council tax rises the rules allow.
"""
import datetime
import json
import pathlib
import re

_PATH = pathlib.Path(__file__).resolve().parents[1] / "data" / "council_finance.json"
_DATA: dict | None = None
YEARS_SHOWN = 6
S114_RECENT_YEARS = 3


def _load() -> dict:
    global _DATA
    if _DATA is None:
        try:
            _DATA = json.loads(_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _DATA = {}
    return _DATA


def _norm(name: str) -> str:
    n = (name or "").lower().replace("&", "and")
    n = re.sub(r"\b(royal borough of|london borough of|city of|borough of|council|city council|borough council|county council|district council|metropolitan|the|ua|cc|mbc|bc|dc|lb)\b", " ", n)
    n = re.sub(r"[^a-z ]", " ", n)
    return " ".join(n.split())


def _efs_year_matches(efs_year: str, latest: str) -> bool:
    """"2026-27" against the table's "2026-2027"."""
    return efs_year[:4] == latest[:4]


def for_council(district_code: str, district_name: str = "", county_name: str = "") -> dict | None:
    data = _load()
    councils = data.get("councils") or {}
    c = councils.get(district_code or "")
    if c is None and district_name:
        key = _norm(district_name)
        c = next((v for v in councils.values() if _norm(v["name"]) == key), None)
    if c is None:
        return None
    latest = data.get("latest_year") or max(c["band_d"])
    years = sorted(c["band_d"])[-YEARS_SHOWN:]
    history = [{"year": y, "label": y[:4] + "-" + y[-2:], "band_d": c["band_d"][y], "rise": c["rises"].get(y)} for y in years]
    efs = list(c.get("efs") or [])
    county_efs = list((data.get("efs_by_name") or {}).get(_norm(county_name), [])) if county_name and _norm(county_name) != _norm(c["name"]) else []
    s114 = list(c.get("s114") or [])
    as_of = data.get("as_of") or datetime.date.today().isoformat()
    cutoff = str(int(as_of[:4]) - S114_RECENT_YEARS) + as_of[4:]
    efs_current = any(_efs_year_matches(e["year"], latest) for e in efs)
    county_efs_current = any(_efs_year_matches(e["year"], latest) for e in county_efs)
    s114_recent = any(n["date"] >= cutoff for n in s114)
    return {
        "name": c["name"],
        "code": district_code,
        "history": history,
        "latest_year": latest,
        "latest_label": latest[:4] + "-" + latest[-2:],
        "rise_latest": c["rises"].get(latest),
        "median_rise_latest": data.get("median_rise_latest"),
        "efs": efs,
        "efs_current": efs_current,
        "county_name": county_name if county_efs else "",
        "county_efs": county_efs,
        "county_efs_current": county_efs_current,
        "s114": s114,
        "s114_recent": s114_recent,
        "s114_as_of": data.get("s114_as_of"),
        "flag": efs_current or county_efs_current or s114_recent,
        "as_of": as_of,
    }
