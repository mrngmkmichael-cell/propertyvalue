"""Your own must-haves, checked on every report (18 Sep 2026,
first-visitor audit F7).

A buyer arrives with a list in their head: no worse than flood zone 2,
band C or better, freehold, that school. The report rated all of it
already and made them read forty-four cards to find the six lines they
came for. A reader now sets up to six conditions once and every report
answers them in one panel, and My properties says how many each saved
home meets.

Nothing here is scored, modelled or weighted. Each condition is a plain
threshold on a fact the report already holds, and a condition is met,
not met, or not answerable, never "probably". Where a source does not
cover the address the panel says so in words rather than counting it as
a miss: "Not mapped for Wales" is not "fails your flood condition".

Batch B's rule holds: a condition on a locked check never carries its
value for a reader who has not opened that home. facts() is handed
premium_unlocked and leaves a locked check's value out entirely, so
neither the panel, the page's JSON nor a data attribute can leak it.
The page shows "Opens with your free full report", or "Opens with
Premium" for an account that has spent its free one.

Conditions live on the device without an account (localStorage, in the
page's own script) and, signed in, on the account as well
(models.MustHaveConditions, one row per account). Every read and write
here is scoped to a user id that comes from the session, never from a
request, the boundary app/watchlist.py keeps.
"""
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import get_session
from app.models import MustHaveConditions

# Six conditions, the owner's number. A buyer with a longer list is
# weighing up, not filtering, and a panel of ten lines stops being read.
MAX_CONDITIONS = 6

# The bands each scale is read on, best first. An index into one of these
# is what a threshold and a fact are compared on, so "no worse than Zone
# 2" is one comparison of two positions rather than a parse of two
# strings.
_FLOOD_ZONES = ("1", "2", "3")
_SURFACE_WATER = ("Very low risk", "Low risk", "Medium risk", "High risk")
_SUBSIDENCE = ("Improbable", "Possible", "Probable")
_EPC_BANDS = ("A", "B", "C", "D", "E", "F", "G")
# broadband._band_label's own four labels, best first.
_BROADBAND = ("Gigabit-capable", "Ultrafast", "Superfast", "Standard/limited")


def _band_options(bands, prefix=""):
    return tuple({"value": b, "label": f"{prefix}{b}"} for b in bands)


