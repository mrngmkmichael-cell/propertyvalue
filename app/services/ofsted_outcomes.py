"""What Ofsted's monthly file actually says about a school, read in full.

Until 4 Sep 2026 the import used one column, "Latest OEIF overall
effectiveness", the 1-4 grade from the school's last *graded* inspection.
That column is blank for more than half of all schools, for two reasons
that are not "never inspected":

- Ungraded inspections. A school judged Good or Outstanding is usually
  revisited with an ungraded inspection whose outcome is "School remains
  Good", "Standards maintained", "Improved significantly" and so on. The
  file records the outcome and the date; the grade column stays blank
  when the graded inspection belonged to a predecessor URN (an academy
  conversion), which is why Harris Primary Academy Orpington read as
  "Not rated" with a May 2025 inspection saying "Improved significantly".
- Report cards. Since November 2025 Ofsted grades nine areas on a
  five-point scale (Exceptional, Strong standard, Expected standard,
  Needs attention, Urgent improvement) plus Safeguarding met or not met,
  and gives no overall grade at all.

This module turns one row of that file into what the site shows: a
rating and label where one honestly exists, a one-line note where it
does not, and the report-card areas where they exist. Pure functions, so
the importer and the tests share them.
"""
from __future__ import annotations

import datetime as dt

RATING_LABELS = {1: "Outstanding", 2: "Good", 3: "Requires improvement", 4: "Inadequate"}
REPORT_CARD_RATING = 5  # a code for the badge colour; not a quality level
REPORT_CARD_LABEL = "Report card"

CARD_AREAS = [
    ("safeguarding", "Safeguarding standards", "Safeguarding"),
    ("inclusion", "Inclusion", "Inclusion"),
    ("curriculum", "Curriculum and teaching", "Curriculum and teaching"),
    ("achievement", "Achievement", "Achievement"),
    ("attendance", "Attendance and behaviour", "Attendance and behaviour"),
    ("personal", "Personal development and wellbeing", "Personal development and wellbeing"),
    ("early_years", "Early years (where applicable)", "Early years"),
    ("post16", "Post-16 provision (where applicable)", "Post-16 provision"),
    ("leadership", "Leadership and governance", "Leadership and governance"),
]
CARD_DATE_COLUMNS = {
    "safeguarding": "Safeguarding standards - date of grade",
    "inclusion": "Inclusion - date of grade",
    "curriculum": "Curriculum and teaching - date of grade",
    "achievement": "Achievement - date of grade",
    "attendance": "Attendance and behaviour - date of grade",
    "personal": "Personal development and wellbeing - date of grade",
    "early_years": "Early years - date of grade",
    "post16": "Post-16 provision - date of grade",
    "leadership": "Leadership and governance - date of grade",
}
CARD_GOOD = {"Exceptional", "Strong standard"}
CARD_EXPECTED = {"Expected standard"}
CARD_WEAK = {"Needs attention", "Urgent improvement"}


def _clean(value) -> str:
    v = (value or "").strip()
    return "" if v.upper() == "NULL" else v


def _grade(value) -> int | None:
    v = _clean(value)
    return int(v) if v.isdigit() and int(v) in RATING_LABELS else None


def _date(value) -> dt.date | None:
    v = _clean(value)
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _month(d: dt.date | None) -> str:
    return d.strftime("%B %Y") if d else ""


def derive(row: dict) -> dict:
    """One Ofsted CSV row to the site's fields.

    Returns rating (1-4, 5 for a report card, or None), rating_label,
    inspection_date, note (one line, may be empty), card (dict of area
    key to grade string, empty when there is no report card) and
    card_date."""
    graded = _grade(row.get("Latest OEIF overall effectiveness"))
    graded_date = _date(row.get("Publication date of latest OEIF graded inspection"))
    outcome = _clean(row.get("Ungraded inspection overall outcome"))
    ungraded_date = _date(row.get("Ungraded inspection publication date")) or _date(row.get("Date of latest ungraded inspection"))

    card = {}
    card_dates = []
    for key, column, _label in CARD_AREAS:
        g = _clean(row.get(column))
        if g:
            card[key] = g
            d = _date(row.get(CARD_DATE_COLUMNS[key]))
            if d:
                card_dates.append(d)
    card_date = max(card_dates) if card_dates else _date(row.get("Publication date"))

    if card:
        graded_areas = {k: v for k, v in card.items() if k != "safeguarding"}
        strong = sum(1 for v in graded_areas.values() if v in CARD_GOOD)
        expected = sum(1 for v in graded_areas.values() if v in CARD_EXPECTED)
        weak = sum(1 for v in graded_areas.values() if v in CARD_WEAK)
        parts = []
        if strong:
            parts.append(f"{strong} area{'s' if strong != 1 else ''} strong or exceptional")
        if expected:
            parts.append(f"{expected} at the expected standard")
        if weak:
            parts.append(f"{weak} needing attention or urgent improvement")
        if card.get("safeguarding") == "Not met":
            parts.append("safeguarding not met")
        note = f"Report card, {_month(card_date)}: " + ", ".join(parts) if parts else f"Report card, {_month(card_date)}"
        return {"rating": REPORT_CARD_RATING, "rating_label": REPORT_CARD_LABEL,
                "inspection_date": card_date, "note": note, "card": card, "card_date": card_date}

    if outcome and (graded_date is None or (ungraded_date and ungraded_date >= graded_date)):
        low = outcome.lower()
        when = _month(ungraded_date)
        if "remains outstanding" in low:
            rating, label = 1, "Outstanding"
        elif "remains good" in low:
            rating, label = 2, "Good"
        else:
            rating, label = graded, (RATING_LABELS.get(graded, "") if graded else "")
        qualifier = ""
        if "concerns" in low:
            qualifier = ", with concerns raised"
        elif "improving" in low:
            qualifier = ", improving"
        if rating in (1, 2) and "remains" in low:
            text = f"Ungraded inspection, {when}: school remains {label}{qualifier}"
        else:
            text = f"Ungraded inspection, {when}: {outcome[0].lower() + outcome[1:]}"
        return {"rating": rating, "rating_label": label,
                "inspection_date": ungraded_date if rating else None,
                "note": text, "card": {}, "card_date": None}

    return {"rating": graded, "rating_label": RATING_LABELS.get(graded, "") if graded else "",
            "inspection_date": graded_date if graded else None, "note": "", "card": {}, "card_date": None}


def card_rows(detail) -> list[tuple[str, str]]:
    """(area label, grade) pairs from a SchoolDetail row, in Ofsted's
    order, for the school page. Empty when the school has no card."""
    out = []
    for key, _column, label in CARD_AREAS:
        value = getattr(detail, f"ofsted_card_{key}", "") if detail is not None else ""
        if value:
            out.append((label, value))
    return out
