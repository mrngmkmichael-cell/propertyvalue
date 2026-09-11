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
import re
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


def all_authorities() -> dict:
    """Every billing authority in the three datasets with its Band D and
    the lowest and highest bands, for the council-by-council page."""
    year = _RAW.get("year", "")
    eng = _RAW.get("england", {}) or {}
    wal = _RAW.get("wales", {}) or {}
    sco = _RAW.get("scotland", {}) or {}
    england = sorted(
        ({"authority": v["authority"], "slug": slug_for_name(v["authority"]), "band_d": v["band_d"],
          "band_a": round(v["band_d"] * 6 / 9, 2), "band_h": round(v["band_d"] * 18 / 9, 2)}
         for v in (eng.get("authorities") or {}).values() if isinstance(v, dict) and v.get("band_d")),
        key=lambda r: r["authority"],
    )
    wales = sorted(
        ({"authority": v["authority"], "slug": slug_for_name(v["authority"]), "band_d": v["band_d"],
          "band_a": round(v["band_d"] * 6 / 9, 2), "band_i": round(v["band_d"] * 21 / 9, 2)}
         for v in (wal.get("authorities") or {}).values() if isinstance(v, dict) and v.get("band_d")),
        key=lambda r: r["authority"],
    )
    scotland = sorted(
        ({"authority": v["authority"], "slug": slug_for_name(v["authority"]), "band_d": v["bands"].get("D"),
          "band_a": v["bands"].get("A"), "band_h": v["bands"].get("H")}
         for v in (sco.get("authorities") or {}).values() if isinstance(v, dict) and v.get("bands", {}).get("D")),
        key=lambda r: r["authority"],
    )
    return {"year": year, "england": england, "wales": wales, "scotland": scotland,
            "source_england": eng.get("source", ""), "source_wales": wal.get("source", ""), "source_scotland": sco.get("source", "")}


_ENGLAND_BY_NAME = {
    _norm(v["authority"]): code
    for code, v in ((_RAW.get("england", {}) or {}).get("authorities") or {}).items()
    if isinstance(v, dict) and v.get("authority")
}


def for_district(ons_code: str | None, district_name: str | None = None) -> dict | None:
    """Council tax for a billing authority: England by ONS code,
    Scotland and Wales by the district name postcodes.io reports.
    None when the area is not in any of the three datasets."""
    year = _RAW.get("year", "")
    entry = (_RAW.get("england", {}).get("authorities", {}) or {}).get(ons_code or "")
    if not entry and district_name:
        # An English council named without its code (the area guides
        # pass the name postcodes.io gives) still finds its figure.
        entry = (_RAW.get("england", {}).get("authorities", {}) or {}).get(_ENGLAND_BY_NAME.get(_norm(district_name), ""))
    if entry:
        band_d = entry["band_d"]
        return {
            "authority": entry["authority"], "slug": slug_for_name(entry["authority"]), "year": year, "band_d": band_d,
            "nation": "England",
            "bands": {b: round(band_d * n / 9, 2) for b, n in ENGLAND_NINTHS.items()},
            "basis": ("Band D is the authority's published average area charge (MHCLG). Other bands "
                      "use the statutory ratios from the Local Government Finance Act 1992."),
        }
    if district_name:
        key = _norm(district_name)
        entry = (_RAW.get("scotland", {}).get("authorities", {}) or {}).get(key)
        if entry:
            return {
                "authority": entry["authority"], "slug": slug_for_name(entry["authority"]), "year": year,
                "nation": "Scotland",
                "band_d": entry["bands"]["D"], "bands": entry["bands"],
                "basis": ("Every band as published by the Scottish Government for this council; "
                          "Scotland sets its own band multipliers."),
            }
        entry = (_RAW.get("wales", {}).get("authorities", {}) or {}).get(key)
        if entry:
            band_d = entry["band_d"]
            return {
                "authority": entry["authority"], "slug": slug_for_name(entry["authority"]), "year": year, "band_d": band_d,
                "nation": "Wales",
                "bands": {b: round(band_d * n / 9, 2) for b, n in WALES_NINTHS.items()},
                "basis": ("Band D is the Welsh Government's published overall average for this "
                          "authority. Other bands (A to I in Wales) use the statutory Welsh ratios."),
            }
    return None


