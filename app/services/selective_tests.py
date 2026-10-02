"""Which entrance test each selective area uses, and when its round runs.

Read from app/data/selective_tests.json, which was compiled one council
at a time from each council's, consortium's or school's own page. No
database and no network.

The point of the file, and of this module, is that the thirty-five
selective areas do not work the same way and the honest version of this
feature has to show that rather than flatten it. Four areas share one
West Midlands test on three different days. Essex and Southend share a
consortium. Reading's two schools test ten weeks apart. In five areas
there is no council test at all and each school runs its own, so a
family applying to two sits two.

Where a source does not name the test provider, the paper content, a
registration window or a results date, the entry says so and this
module passes that through untouched. Nothing here is inferred from a
neighbouring area.
"""
import datetime as dt
import json
import logging
import os
import threading

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "selective_tests.json")

_lock = threading.Lock()
_loaded = False
_data: dict = {}
_by_name: dict = {}


def _normal(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _load() -> bool:
    global _loaded, _data, _by_name
    with _lock:
        if _loaded:
            return bool(_data)
        _loaded = True
        try:
            with open(DATA, encoding="utf-8") as handle:
                _data = json.load(handle)
            _by_name = {_normal(area["council"]): area for area in _data["areas"]}
        except Exception as problem:  # noqa: BLE001 - a page must not care
            logging.warning("selective tests file unavailable: %s", problem)
            _data = {}
        return bool(_data)


def source() -> dict | None:
    if not _load():
        return None
    return {"entry_year": _data["entry_year"], "areas": _data["areas_covered"],
            "schools": _data["schools_covered"], "national": _data["national_dates"]}


def _day(value: str) -> str:
    """"2026-09-10, for pupils at a Kent primary school" as "10 September
    2026, for pupils at a Kent primary school". The date is first in
    every string so that the file sorts by it."""
    if not value:
        return ""
    head, _, tail = value.partition(",")
    head = head.strip()
    try:
        when = dt.date.fromisoformat(head)
        said = f"{when.day} {when.strftime('%B')} {when.year}"
    except ValueError:
        # A council that named the month and not the day, as Birmingham
        # did, is written as "2026-09" and should read as September 2026.
        try:
            month = dt.date.fromisoformat(head + "-01")
            said = f"{month.strftime('%B')} {month.year}"
        except ValueError:
            return value
    return f"{said},{tail}" if tail else said


def _first_date(area: dict) -> str:
    for entry in area.get("test_dates") or []:
        head = entry.split(",")[0].strip()
        try:
            dt.date.fromisoformat(head)
            return head
        except ValueError:
            continue
    return ""


def _readable(area: dict) -> dict:
    """One area with its dates written the way the site writes dates,
    and nothing added that the sources did not say."""
    out = dict(area)
    out["test_dates_said"] = [_day(entry) for entry in area.get("test_dates") or []]
    out["first_test_date"] = _first_date(area)
    # Birmingham says "September 2026" without naming the day, so the
    # table shows that rather than "not published", which would be a
    # harsher thing than the council actually said.
    if out["first_test_date"]:
        out["first_test_said"] = _day(out["first_test_date"]).split(",")[0]
    elif out["test_dates_said"]:
        out["first_test_said"] = out["test_dates_said"][0].split(",")[0]
    else:
        out["first_test_said"] = ""
    out["registration_closed_said"] = _day(area.get("registration_closed", ""))
    out["registration_opened_said"] = _day(area.get("registration_opened", ""))
    out["results_said"] = _day(area.get("results_date", ""))
    out["provider_said"] = area.get("provider") or ""
    out["slug"] = _normal(area["council"])
    out["one_test"] = not area.get("test_name", "").startswith("Each school")
    return out


def all_areas() -> list[dict]:
    """Every selective area, alphabetically, as the page lists them."""
    if not _load():
        return []
    return [_readable(area) for area in _data["areas"]]


def for_council(name: str | None) -> dict | None:
    """One area by council name, for the council hub and school pages."""
    if not _load() or not name:
        return None
    area = _by_name.get(_normal(name))
    return _readable(area) if area else None


def summary() -> dict | None:
    """What the page can say about the set without counting by hand."""
    areas = all_areas()
    if not areas:
        return None
    return {
        "areas": len(areas),
        "schools": _data["schools_covered"],
        "entry_year": _data["entry_year"],
        "with_provider": sum(1 for area in areas if area["provider"]),
        "without_provider": sum(1 for area in areas if not area["provider"]),
        "with_results_date": sum(1 for area in areas if area["results_date"]),
        "own_test_per_school": sum(1 for area in areas if not area["one_test"]),
        "providers": sorted({area["provider"] for area in areas if area["provider"]}),
        "national": _data["national_dates"],
        "read_on": max(area["read_on"] for area in areas),
    }