# Every condition a reader may set: the check it reads, the card its line
# opens, whether that check is locked, and the thresholds offered. "worse"
# means a larger index in the scale above is a miss; "at_least" means a
# smaller index is fine; "at_most" compares two numbers; "is" is the one
# yes-or-no condition.
#
# Only checks the report already rates are here, and only where the
# threshold is a fact rather than a judgement: there is no "nice area"
# condition, because no source publishes one.
CONDITIONS = (
    {
        "key": "flood", "check": "Flood Risk", "modal": "modal-flood", "locked": False,
        "prompt": "Flood zone no worse than", "test": "worse", "scale": _FLOOD_ZONES,
        "options": _band_options(_FLOOD_ZONES, "Zone "),
        "source": "Environment Agency",
    },
    {
        "key": "surface_water", "check": "Surface Water Risk", "modal": "modal-surface-water", "locked": False,
        "prompt": "Surface water risk no worse than", "test": "worse", "scale": _SURFACE_WATER,
        "options": _band_options(_SURFACE_WATER),
        "source": "Environment Agency",
    },
    {
        "key": "epc", "check": "Energy Efficiency", "modal": "modal-epc", "locked": False,
        "prompt": "EPC band at least", "test": "worse", "scale": _EPC_BANDS,
        "options": _band_options(_EPC_BANDS, "Band "),
        "source": "EPC Register",
    },
    {
        "key": "broadband", "check": "Broadband", "modal": "modal-broadband", "locked": False,
        "prompt": "Broadband at least", "test": "worse", "scale": _BROADBAND,
        "options": _band_options(_BROADBAND),
        "source": "Ofcom",
    },
    {
        "key": "council_tax", "check": "Council Tax", "modal": "modal-council-tax", "locked": False,
        "prompt": "Band D council tax at most", "test": "at_most",
        "options": ({"value": "1500", "label": "£1,500 a year"},
                    {"value": "1750", "label": "£1,750 a year"},
                    {"value": "2000", "label": "£2,000 a year"},
                    {"value": "2250", "label": "£2,250 a year"},
                    {"value": "2500", "label": "£2,500 a year"}),
        "source": "MHCLG, Welsh and Scottish Governments",
    },
    {
        "key": "tenure", "check": "Local Market", "modal": "modal-sold-price-history", "locked": False,
        "prompt": "Tenure on the last recorded sale", "test": "is",
        "options": ({"value": "freehold", "label": "Freehold"},),
        "source": "HM Land Registry",
    },
    {
        "key": "sewage", "check": "Sewage Discharge", "modal": "modal-sewage", "locked": True,
        "prompt": "Spill hours at the nearest storm overflow no more than", "test": "at_most",
        "options": ({"value": "0", "label": "None at all"},
                    {"value": "24", "label": "24 hours a year"},
                    {"value": "100", "label": "100 hours a year"},
                    {"value": "500", "label": "500 hours a year"}),
        "source": "Environment Agency",
    },
    {
        "key": "subsidence", "check": "Subsidence Risk", "modal": "modal-clay-risk", "locked": True,
        "prompt": "Subsidence risk by 2030 no worse than", "test": "worse", "scale": _SUBSIDENCE,
        "options": _band_options(_SUBSIDENCE),
        "source": "British Geological Survey",
    },
    {
        "key": "buses", "check": "Bus Service", "modal": "modal-bus", "locked": True,
        "prompt": "Buses an hour at the best stop at least", "test": "at_least",
        "options": ({"value": "2", "label": "2 an hour"},
                    {"value": "4", "label": "4 an hour"},
                    {"value": "6", "label": "6 an hour"}),
        "source": "DfT Bus Open Data Service",
    },
    {
        "key": "school", "check": "School Catchment Areas", "modal": "modal-catchment", "locked": True,
        "prompt": "Likely for this school", "test": "school", "free_text": True,
        "options": (),
        "source": "Council admissions data",
    },
)

BY_KEY = {c["key"]: c for c in CONDITIONS}
# A named school is typed, not chosen from a list, because the school a
# buyer wants is rarely near every home they look at. Bounded so a
# request cannot carry a paragraph.
SCHOOL_NAME_MAX = 120


def _index(scale: tuple, value) -> int | None:
    text = str(value or "").strip()
    for i, band in enumerate(scale):
        if band.lower() == text.lower():
            return i
    return None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse(raw) -> dict:
    """The conditions a request or a stored row carries, keeping only
    keys this module knows and thresholds it offers, at most
    MAX_CONDITIONS of them, in CONDITIONS' own order. Anything else is
    dropped rather than refused, so a stored row written by an older
    version of the page still opens."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw, dict):
        return {}
    kept = {}
    for condition in CONDITIONS:
        key = condition["key"]
        if key not in raw:
            continue
        value = raw[key]
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            continue
        value = str(value).strip()
        if not value:
            continue
        if condition.get("free_text"):
            if len(value) > SCHOOL_NAME_MAX:
                continue
            kept[key] = " ".join(value.split())
        elif any(o["value"] == value for o in condition["options"]):
            kept[key] = value
        if len(kept) >= MAX_CONDITIONS:
            break
    return kept


def option_label(key: str, value: str) -> str:
    """How the reader's own threshold reads back to them."""
    condition = BY_KEY.get(key)
    if not condition:
        return ""
    if condition.get("free_text"):
        return value
    for option in condition["options"]:
        if option["value"] == value:
            return option["label"]
    return value


# ---- The facts one report holds -----------------------------------------
# Each returns {"known", "value", "text", "gap"}: the comparable value,
# the words for it, and, where the source does not reach this address,
# what to say instead of a miss. A check whose service failed is simply
# not known, and the panel says so.


