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
import calendar
import datetime
import json
import pathlib
import re

from app.services import council_tax

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


def _band_d_from_council_tax(history: list[dict], code: str) -> list[dict]:
    """The latest year's Band D as app/data/council_tax.json gives it,
    where that file covers the same council and the same year.

    18 Sep 2026, first-visitor audit item D7: this file holds MHCLG's live
    table to the whole pound and council_tax.json holds the same year's
    Band D to the penny, so the LS6 guide gave Leeds's Band D as £2,284 in
    its lead and council section and £2,283 in House prices. One figure,
    one source: the council tax file, which the report's band table, the
    running-costs page and the council pages already read. Earlier years
    and the published rises are this file's, as before. A new list is
    returned; the one passed in may belong to a cached payload."""
    if not history or not code:
        return history
    ct = council_tax.for_district(code)
    latest = history[-1]
    if (not ct or ct.get("nation") != "England" or not ct.get("band_d")
            or str(ct.get("year") or "")[:4] != str(latest.get("year") or "")[:4]):
        return history
    return history[:-1] + [dict(latest, band_d=ct["band_d"])]


def with_council_tax_band_d(finance: dict | None) -> dict | None:
    """for_council's answer with its latest Band D from the council tax
    file, for an answer cached before 18 Sep 2026 (the area guides keep
    theirs in a payload for a week)."""
    if not finance or not finance.get("history"):
        return finance
    history = _band_d_from_council_tax(finance["history"], finance.get("code") or "")
    return finance if history is finance["history"] else dict(finance, history=history)


def for_council(district_code: str, district_name: str = "", county_name: str = "") -> dict | None:
    data = _load()
    councils = data.get("councils") or {}
    c = councils.get(district_code or "")
    code = district_code if c is not None else ""
    if c is None and district_name:
        key = _norm(district_name)
        code, c = next(((k, v) for k, v in councils.items() if _norm(v["name"]) == key), ("", None))
    if c is None:
        return None
    latest = data.get("latest_year") or max(c["band_d"])
    years = sorted(c["band_d"])[-YEARS_SHOWN:]
    history = [{"year": y, "label": y[:4] + "-" + y[-2:], "band_d": c["band_d"][y], "rise": c["rises"].get(y)} for y in years]
    history = _band_d_from_council_tax(history, code)
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


def _month_year(iso_date: str) -> str:
    """"2023-09-22" as "September 2023"."""
    try:
        return f"{calendar.month_name[int(iso_date[5:7])]} {int(iso_date[:4])}"
    except (ValueError, IndexError, TypeError):
        return iso_date


def flag_sentence(finance: dict | None) -> str | None:
    """What the flag is, in plain words, for the report's list of things
    worth checking and its verdict. None when there is no flag.

    18 Sep 2026, first-visitor audit item D3: the chip read "Council under
    exceptional financial support or a section 114 notice", the two
    possible reasons joined by "or" and neither one explained, so a reader
    in York could not tell which applied or what either meant. It now
    names the council, what happened and the year, taking this year's
    exceptional support first (it is current), then a county council's,
    then a recent section 114 notice, the order for_council's flag reads.
    The council's name is the dataset's, "York UA" shortened to "York",
    and a name that already says it, "Dorset Council", is not followed by
    a second "council".
    """
    if not finance or not finance.get("flag"):
        return None
    name = re.sub(r"\s+UA$", "", finance.get("name") or "").strip() or "The"
    council = name if name.lower().endswith("council") else f"{name} council"
    latest = finance.get("latest_year") or ""
    if finance.get("efs_current"):
        year = next((e["year"] for e in finance.get("efs") or [] if _efs_year_matches(e["year"], latest)), finance.get("latest_label"))
        return f"{council} needed exceptional government support for {year}"
    if finance.get("county_efs_current"):
        year = next((e["year"] for e in finance.get("county_efs") or [] if _efs_year_matches(e["year"], latest)), finance.get("latest_label"))
        return f"{finance.get('county_name')} County Council needed exceptional government support for {year}"
    notices = sorted(n["date"] for n in finance.get("s114") or [] if n.get("date"))
    if notices:
        return (f"{council} issued a section 114 notice in {_month_year(notices[-1])}, "
                "saying it could not balance its budget")
    return None
