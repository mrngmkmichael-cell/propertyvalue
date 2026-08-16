"""Mobile signal coverage, from our own `mobile_coverage` table
(populated offline by scripts/import_mobile_coverage.py) - keyed by
local authority code, which postcodes.io already returns in
location["codes"]["admin_district"].
"""
from app.db import get_session, is_configured
from app.models import MobileCoverage


def coverage_for_laua(laua_code: str) -> dict | None:
    if not is_configured() or not laua_code:
        return None
    with get_session() as session:
        row = session.get(MobileCoverage, laua_code)
        if row is None or row.coverage_4g_outdoor_all_pct is None:
            return None
        return {
            "la_name": row.la_name,
            "coverage_4g_outdoor_all_pct": row.coverage_4g_outdoor_all_pct,
            "coverage_4g_indoor_all_pct": row.coverage_4g_indoor_all_pct,
            "no_4g_outdoor_pct": row.no_4g_outdoor_pct,
            "coverage_5g_outdoor_pct": row.coverage_5g_outdoor_pct,
        }