def _unknown(gap: str | None = None) -> dict:
    return {"known": False, "value": None, "text": "", "gap": gap}


def _fact_flood(context: dict) -> dict:
    gap = context.get("flood_not_covered")
    if gap:
        country = gap.get("country") if isinstance(gap, dict) else None
        return _unknown(f"Not mapped for {country}" if country else "Not mapped here")
    zone = context.get("flood_zone") or {}
    number = zone.get("zone")
    if not number:
        return _unknown()
    return {"known": True, "value": _index(_FLOOD_ZONES, str(number)),
            "text": zone.get("label") or f"Zone {number}", "gap": None}


def _fact_surface_water(context: dict) -> dict:
    gap = context.get("flood_not_covered")
    if gap:
        country = gap.get("country") if isinstance(gap, dict) else None
        return _unknown(f"Not mapped for {country}" if country else "Not mapped here")
    risk = context.get("surface_water") or {}
    index = _index(_SURFACE_WATER, risk.get("label"))
    if index is None:
        return _unknown()
    return {"known": True, "value": index, "text": risk["label"], "gap": None}


def _fact_epc(context: dict) -> dict:
    # Without a house number the certificate is the postcode's newest,
    # another home's (18 Sep 2026, item D1), so the band is not this
    # home's to test.
    if not context.get("house_number"):
        return _unknown("Needs a house number")
    band = (context.get("property_detail") or {}).get("current_band")
    index = _index(_EPC_BANDS, band)
    if index is None:
        return _unknown()
    return {"known": True, "value": index, "text": f"Band {band}", "gap": None}


def _fact_broadband(context: dict) -> dict:
    label = (context.get("broadband") or {}).get("label")
    index = _index(_BROADBAND, label)
    if index is None:
        return _unknown()
    return {"known": True, "value": index, "text": label, "gap": None}


def _fact_council_tax(context: dict) -> dict:
    band_d = (context.get("council_tax") or {}).get("band_d")
    amount = _number(band_d)
    if amount is None:
        return _unknown()
    return {"known": True, "value": amount,
            "text": f"£{amount:,.0f} a year at Band D", "gap": None}


def _fact_tenure(context: dict) -> dict:
    if not context.get("house_number"):
        return _unknown("Needs a house number")
    sales = context.get("transactions") or []
    tenure = (sales[0].get("tenure") or "").strip().lower() if sales else ""
    if not tenure:
        return _unknown()
    return {"known": True, "value": tenure, "text": tenure.capitalize(), "gap": None}


def _fact_sewage(context: dict) -> dict:
    gap = context.get("england_only_gap")
    if gap:
        return _unknown(f"Not covered in {gap}")
    if context.get("sewage_error"):
        return _unknown()
    outfalls = context.get("sewage_outfalls")
    if outfalls is None:
        return _unknown()
    if not outfalls:
        return {"known": True, "value": 0.0, "text": "No outfalls found nearby", "gap": None}
    hours = _number(outfalls[0].get("duration_hrs"))
    if hours is None:
        return _unknown()
    year = outfalls[0].get("year")
    return {"known": True, "value": hours,
            "text": f"{hours:,.0f} spill hours nearby" + (f" in {year}" if year else ""), "gap": None}


def _fact_subsidence(context: dict) -> dict:
    index = _index(_SUBSIDENCE, (context.get("clay_risk") or {}).get("class_2030"))
    if index is None:
        return _unknown()
    return {"known": True, "value": index,
            "text": f"{_SUBSIDENCE[index]} by 2030", "gap": None}


def _fact_buses(context: dict) -> dict:
    best = (context.get("bus_service") or {}).get("best") or {}
    per_hour = _number(best.get("weekday_day_per_hour"))
    if per_hour is None:
        return _unknown()
    return {"known": True, "value": per_hour,
            "text": f"{per_hour:g} an hour at {best.get('name') or 'the best stop'}", "gap": None}


