"""Fixed-line broadband availability, from our own
`broadband_coverage` table (populated offline by
scripts/import_broadband.py) - keyed directly by full postcode
(unit level), the same canonical format postcodes.io returns.
"""
from app.db import get_session, is_configured
from app.models import BroadbandCoverage


def _band_label(gigabit_pct: float, ultrafast_pct: float, superfast_pct: float) -> str:
    if gigabit_pct >= 80:
        return "Gigabit-capable"
    if ultrafast_pct >= 80:
        return "Ultrafast"
    if superfast_pct >= 80:
        return "Superfast"
    return "Standard/limited"


def coverage_for_postcode(postcode: str) -> dict | None:
    if not is_configured() or not postcode:
        return None
    with get_session() as session:
        row = session.get(BroadbandCoverage, postcode)
        if row is None or row.gigabit_pct is None:
            return None
        return {
            "gigabit_pct": row.gigabit_pct,
            "ultrafast_pct": row.ultrafast_pct,
            "superfast_pct": row.superfast_pct,
            "below_uso_pct": row.below_uso_pct,
            "label": _band_label(row.gigabit_pct, row.ultrafast_pct, row.superfast_pct),
        }
