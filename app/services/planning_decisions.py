"""How this council decides planning applications.

Read from app/data/planning_decisions.json, which
scripts/import_planning_decisions.py writes from MHCLG's planning live
tables: one entry per English local planning authority, about 60 KB, so
no database and no network.

The card answers a question the rest of the report cannot: part of
whether a house can be changed is the authority rather than the house.
It is deliberately four plain figures and no verdict, because a council
granting 94% of applications is not "good" and one granting 78% is not
"bad": a tight conservation area and a growth borough are different
places, not better and worse ones.

England only. Wales, Scotland and Northern Ireland publish their own
planning statistics, so an address there is told the check does not
reach it.
"""
import json
import logging
import os
import threading

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "planning_decisions.json")
# Under this many percentage points apart, two shares read as the same
# thing. Said on the card, so a reader is never left to guess the rule.
SAME_WITHIN_PP = 2.0

_lock = threading.Lock()
_loaded = False
_data: dict = {}
_by_name: dict = {}


def _normal(name: str) -> str:
    """Council names are spelled differently by every publisher, so
    'Bristol, City of' and 'Bristol City of' are one key here."""
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
            _by_name = {_normal(entry["name"]): code
                        for code, entry in _data["authorities"].items()}
        except Exception as problem:  # noqa: BLE001 - the report must not care
            logging.warning("planning decisions file unavailable: %s", problem)
            _data = {}
        return bool(_data)


OTHER_NATIONS = {
    "Wales": ("the Welsh Government", "https://www.gov.wales/development-management-quarterly-survey"),
    "Scotland": ("the Scottish Government",
                 "https://www.gov.scot/collections/planning-performance-statistics/"),
    "Northern Ireland": ("the Department for Infrastructure",
                         "https://www.infrastructure-ni.gov.uk/articles/"
                         "northern-ireland-planning-statistics"),
}


def outside_coverage(country: str | None) -> dict | None:
    """None in England, or where we were not told. Otherwise the country
    and who publishes planning statistics there."""
    name = (country or "").strip()
    if not name or name == "England":
        return None
    body, url = OTHER_NATIONS.get(name, ("", ""))
    return {"country": name, "body": body, "url": url}


def source() -> dict | None:
    if not _load():
        return None
    return {"name": _data["source"], "url": _data["page"], "period": _data["period"],
            "read_on": _data["read_on"], "tables": _data["tables"],
            "authorities": len(_data["authorities"])}


def _against(council: float | None, england: float | None) -> str:
    """Three words, by one stated rule, never a judgement."""
    if council is None or england is None:
        return ""
    if abs(council - england) < SAME_WITHIN_PP:
        return "about the same as"
    return "higher than" if council > england else "lower than"


def for_council(code: str | None, name: str | None = None) -> dict | None:
    """One authority's year, with England beside it. The ONS code first,
    the council's name only as a fallback, since the code is the thing
    both publishers agree on."""
    if not _load():
        return None
    entry = _data["authorities"].get((code or "").strip())
    if entry is None and name:
        entry = _data["authorities"].get(_by_name.get(_normal(name), ""))
    if entry is None:
        return None
    england = _data["england"]
    return {
        **entry,
        "period": _data["period"],
        "england": england,
        "granted_against_england": _against(entry["granted_pct"], england["granted_pct"]),
        "extension_against_england": _against(entry["extension_pct"], england["extension_pct"]),
        "same_within_pp": int(SAME_WITHIN_PP) if SAME_WITHIN_PP == int(SAME_WITHIN_PP) else SAME_WITHIN_PP,
        "national_speed": _data["national_speed"],
        "source": _data["source"],
        "source_url": _data["page"],
        "tables": _data["tables"],
        "read_on": _data["read_on"],
    }