def _fact_school(context: dict) -> dict:
    """Every school this report holds a council's published distance for,
    with its reading. An estimated distance is left out: the condition is
    on published figures only, so a school we only have an estimate for
    is not answered rather than answered from a model."""
    summary = context.get("school_verdicts") or {}
    rows = [r for r in (summary.get("schools") or []) if r.get("kind") == "published"]
    if not rows:
        return _unknown()
    return {"known": True, "gap": None, "text": "",
            "value": {r["name"]: {"level": r["level"], "label": r["label"]} for r in rows}}


_FACTS = {
    "flood": _fact_flood, "surface_water": _fact_surface_water, "epc": _fact_epc,
    "broadband": _fact_broadband, "council_tax": _fact_council_tax, "tenure": _fact_tenure,
    "sewage": _fact_sewage, "subsidence": _fact_subsidence, "buses": _fact_buses,
    "school": _fact_school,
}


def facts(context: dict, premium_unlocked: bool) -> dict:
    """What this report can answer, by condition key. A locked check for
    a reader who has not opened this home carries no value, no text and
    no gap: only {"locked": True}. The page renders this into its script,
    so anything here is readable by anyone who views source, and batch
    B's rule is that a locked finding is not."""
    out = {}
    for condition in CONDITIONS:
        key = condition["key"]
        if condition["locked"] and not premium_unlocked:
            out[key] = {"locked": True, "known": False, "value": None, "text": "", "gap": None}
            continue
        fact = _FACTS[key](context)
        out[key] = {"locked": False, **fact}
    return out


# ---- One reader's conditions against one home's facts --------------------


def _met(condition: dict, threshold: str, fact: dict) -> bool | None:
    """True, False, or None when the fact is not known. Never a guess."""
    if not fact.get("known"):
        return None
    value = fact.get("value")
    test = condition["test"]
    if test == "worse":
        wanted = _index(condition["scale"], threshold)
        return None if wanted is None or value is None else value <= wanted
    if test == "at_most":
        wanted = _number(threshold)
        return None if wanted is None or value is None else value <= wanted
    if test == "at_least":
        wanted = _number(threshold)
        return None if wanted is None or value is None else value >= wanted
    if test == "is":
        return str(value or "").strip().lower() == threshold.strip().lower()
    if test == "school":
        schools = value if isinstance(value, dict) else {}
        wanted = threshold.strip().lower()
        for name, reading in schools.items():
            if name.strip().lower() == wanted:
                return reading.get("level") == "likely"
        return None
    return None


def evaluate(conditions: dict, known: dict, unlocked_label: str = "",
             no_answer: str = "Not answered on this report") -> dict:
    """One row per condition the reader set, in CONDITIONS' order, and
    the counts the panel's first line reads. A row is:

      key, prompt, threshold (the reader's words), modal (the card it
      opens), met (True, False or None), text (what the report found,
      empty when it did not), why (why there is no answer).

    A locked row's "why" is the offer, never a hint of the reading."""
    rows = []
    for condition in CONDITIONS:
        key = condition["key"]
        if key not in conditions:
            continue
        threshold = conditions[key]
        fact = known.get(key) or {"locked": condition["locked"], "known": False}
        row = {
            "key": key, "prompt": condition["prompt"], "check": condition["check"],
            "threshold": option_label(key, threshold), "modal": condition["modal"],
            "source": condition["source"], "locked": bool(fact.get("locked")),
            "met": None, "text": "", "why": "",
        }
        if fact.get("locked"):
            row["why"] = unlocked_label or "Opens with a full report"
        else:
            row["met"] = _met(condition, threshold, fact)
            row["text"] = fact.get("text") or ""
            if row["met"] is None:
                row["why"] = fact.get("gap") or no_answer
        rows.append(row)
    met = sum(1 for r in rows if r["met"] is True)
    unknown = sum(1 for r in rows if r["met"] is None)
    return {
        "rows": rows, "met": met, "total": len(rows), "unknown": unknown,
        "line": summary_line(met, len(rows), unknown),
    }


