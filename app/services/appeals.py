"""School admission appeals, by council.

The Department for Education publishes, each August, how many parents
appealed a refused school place, how many appeals a panel heard and how
many it allowed. It publishes them by local authority, by region and
nationally, and not school by school: appeals against community schools
reach it as council totals, and the school-level returns from academies
are not released. So every figure here belongs to a council, and every
page that shows one says so.

app/data/admission_appeals.json is written by
scripts/import_admission_appeals.py; see that file for the source and
the once-a-year refresh.
"""
import functools
import json
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "admission_appeals.json"

SECONDARY = "Secondary"
PRIMARY = "Primary"
INFANT = "Primary (infant classes)"
PHASES = (SECONDARY, PRIMARY, INFANT)
# Below this many appeals actually heard, a percentage is one family's
# luck rather than a council's record, and the page says so instead of
# ranking it.
READABLE_MIN_HEARD = 30


@functools.lru_cache(maxsize=1)
def _data() -> dict:
    try:
        return json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def latest_year() -> str | None:
    return _data().get("latest_year")


def source() -> dict:
    d = _data()
    return {"release": d.get("release", ""), "url": d.get("source", ""),
            "year": d.get("latest_year", ""), "years": d.get("years", [])}


def england(year: str | None = None) -> dict:
    d = _data()
    return (d.get("england") or {}).get(year or d.get("latest_year", ""), {})


def _phase_rows(block: dict) -> list[dict]:
    """One row per phase, newest figures, in the order a parent reads
    them: secondary first, because that is where appeals happen."""
    rows = []
    for phase in PHASES:
        figures = block.get(phase)
        if not figures or not figures.get("lodged"):
            continue
        rows.append({"phase": phase, **figures,
                     "readable": bool(figures.get("heard") and figures["heard"] >= READABLE_MIN_HEARD)})
    return rows


def for_council(name: str) -> dict | None:
    """What a council's parents did last year, with the five-year run and
    England beside it. None when the council is not in the release, which
    happens for a council that was abolished or created since."""
    d = _data()
    block = (d.get("councils") or {}).get(name)
    if not block:
        return None
    year = d["latest_year"]
    latest = block.get(year) or {}
    if not latest:
        return None
    history = []
    for y in d["years"]:
        figures = (block.get(y) or {}).get(SECONDARY)
        if figures and figures.get("heard"):
            history.append({"year": y, **figures})
    return {
        "council": name,
        "year": year,
        "phases": _phase_rows(latest),
        "secondary": latest.get(SECONDARY),
        "history": history,
        "england": england(year).get(SECONDARY) or {},
        "source": source(),
    }


def league(phase: str = SECONDARY) -> list[dict]:
    """Every council with appeals heard last year, the ones a rate can be
    read from first, each with the counts it rests on."""
    d = _data()
    year = d.get("latest_year")
    if not year:
        return []
    rows = []
    for council, years in (d.get("councils") or {}).items():
        figures = (years.get(year) or {}).get(phase)
        if not figures or not figures.get("heard"):
            continue
        rows.append({"council": council, **figures,
                     "readable": figures["heard"] >= READABLE_MIN_HEARD})
    rows.sort(key=lambda r: (not r["readable"], -(r["won_pct"] or 0), -r["heard"]))
    return rows


def summary() -> dict:
    """The figures the national page leads with."""
    d = _data()
    year = d.get("latest_year")
    if not year:
        return {}
    rows = league()
    readable = [r for r in rows if r["readable"]]
    nation = england(year).get(SECONDARY) or {}
    best = max(readable, key=lambda r: r["won_pct"], default=None)
    worst = min(readable, key=lambda r: r["won_pct"], default=None)
    return {
        "year": year,
        "councils": len(rows),
        "england": nation,
        "england_primary": england(year).get(PRIMARY) or {},
        "england_infant": england(year).get(INFANT) or {},
        "best": best,
        "worst": worst,
        "heard_total": sum(r["heard"] for r in rows),
        "won_total": sum(r["won"] or 0 for r in rows),
        "source": source(),
    }
