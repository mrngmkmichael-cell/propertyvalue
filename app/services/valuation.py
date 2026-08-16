"""A rough estimate of what a property might be worth today, built
from nearby Land Registry sold comparables rather than any licensed
or proprietary automated valuation model (AVM) - there's no free
equivalent to those, so this is deliberately transparent about being
a DIY estimate from public sold-price data, not a professional
valuation or mortgage-lender-grade figure.

Method: take sold comparables within the search radius already used
by the Comparables tab, keep only recent ones (old sales are a poor
guide to today's value), inflate each by the area's latest year-on-year
HPI growth rate compounded over the years since sale, then report the
median plus an interquartile range. Comparables matching the subject
property's broad type (flat/terraced/semi/detached) are preferred when
there are enough of them, since a flat and a detached house selling
"nearby" tell you very different things.
"""
import datetime

RECENT_YEARS = 5
MIN_COMPARABLES = 5


def normalize_property_type(epc_dwelling_type: str | None) -> str | None:
    """Map an EPC dwelling_type string (e.g. 'Mid-terrace house') onto
    the same broad categories Land Registry uses (detached/semi-detached/
    terraced/flat-maisonette), so the two can be compared."""
    if not epc_dwelling_type:
        return None
    t = epc_dwelling_type.lower()
    if "flat" in t or "maisonette" in t:
        return "flat-maisonette"
    if "semi" in t:
        return "semi-detached"
    if "detached" in t:
        return "detached"
    if "terrace" in t:
        return "terraced"
    return "other"


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
    subject_property_type: str | None,
    annual_growth_pct: float | None,
) -> dict | None:
    growth_rate = (annual_growth_pct or 0) / 100

    usable = []
    for tx in comparables:
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

    matched_type = False
    if subject_property_type:
        same_type = [u for u in usable if u.get("property_type") == subject_property_type]
        if len(same_type) >= MIN_COMPARABLES:
            usable = same_type
            matched_type = True

    if len(usable) < MIN_COMPARABLES:
        return None

    amounts = sorted(u["adjusted_amount"] for u in usable)

    return {
        "estimate": round(_percentile(amounts, 0.5), -3),
        "low": round(_percentile(amounts, 0.25), -3),
        "high": round(_percentile(amounts, 0.75), -3),
        "sample_size": len(amounts),
        "matched_property_type": matched_type,
        "years_window": RECENT_YEARS,
    }