def summary_line(met: int, total: int, unknown: int) -> str:
    """"3 of 5 met, 1 not yet known". The count of met is never padded
    with what is unknown, and what is unknown is never counted as a
    miss: both are said."""
    if not total:
        return ""
    line = f"{met} of {total} met"
    if unknown:
        line += f", {unknown} not yet known"
    return line


# ---- The same conditions against a saved home's snapshot -----------------
# My properties holds a snapshot per home (main._comparison_summary), the
# one the change alerts compare. It answers the free checks it already
# carries and nothing else: a locked check is not in it, so a saved home
# reads "not yet known" for those until its own report is opened, which
# is where a full report answers them.
#
# Surface water and broadband (21 Sep 2026) are free checks the snapshot
# did not carry, so every saved home read "Not yet known" for both. A
# report visit now writes them into the home's snapshot (snapshot_facts,
# called by main._summary_from_report), and My properties and the alert
# job carry them forward, because _comparison_summary does not fetch
# them. A home whose report has not been opened since holds neither, and
# its line says so rather than a bare "Not yet known".

_SNAPSHOT_TEXT_KEYS = {"flood": "flood_zone", "epc": "energy_band",
                       "council_tax": "band_d", "tenure": "tenure",
                       "surface_water": "surface_water", "broadband": "broadband"}

# The two snapshot keys only a report visit writes.
REPORT_SNAPSHOT_KEYS = ("surface_water", "broadband")
# Written where the report has broadband's answer but Ofcom holds no figure
# for the postcode, so My properties says what the report says ("No broadband
# coverage data available for this postcode") rather than "not yet known".
BROADBAND_NO_FIGURE = "No broadband coverage data for this postcode"
# What a saved home says for either before its report has written them.
NOT_YET_FROM_REPORT = "Not yet known until you next open this home's report"


def snapshot_facts(context: dict) -> dict:
    """The two report-only facts for a saved home's snapshot, from a
    report's own context (21 Sep 2026): the band where the report has
    one, the report's own words where the source does not reach this
    address ("Not mapped for Wales") or holds no figure for it, and
    nothing at all where the check failed on this render, so the stored
    reading is kept (main._keep_report_facts)."""
    out = {}
    surface = _fact_surface_water(context)
    if surface["known"] or surface["gap"]:
        out["surface_water"] = surface["text"] or surface["gap"]
    if "broadband" in context and not context.get("broadband_error"):
        if not context.get("broadband"):
            out["broadband"] = BROADBAND_NO_FIGURE
        else:
            broadband = _fact_broadband(context)
            if broadband["known"]:
                out["broadband"] = broadband["text"]
    return out