# ---- One page per billing authority (8 Sep 2026) ---------------------------
# /running-costs/council-tax/<slug>: the query "council tax <town>" has real
# demand and this site holds every authority's figure, so each gets a page
# with every band, its rank in its nation and, for England, the council's
# finances. Slugs come from the published authority names.

def slug_for_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:80]


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


_PAGES: dict | None = None


def pages() -> dict:
    """slug -> entry for every authority with a Band D figure, all three
    nations, built once. Each entry carries the bands, the ratio of each
    band to Band D, and the nation it ranks within."""
    global _PAGES
    if _PAGES is not None:
        return _PAGES

    year = _RAW.get("year", "")
    built: dict[str, dict] = {}
    eng = (_RAW.get("england", {}) or {}).get("authorities") or {}
    for code, v in eng.items():
        if not isinstance(v, dict) or not v.get("band_d"):
            continue
        band_d = v["band_d"]
        built[slug_for_name(v["authority"])] = {
            "nation": "England", "code": code, "authority": v["authority"], "year": year, "band_d": band_d,
            "bands": {b: round(band_d * n / 9, 2) for b, n in ENGLAND_NINTHS.items()},
            "ratios": {b: n / 9 for b, n in ENGLAND_NINTHS.items()},
            "source": "Ministry of Housing, Communities and Local Government, live council tax tables (Band D area average, all precepts included)",
            "source_short": "MHCLG", "top_band": "H",
            "basis": "Band D is the authority's published average area charge; other bands use the statutory ratios in the Local Government Finance Act 1992, so a parish precept can move an actual bill a little either way.",
        }
    for key, v in ((_RAW.get("wales", {}) or {}).get("authorities") or {}).items():
        if not isinstance(v, dict) or not v.get("band_d"):
            continue
        band_d = v["band_d"]
        built[slug_for_name(v["authority"])] = {
            "nation": "Wales", "code": None, "authority": v["authority"], "year": year, "band_d": band_d,
            "bands": {b: round(band_d * n / 9, 2) for b, n in WALES_NINTHS.items()},
            "ratios": {b: n / 9 for b, n in WALES_NINTHS.items()},
            "source": "Welsh Government, council tax levels by billing authority (average Band D, all precepts included)",
            "source_short": "Welsh Government", "top_band": "I",
            "basis": "Band D is the Welsh Government's published overall average for this authority; the other bands, A to I, use the statutory Welsh ratios, so a community council precept can move an actual bill a little either way.",
        }
    for key, v in ((_RAW.get("scotland", {}) or {}).get("authorities") or {}).items():
        if not isinstance(v, dict) or not (v.get("bands") or {}).get("D"):
            continue
        bands = v["bands"]
        built[slug_for_name(v["authority"])] = {
            "nation": "Scotland", "code": None, "authority": v["authority"], "year": year, "band_d": bands["D"],
            "bands": dict(bands), "ratios": {b: round(x / bands["D"], 4) for b, x in bands.items()},
            "source": "Scottish Government, council tax by band and council",
            "source_short": "Scottish Government", "top_band": "H",
            "basis": "Every band as published by the Scottish Government for this council. Scotland sets its own multipliers for bands E to H, so they are not the English ratios. Water and sewerage charges, collected with council tax in Scotland, are not included.",
        }
    # Rank within the nation, highest Band D first, and the nation's median.
    for nation in ("England", "Wales", "Scotland"):
        rows = sorted((e for e in built.values() if e["nation"] == nation), key=lambda e: e["band_d"], reverse=True)
        values = sorted(e["band_d"] for e in rows)
        n = len(values)
        median = values[n // 2] if n % 2 else round((values[n // 2 - 1] + values[n // 2]) / 2, 2)
        for i, e in enumerate(rows, start=1):
            e["rank"] = i
            e["rank_label"] = ordinal(i)
            e["of"] = n
            e["nation_median"] = median
            e["highest"] = rows[0]["authority"]
            e["lowest"] = rows[-1]["authority"]
    _PAGES = built
    return built


def page(slug: str) -> dict | None:
    return pages().get(slug)


def slug_for_district(ons_code: str | None, district_name: str | None) -> str | None:
    found = for_district(ons_code, district_name)
    return found["slug"] if found else None
