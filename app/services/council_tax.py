"""Council tax by billing authority, UK-wide.

England: MHCLG Band D area charge per authority (by ONS code); other
bands derived with the English statutory ninths (Local Government
Finance Act 1992 s.5). Wales: Welsh Government average Band D per
authority (by name); bands A-I derived with the Welsh ninths. Scotland:
the Scottish Government publishes every band per council directly, so
those figures are used as-is (Scotland's own post-2017 multipliers are
already inside them). See scripts/import_council_tax.py for sources.
"""
import json
from pathlib import Path

_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "council_tax.json"

ENGLAND_NINTHS = {"A": 6, "B": 7, "C": 8, "D": 9, "E": 11, "F": 13, "G": 15, "H": 18}
WALES_NINTHS = {"A": 6, "B": 7, "C": 8, "D": 9, "E": 11, "F": 13, "G": 15, "H": 18, "I": 21}

try:
    _RAW = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    _RAW = {}


def _norm(name: str) -> str:
    return " ".join(name.replace("&", "and").split()).lower()


def for_district(ons_code: str | None, district_name: str | None = None) -> dict | None:
    """Council tax for a billing authority: England by ONS code,
    Scotland and Wales by the district name postcodes.io reports.
    None when the area is not in any of the three datasets."""
    year = _RAW.get("year", "")
    entry = (_RAW.get("england", {}).get("authorities", {}) or {}).get(ons_code or "")
    if entry:
        band_d = entry["band_d"]
        return {
            "authority": entry["authority"], "year": year, "band_d": band_d,
            "bands": {b: round(band_d * n / 9, 2) for b, n in ENGLAND_NINTHS.items()},
            "basis": ("Band D is the authority's published average area charge (MHCLG). Other bands "
                      "use the statutory ratios from the Local Government Finance Act 1992."),
        }
    if district_name:
        key = _norm(district_name)
        entry = (_RAW.get("scotland", {}).get("authorities", {}) or {}).get(key)
        if entry:
            return {
                "authority": entry["authority"], "year": year,
                "band_d": entry["bands"]["D"], "bands": entry["bands"],
                "basis": ("Every band as published by the Scottish Government for this council; "
                          "Scotland sets its own band multipliers."),
            }
        entry = (_RAW.get("wales", {}).get("authorities", {}) or {}).get(key)
        if entry:
            band_d = entry["band_d"]
            return {
                "authority": entry["authority"], "year": year, "band_d": band_d,
                "bands": {b: round(band_d * n / 9, 2) for b, n in WALES_NINTHS.items()},
                "basis": ("Band D is the Welsh Government's published overall average for this "
                          "authority. Other bands (A to I in Wales) use the statutory Welsh ratios."),
            }
    return None