def facts_from_snapshot(snapshot: dict) -> dict:
    """The same shape facts() returns, from what a saved home's snapshot
    holds. Keys the snapshot does not carry come back not known."""
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    known = {c["key"]: {"locked": False, "known": False, "value": None, "text": "", "gap": None}
             for c in CONDITIONS}

    zone_label = str(snapshot.get("flood_zone") or "")
    if zone_label.startswith("Zone "):
        index = _index(_FLOOD_ZONES, zone_label[5:6])
        if index is not None:
            known["flood"] = {"locked": False, "known": True, "value": index, "text": zone_label, "gap": None}
    elif zone_label:
        # "Not mapped for Wales", written by the watchlist's own summary.
        known["flood"]["gap"] = zone_label

    # Surface water and broadband as the home's report last wrote them (21
    # Sep 2026, snapshot_facts). Outside England surface water is not
    # mapped either: the same Environment Agency maps as the flood zone
    # (flood_zones.outside_coverage), and the report says so for both.
    surface = str(snapshot.get("surface_water") or "")
    surface_index = _index(_SURFACE_WATER, surface)
    if surface_index is not None:
        known["surface_water"] = {"locked": False, "known": True, "value": surface_index,
                                  "text": _SURFACE_WATER[surface_index], "gap": None}
    elif surface.startswith("Not mapped"):
        known["surface_water"]["gap"] = surface
    elif zone_label.startswith("Not mapped"):
        known["surface_water"]["gap"] = zone_label
    else:
        known["surface_water"]["gap"] = NOT_YET_FROM_REPORT

    broadband = str(snapshot.get("broadband") or "")
    broadband_index = _index(_BROADBAND, broadband)
    if broadband_index is not None:
        known["broadband"] = {"locked": False, "known": True, "value": broadband_index,
                              "text": _BROADBAND[broadband_index], "gap": None}
    elif broadband == BROADBAND_NO_FIGURE:
        known["broadband"]["gap"] = BROADBAND_NO_FIGURE
    else:
        known["broadband"]["gap"] = NOT_YET_FROM_REPORT

    band_index = _index(_EPC_BANDS, snapshot.get("energy_band"))
    if band_index is not None:
        known["epc"] = {"locked": False, "known": True, "value": band_index,
                        "text": f"Band {snapshot['energy_band']}", "gap": None}

    band_d = _number(snapshot.get("band_d"))
    if band_d is not None:
        known["council_tax"] = {"locked": False, "known": True, "value": band_d,
                                "text": f"£{band_d:,.0f} a year at Band D", "gap": None}

    tenure = str(snapshot.get("tenure") or "").strip().lower()
    if tenure:
        known["tenure"] = {"locked": False, "known": True, "value": tenure,
                           "text": tenure.capitalize(), "gap": None}

    # The names the snapshot keeps are the schools this home is Likely
    # for, already published figures (main._comparison_summary reads them
    # from the same verdicts the report does). A name that is not in the
    # list is not a miss: the snapshot holds only the likely ones, so
    # anything else is not known here.
    verdicts = snapshot.get("school_verdicts") or {}
    likely = verdicts.get("likely") if isinstance(verdicts, dict) else None
    if isinstance(likely, list) and likely:
        known["school"] = {"locked": False, "known": True, "gap": None, "text": "",
                           "value": {name: {"level": "likely", "label": "Likely"} for name in likely}}
    return known


def for_snapshot(conditions: dict, snapshot: dict, unlocked_label: str = "",
                 premium_unlocked: bool = False) -> dict:
    """"3 of 5 met, 2 not yet known" for one saved home, and its rows.

    A condition on a locked check says where it opens, as on the report,
    unless this home is already open in full: then it is simply one the
    snapshot does not answer, and its own report does."""
    if not conditions:
        return {"rows": [], "met": 0, "total": 0, "unknown": 0, "line": ""}
    known = facts_from_snapshot(snapshot)
    for condition in CONDITIONS:
        if condition["locked"]:
            known[condition["key"]] = {"locked": not premium_unlocked, "known": False,
                                       "value": None, "text": "", "gap": None}
    # A fact the snapshot does not hold is answered on the home's own
    # report, which is where the reader is sent, rather than dressed up
    # as a miss here.
    return evaluate(conditions, known, unlocked_label=unlocked_label, no_answer="Not yet known")


# ---- The account's own row ----------------------------------------------


def load(user_id: int) -> dict:
    """This account's conditions, or an empty dict. One row per account,
    read once for the page that needs it."""
    with get_session() as session:
        row = session.scalar(select(MustHaveConditions).where(MustHaveConditions.user_id == user_id))
        return parse(row.conditions) if row else {}


def save(user_id: int, conditions: dict, _retry: bool = True) -> dict:
    """Keep this account's conditions, replacing whatever was there. An
    empty set clears the row's conditions rather than deleting it, so
    nothing is ever removed from the table."""
    kept = parse(conditions)
    try:
        with get_session() as session:
            row = session.scalar(select(MustHaveConditions).where(MustHaveConditions.user_id == user_id))
            if row is None:
                row = MustHaveConditions(user_id=user_id)
                session.add(row)
            row.conditions = json.dumps(kept, separators=(",", ":"))
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            return kept
    except IntegrityError:
        # Two first saves at once (the page's form and its script): the
        # other made the row, so this one updates it.
        if not _retry:
            raise
        return save(user_id, conditions, _retry=False)
