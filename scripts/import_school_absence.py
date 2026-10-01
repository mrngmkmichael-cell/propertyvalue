"""How often children are out of school, council by council, from the
DfE's absence statistics, into a small local file.

Source: https://explore-education-statistics.service.gov.uk/
        find-statistics/pupil-absence-in-schools-in-england
API:    https://api.education.gov.uk/statistics/v1
Refresh: once a year, each spring, for the academic year before last.

Two figures a parent can use, for primary and secondary separately,
with England beside them:

  overall absence     the share of half-day sessions missed, the number
                      the published statistics lead on
  persistent absence  the share of pupils who missed a tenth or more of
                      their sessions, which is the one that describes
                      how many children, not how many days

The DfE publishes both by local authority and never by school: a
school's own rate exists in the performance tables, not here, so every
page that shows this says whose figure it is, exactly as the admission
appeals pages do.

    .venv/Scripts/python.exe scripts/import_school_absence.py

Writes app/data/school_absence.json. Re-runnable: the file is replaced.
"""
import datetime as dt
import json
import os
import sys

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

API = "https://api.education.gov.uk/statistics/v1"
PUBLICATION = ("https://explore-education-statistics.service.gov.uk/find-statistics/"
               "pupil-absence-in-schools-in-england")
SESSIONS = "019d209b-b031-7497-8205-af255b581d91"   # Absence sessions by reason
PERSISTENT = "019d209c-08dc-74b6-9edb-52d521406fcf"  # Absence for persistent and severe absentees
OUT = os.path.join(ROOT, "app", "data", "school_absence.json")
UA = {"User-Agent": "UKPropertyInsight import (support@ukpropertyinsight.co.uk)"}
PAGE_SIZE = 1000

# Filters are named by label and resolved against each data set's own
# meta, because the two data sets give the same option different ids:
# "State-funded primary" is HT00z in one and ksZ6h in the other, and
# pinning one set silently returned nothing from the other.
PHASE_LABELS = {"primary": "State-funded primary", "secondary": "State-funded secondary",
                "all": "Total"}
ATTENDANCE_LABELS = {"overall": "Overall absence", "unauthorised": "Unauthorised"}
REASON_LABELS = {"overall": "Overall absence", "unauthorised": "All unauthorised"}
ABSENTEES = {"persistent": "Persistent absence", "severe": "Severe absence"}
PCT_SESSIONS = "jgLjA"
PCT_PUPILS = "TuAuP"


def meta(dataset_id: str) -> dict:
    reply = httpx.get(f"{API}/data-sets/{dataset_id}/meta", headers=UA, timeout=120)
    reply.raise_for_status()
    return reply.json()


def latest_year(info: dict) -> dict:
    return info["timePeriods"][-1]


def la_codes(info: dict) -> dict:
    """The data set's own location ids, mapped to ONS codes and names."""
    group = next(g for g in info["locations"] if g["level"]["code"] == "LA")
    return {option["id"]: (option.get("code") or "", option.get("label") or "")
            for option in group["options"]}


def option_id(info: dict, filter_label: str, option_label: str) -> str:
    group = next(f for f in info["filters"] if f["label"] == filter_label)
    return next(o["id"] for o in group["options"] if o["label"] == option_label)


def query(dataset_id: str, clauses: list, indicators: list, period: dict) -> list:
    rows, page = [], 1
    while True:
        body = {
            "criteria": {"and": [*clauses,
                                 {"timePeriods": {"eq": {"period": period["period"],
                                                         "code": period["code"]}}},
                                 {"geographicLevels": {"in": ["LA", "NAT"]}}]},
            "indicators": indicators, "page": page, "pageSize": PAGE_SIZE,
        }
        reply = httpx.post(f"{API}/data-sets/{dataset_id}/query", json=body, headers=UA, timeout=180)
        reply.raise_for_status()
        payload = reply.json()
        rows.extend(payload["results"])
        if page >= payload["paging"]["totalPages"]:
            return rows
        page += 1


def phases(info: dict) -> dict:
    """This data set's own option id for each phase we show."""
    return {name: option_id(info, "School type", label)
            for name, label in PHASE_LABELS.items()}


def phase_of(row, filter_id, options):
    for name, option in options.items():
        if row["filters"].get(filter_id) == option:
            return name
    return None


