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


MIN_FLOOR_AREA = 15  # square metres; anything smaller is a data error, not a home
MAX_PSQM_ROWS = 8


def price_per_sqm(
    comparables: list[dict],
    subject_floor_area: float | None,
    subject_transactions: list[dict] | None = None,
    annual_growth_pct: float | None = None,
) -> dict | None:
    """Pounds per square metre from the recent nearby sales whose EPC
    floor area is known (any size, unlike the estimate above, so the
    sample is larger), each scaled for the area's growth since its sale
    like the estimate; the median and quartiles; this home's own last
    sale per square metre when its price and floor area are both known;
    and what its floor area would fetch at the local median. Sold prices
    are HM Land Registry's, floor areas the EPC Register's: nothing is
    modelled beyond the growth scaling, which the page names."""
    growth_rate = (annual_growth_pct or 0) / 100
    rows = []
    for tx in comparables or []:
        floor_area = tx.get("floor_area")
        years = _years_since(tx.get("date"))
        try:
            amount = float(tx.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        if not floor_area or floor_area < MIN_FLOOR_AREA or amount <= 0 or years is None or years > RECENT_YEARS:
            continue
        adjusted = amount * ((1 + growth_rate) ** years)
        rows.append({
            "address": tx.get("address", ""), "date": tx.get("date", ""), "amount": amount, "floor_area": floor_area,
            "per_sqm": round(adjusted / floor_area), "sold_per_sqm": round(amount / floor_area),
            "distance_m": tx.get("distance_m"),
        })
    subject = None
    if subject_floor_area and subject_floor_area >= MIN_FLOOR_AREA:
        dated = [t for t in (subject_transactions or []) if t.get("date") and t.get("amount")]
        if dated:
            last = max(dated, key=lambda t: t["date"])
            try:
                amount = float(last["amount"])
            except (TypeError, ValueError):
                amount = 0
            if amount > 0:
                subject = {"amount": amount, "date": last["date"], "year": str(last["date"])[:4], "per_sqm": round(amount / subject_floor_area), "floor_area": subject_floor_area}
    if not rows and not subject:
        return None
    values = sorted(r["per_sqm"] for r in rows)
    median = round(_percentile(values, 0.5)) if values else None
    rows.sort(key=lambda r: r["date"], reverse=True)
    return {
        "sample_size": len(rows),
        "median": median,
        "low": round(_percentile(values, 0.25)) if values else None,
        "high": round(_percentile(values, 0.75)) if values else None,
        "years_window": RECENT_YEARS,
        "rows": rows[:MAX_PSQM_ROWS],
        "subject": subject,
        "subject_floor_area": subject_floor_area,
        "implied_value": round(median * subject_floor_area, -3) if median and subject_floor_area else None,
        "subject_vs_median_pct": round((subject["per_sqm"] / median - 1) * 100) if subject and median else None,
    }

