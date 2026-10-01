"""How often children are out of school in one council's schools.

Read from app/data/school_absence.json, which
scripts/import_school_absence.py writes from the DfE's absence
statistics: 152 councils, primary and secondary, with England beside
them. No database and no network.

Two figures, because they answer different questions. Overall absence
is the share of half-day sessions missed, which is the headline the
statistics lead on and is moved by a bad flu week as much as by
anything. Persistent absence is the share of pupils who missed a tenth
or more of their sessions, which says how many children rather than how
many days, and is the one that tends to describe a school system.

The DfE publishes both by local authority and never by school, exactly
as it does with admission appeals, so every page that shows this says
whose figure it is.
"""
import json
import logging
import os
import threading

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "school_absence.json")
# Under a percentage point apart, two absence rates are the same thing.
# Said on the page, so a reader is never left to guess the rule.
SAME_WITHIN_PP = 1.0

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
            _by_name = {_normal(entry["name"]): code
                        for code, entry in _data["councils"].items()}
        except Exception as problem:  # noqa: BLE001 - a page must not care
            logging.warning("school absence file unavailable: %s", problem)
            _data = {}
        return bool(_data)


def source() -> dict | None:
    if not _load():
        return None
    return {"name": _data["source"], "url": _data["url"], "year": _data["year"],
            "read_on": _data["read_on"], "councils": len(_data["councils"])}


def _against(council: float | None, england: float | None) -> str:
    if council is None or england is None:
        return ""
    if abs(council - england) < SAME_WITHIN_PP:
        return "about the same as"
    return "higher than" if council > england else "lower than"


def for_council(name: str | None, code: str | None = None) -> dict | None:
    """One council's absence beside England's. The council hub holds a
    name rather than an ONS code, so the name is the way in and the code
    is used when a caller has one."""
    if not _load():
        return None
    entry = _data["councils"].get((code or "").strip())
    if entry is None and name:
        entry = _data["councils"].get(_by_name.get(_normal(name), ""))
    if entry is None:
        return None
    england = _data["england"]
    phases = []
    for phase, label in (("primary", "Primary"), ("secondary", "Secondary")):
        here, nation = entry.get(phase) or {}, england.get(phase) or {}
        if here.get("overall_pct") is None:
            continue
        phases.append({
            "phase": label,
            "overall_pct": here.get("overall_pct"),
            "unauthorised_pct": here.get("unauthorised_pct"),
            "persistent_pct": here.get("persistent_pct"),
            "severe_pct": here.get("severe_pct"),
            "england_overall_pct": nation.get("overall_pct"),
            "england_persistent_pct": nation.get("persistent_pct"),
            "overall_against_england": _against(here.get("overall_pct"), nation.get("overall_pct")),
            "persistent_against_england": _against(here.get("persistent_pct"),
                                                   nation.get("persistent_pct")),
        })
    if not phases:
        return None
    return {
        "council": entry["name"],
        "year": _data["year"],
        "phases": phases,
        "primary": phases[0],
        "england": england,
        "same_within_pp": int(SAME_WITHIN_PP) if SAME_WITHIN_PP == int(SAME_WITHIN_PP) else SAME_WITHIN_PP,
        "persistent_threshold": _data["persistent_threshold"],
        "severe_threshold": _data["severe_threshold"],
        "source": _data["source"],
        "source_url": _data["url"],
        "read_on": _data["read_on"],
    }
