"""A rough estimate of what a property might be worth today, built
from nearby Land Registry sold comparables rather than any licensed
or proprietary automated valuation model (AVM) - there's no free
equivalent to those, so this is deliberately transparent about being
a DIY estimate from public sold-price data, not a professional
valuation or mortgage-lender-grade figure.

Method: take sold comparables within the search radius already used
by the Comparables tab, keep only recent ones (old sales are a poor
guide to today's value) whose EPC floor area is within
FLOOR_AREA_VARIANCE_PCT of the subject property's own floor area -
two "nearby" sales can be a one-bed flat and a five-bed house, which
tell you nothing about each other's value even in the same postcode -
then inflate each by the area's latest year-on-year HPI growth rate
compounded over the years since sale, and report the median plus an
interquartile range.
"""
import datetime

RECENT_YEARS = 1
FLOOR_AREA_VARIANCE_PCT = 5


def _years_since(date_str: str | None) -> float | None:
    try:
        sale_date = datetime.date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return None
    return max(0.0, (datetime.date.today() - sale_date).days / 365.25)


def _percentile(sorted_values: list[float], pct: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def estimate_value(
    comparables: list[dict],
    subject_floor_area: float | None,
    annual_growth_pct: float | None,
) -> dict | None:
    if not subject_floor_area:
        return None

    growth_rate = (annual_growth_pct or 0) / 100
    margin = subject_floor_area * (FLOOR_AREA_VARIANCE_PCT / 100)
    low_area, high_area = subject_floor_area - margin, subject_floor_area + margin

    usable = []
    for tx in comparables:
        floor_area = tx.get("floor_area")
        if not floor_area or not (low_area <= floor_area <= high_area):
            continue
        years = _years_since(tx.get("date"))
        if years is None or years > RECENT_YEARS:
            continue
        try:
            amount = float(tx["amount"])
        except (TypeError, ValueError, KeyError):
            continue
        if amount <= 0:
            continue
        adjusted = amount * ((1 + growth_rate) ** years)
        usable.append({**tx, "adjusted_amount": adjusted})

    if not usable:
        return None

    amounts = sorted(u["adjusted_amount"] for u in usable)

    return {
        "estimate": round(_percentile(amounts, 0.5), -3),
        "low": round(_percentile(amounts, 0.25), -3),
        "high": round(_percentile(amounts, 0.75), -3),
        "sample_size": len(amounts),
        "years_window": RECENT_YEARS,
        "floor_area_variance_pct": FLOOR_AREA_VARIANCE_PCT,
    }
