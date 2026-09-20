"""Is the asking price in line? The Comparables page's filters and figures
(18 Sep 2026, first-visitor audit of 17 Sep, item F1).

The page lists up to 300 nearby Land Registry sales, and until this date
its median, range, "sits above 71%" sentence and position bar were worked
out once over every one of them: flats beside detached houses, and Land
Registry's "Other", which is where a sports centre or an office block is
filed. The year buttons hid rows but never moved a figure. Among the 80
semi-detached sales alone, the audit's £823,500 sat above 40%, not 71%.

Type chips, a tenure toggle and the year buttons now choose one set of
rows, and every figure on the page describes that set. The page's own
script does the same sums on the rows in the page as the reader changes
them; this module is the server's half, for the first render and for a
reader without JavaScript, whose form submits the same choices as query
parameters. Both halves must agree to the pound, so each rule here is
written to be repeated line for line in comparables.html's script: the
median is the upper middle recorded sale (main._median), percentages
round halves up, and a year cutoff is today less N years.

Nothing here estimates a price. It ranks, filters and compares sales HM
Land Registry recorded.
"""
from __future__ import annotations

import datetime
import math
import re

# Land Registry's five property types, as the chips name them. The Price
# Paid Data labels flats "flat-maisonette"; a sale with no type recorded
# goes with Other, the bucket for everything that is not one of the four.
TYPES = ("detached", "semi-detached", "terraced", "flat", "other")
TYPE_LABELS = {
    "detached": "Detached", "semi-detached": "Semi-detached", "terraced": "Terraced",
    "flat": "Flat", "other": "Other",
}
HOME_TYPES = TYPES[:4]
HOUSE_TYPES = TYPES[:3]
TENURES = ("freehold", "leasehold")
YEAR_CHOICES = (10, 5, 2)

# An asking price is typed by hand and travels in a URL, so it is read
# strictly: digits, with the pound sign, commas, spaces and pence
# allowed and dropped. Below £1,000 or above £100m it is a typing slip.
PRICE_MIN = 1_000
PRICE_MAX = 100_000_000
PRICE_INPUT_MAX = 20


def row_type(value) -> str:
    v = (value or "").strip().lower()
    if v.startswith("flat"):
        return "flat"
    return v if v in HOUSE_TYPES else "other"


def row_tenure(value) -> str:
    v = (value or "").strip().lower()
    return v if v in TENURES else ""


def parse_price(raw) -> int | None:
    text = str(raw or "").strip()
    if not text or len(text) > PRICE_INPUT_MAX:
        return None
    # ASCII digits only, as the script's regular expression reads them.
    m = re.fullmatch(r"([0-9]+)(?:\.[0-9]{1,2})?", re.sub(r"[£,\s]", "", text))
    if not m:
        return None
    value = int(m.group(1))
    return value if PRICE_MIN <= value <= PRICE_MAX else None


def half_up(value: float) -> int:
    """Halves up, as the script's Math.round does."""
    return math.floor(value + 0.5)


def years_cutoff(years: int, today: datetime.date) -> str | None:
    """The earliest sale date a "Last N years" choice keeps, as ISO text.
    29 February rolls to 1 March, as the script's setFullYear does."""
    if not years:
        return None
    try:
        return today.replace(year=today.year - years).isoformat()
    except ValueError:
        return datetime.date(today.year - years, 3, 1).isoformat()


