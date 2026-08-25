"""Council tax by billing authority (England).

Band D area charges imported from MHCLG's annual "Council Tax levels
set by local authorities in England" release (see
scripts/import_council_tax.py). Other bands are derived using the
statutory ratios from the Local Government Finance Act 1992, which
apply uniformly across England: each band is a fixed number of ninths
of Band D. That derivation is exact, not modelled - the law defines
the ratios - but the figure for any specific property depends on its
banding, which is on the VOA listing and the seller's bill.
"""
import json
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "council_tax.json"

# Local Government Finance Act 1992, s.5: ninths of Band D.
BAND_NINTHS = {"A": 6, "B": 7, "C": 8, "D": 9, "E": 11, "F": 13, "G": 15, "H": 18}

try:
    _RAW = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    _RAW = {"year": "", "authorities": {}}


def for_district(ons_code: str | None) -> dict | None:
    """Council tax for a billing authority by ONS code, or None when
    the code is missing or outside England's dataset."""
    if not ons_code:
        return None
    entry = _RAW["authorities"].get(ons_code)
    if not entry:
        return None
    band_d = entry["band_d"]
    return {
        "authority": entry["authority"],
        "year": _RAW["year"],
        "band_d": band_d,
        "bands": {band: round(band_d * ninths / 9, 2) for band, ninths in BAND_NINTHS.items()},
    }