def number(value):
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return None


def main():
    sessions_meta, persistent_meta = meta(SESSIONS), meta(PERSISTENT)
    period = latest_year(sessions_meta)
    if latest_year(persistent_meta)["period"] != period["period"]:
        raise SystemExit("the two absence data sets are on different years")
    codes = la_codes(sessions_meta)
    school_type = next(f["id"] for f in sessions_meta["filters"] if f["label"] == "School type")
    attendance = next(f["id"] for f in sessions_meta["filters"] if f["label"] == "Attendance type")
    reason = next(f["id"] for f in sessions_meta["filters"] if f["label"] == "Absence reason")

    councils: dict = {}
    england: dict = {}

    def slot(row, name):
        if row["geographicLevel"] == "NAT":
            return england.setdefault(name, {})
        code, label = codes.get(row["locations"].get("LA"), ("", ""))
        if not code:
            return None
        entry = councils.setdefault(code, {"name": label})
        return entry.setdefault(name, {})

    print(f"Absence sessions for {period['label']}")
    session_phases = phases(sessions_meta)
    attendance_ids = {name: option_id(sessions_meta, "Attendance type", label)
                      for name, label in ATTENDANCE_LABELS.items()}
    reason_ids = {name: option_id(sessions_meta, "Absence reason", label)
                  for name, label in REASON_LABELS.items()}
    rows = query(SESSIONS, [{"filters": {"in": list(session_phases.values())}},
                            {"filters": {"in": list(attendance_ids.values())}},
                            {"filters": {"in": list(reason_ids.values())}}],
                 [PCT_SESSIONS], period)
    for row in rows:
        phase = phase_of(row, school_type, session_phases)
        if phase is None:
            continue
        for measure, option in attendance_ids.items():
            if row["filters"].get(attendance) == option and row["filters"].get(reason) == reason_ids[measure]:
                target = slot(row, phase)
                if target is not None:
                    target[f"{measure}_pct"] = number(row["values"].get(PCT_SESSIONS))

    print(f"Persistent and severe absentees for {period['label']}")
    absentee_filter = next(f["id"] for f in persistent_meta["filters"] if f["label"] == "Absence type")
    absentee_type = next(f["id"] for f in persistent_meta["filters"] if f["label"] == "School type")
    wanted = {option_id(persistent_meta, "Absence type", label): name
              for name, label in ABSENTEES.items()}
    persistent_phases = phases(persistent_meta)
    rows = query(PERSISTENT, [{"filters": {"in": list(persistent_phases.values())}},
                              {"filters": {"in": list(wanted)}}],
                 [PCT_PUPILS], period)
    for row in rows:
        phase = phase_of(row, absentee_type, persistent_phases)
        measure = wanted.get(row["filters"].get(absentee_filter))
        if phase is None or measure is None:
            continue
        target = slot(row, phase)
        if target is not None:
            target[f"{measure}_pct"] = number(row["values"].get(PCT_PUPILS))

    complete = {code: entry for code, entry in councils.items()
                if entry.get("primary", {}).get("overall_pct") is not None}
    if len(complete) < 100 or not england.get("primary"):
        raise SystemExit(f"absence did not read as expected: {len(complete)} councils")

    payload = {
        "about": ("How often children are out of school, by council. Overall absence is the share "
                  "of half-day sessions missed. Persistent absence is the share of pupils who "
                  "missed a tenth or more of their sessions, and severe absence the share who "
                  "missed half or more. The DfE publishes these by local authority and never by "
                  "school."),
        "source": "DfE pupil absence in schools in England",
        "url": PUBLICATION,
        "year": period["label"],
        "read_on": dt.date.today().isoformat(),
        "coverage": ["England"],
        "persistent_threshold": "a tenth or more of their sessions",
        "severe_threshold": "half or more of their sessions",
        "england": england,
        "councils": complete,
    }
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, ensure_ascii=False)
        handle.write("\n")
    print(f"Wrote {OUT}")
    print(f"  {period['label']}, {len(complete)} councils")
    for phase in ("primary", "secondary"):
        row = england.get(phase, {})
        print(f"  England {phase}: {row.get('overall_pct')}% of sessions missed, "
              f"{row.get('persistent_pct')}% of pupils persistently absent")


if __name__ == "__main__":
    main()