def _amount(row) -> float | None:
    try:
        value = float(row.get("amount"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def default_types(known_type: str | None) -> list[str]:
    """The chips ticked before the reader touches them: the home's own
    type when its sale is on record, otherwise the four kinds of home.
    Other starts unticked either way unless it is the home's own."""
    return [known_type] if known_type in TYPES else list(HOME_TYPES)


def state_from_query(params, known_type: str | None = None) -> dict:
    """The reader's choices from the page's query string. A `type`
    parameter that is present, even empty, is taken as it stands: the
    no-JavaScript form always sends an empty one, so unticking every chip
    reads as no type rather than the defaults. The last `years` wins: the
    form sends its current choice first and a pressed year button last."""
    if "type" in params:
        wanted = set(params.getlist("type"))
        types = [t for t in TYPES if t in wanted]
    else:
        types = default_types(known_type)
    tenure = (params.get("tenure") or "").strip().lower()
    years = 0
    for raw in params.getlist("years"):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value == 0 or value in YEAR_CHOICES:
            years = value
    return {
        "types": types,
        "tenure": tenure if tenure in TENURES else "any",
        "years": years,
        "price": parse_price(params.get("price")),
    }


def _and(words: list[str]) -> str:
    if len(words) < 2:
        return words[0] if words else ""
    return ", ".join(words[:-1]) + " and " + words[-1]


def qualifier(types: list[str], tenure: str, years: int, present: set[str]) -> str:
    """The words after "the 22 nearby sold prices" that say which ones:
    " for semi-detached houses in the last 2 years". The types are named
    only when the choice leaves out a type the page holds, so a page of
    nothing but flats never reads "for flats"."""
    phrase = ""
    if any(t not in types for t in present):
        houses = [t for t in HOUSE_TYPES if t in types]
        nouns = []
        if len(houses) == len(HOUSE_TYPES):
            nouns.append("houses")
        elif houses:
            nouns.append(_and(houses) + " houses")
        if "flat" in types:
            nouns.append("flats")
        if "other" in types:
            nouns.append("Other sales")
        phrase = _and(nouns)
    out = ""
    if tenure in TENURES:
        out = f" for {tenure} {phrase}" if phrase else f" sold {tenure}"
    elif phrase:
        out = f" for {phrase}"
    if years:
        out += f" in the last {years} years"
    return out


def build(rows: list[dict], state: dict, today: datetime.date,
          reference_price: float | None = None) -> dict:
    """Everything the page says about the chosen set of rows. `shown` is
    one flag per row, in the page's order, for the rows the server hides
    until the reader's own script takes over."""
    types, tenure = state["types"], state["tenure"]
    base = [
        (row_type(r.get("property_type")) in types
         and (tenure == "any" or row_tenure(r.get("tenure")) == tenure))
        for r in rows
    ]
    base_dates = [(r.get("date") or "") for r, ok in zip(rows, base) if ok]
    # A year button that would keep every row of the chosen types is
    # hidden: it would look like a choice and change nothing.
    hidden_years = [
        y for y in YEAR_CHOICES
        if base_dates and all(d >= years_cutoff(y, today) for d in base_dates)
    ]
    years = 0 if state["years"] in hidden_years else state["years"]
    cutoff = years_cutoff(years, today)
    # 20 Sep 2026: a row whose amount is missing is hidden with the rest,
    # so the table can never show a row under "nothing matches these choices".
    shown = [ok and _amount(r) is not None and (cutoff is None or (r.get("date") or "") >= cutoff)
             for r, ok in zip(rows, base)]
    prices = sorted(a for r, ok in zip(rows, shown) if ok and (a := _amount(r)) is not None)
    n = len(prices)
    row_types = [row_type(r.get("property_type")) for r in rows]
    out = {
        "types": types, "tenure": tenure, "years": years, "price": state.get("price"),
        "shown": shown, "shown_count": sum(shown), "total": len(rows),
        "row_types": row_types, "row_tenures": [row_tenure(r.get("tenure")) for r in rows],
        "hidden_years": hidden_years, "count": n,
        "median": prices[n // 2] if n else None,
        "low": prices[0] if n else None, "high": prices[-1] if n else None,
        "qualifier": qualifier(types, tenure, years, set(row_types)),
        "reference_pct": None, "asking_below": None, "asking_pct": None, "asking_same": None,
    }
    if n and reference_price:
        below = sum(1 for p in prices if p < reference_price)
        out["reference_pct"] = half_up(below * 100 / n)
    price = state.get("price")
    if n and price:
        below = sum(1 for p in prices if p < price)
        out["asking_below"] = below
        out["asking_pct"] = half_up(below * 100 / n)
        out["asking_same"] = sum(1 for p in prices if p == price)
    return out
