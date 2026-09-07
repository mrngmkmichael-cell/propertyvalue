"""Whether Flood Re can stand behind this home's buildings insurance.

Flood Re is the government-backed reinsurance scheme (Water Act 2014, the
Flood Reinsurance (Scheme Funding and Administration) Regulations 2015)
that lets insurers offer affordable flood cover on homes at risk. Its
eligibility rules are public and fixed, and two of them can be checked
from data the report already holds:

- Homes built on or after 1 January 2009 are excluded, so that the
  scheme never underwrites building on floodplains. The EPC gives the
  construction year (new-build SAP certificates) or an age band (RdSAP:
  "2007 to 2011" straddles the line and is reported as uncertain).
- Buildings of four or more residential units are excluded; a flat in a
  block is insured by the freeholder's policy, so the EPC's dwelling type
  is the tell.

Also outside the scheme: homes owned by companies, commercial premises,
and (in Wales only) Council Tax band I. Nothing here is a quote: an
excluded home in Flood Zone 1 may insure easily; an eligible home in
Zone 3 may still pay a lot. The point is to say, before an offer, which
homes cannot lean on the scheme at all.
"""
import re

CUTOFF_YEAR = 2009
_YEAR = re.compile(r"(\d{4})")


def built_after_cutoff(year_built) -> bool | None:
    """True when the EPC says the home was built in 2009 or later, False
    when before, None when the age band straddles 2009 or nothing is known."""
    text = str(year_built or "").strip()
    if not text:
        return None
    if text.lower().startswith("before"):
        return False
    years = [int(y) for y in _YEAR.findall(text)]
    if not years:
        return None
    if "onwards" in text.lower():
        return years[0] >= CUTOFF_YEAR
    if len(years) == 1:
        return years[0] >= CUTOFF_YEAR
    lo, hi = min(years), max(years)
    if lo >= CUTOFF_YEAR:
        return True
    if hi < CUTOFF_YEAR:
        return False
    return None  # the band crosses 1 January 2009


def assess(year_built=None, dwelling_type: str = "", flood_zone: dict | None = None, surface_water: dict | None = None) -> dict | None:
    """The scheme's standing for this home, with the risk it would matter
    for. None when nothing at all is known about the home or the risk."""
    zone = (flood_zone or {}).get("zone")
    sw_label = (surface_water or {}).get("label") or ""
    at_risk = bool((zone and int(zone) >= 2) or sw_label in ("High risk", "Medium risk"))
    after = built_after_cutoff(year_built)
    is_flat = "flat" in (dwelling_type or "").lower() or "maisonette" in (dwelling_type or "").lower()
    if year_built in (None, "") and not is_flat and zone is None and not sw_label:
        return None

    if after is True:
        standing, headline = "excluded", "Not available: built in 2009 or later"
    elif after is None and year_built:
        standing, headline = "uncertain", "Uncertain: the EPC age band crosses 2009"
    elif is_flat:
        standing, headline = "block", "Depends on the block: flats are insured by the freeholder"
    elif after is False:
        standing, headline = "eligible", "Available: built before 2009"
    else:
        standing, headline = "unknown", "Build date not on the certificate"

    return {
        "standing": standing,
        "headline": headline,
        "at_risk": at_risk,
        "zone": zone,
        "surface_water": sw_label,
        "year_built": year_built,
        "flat": is_flat,
        # The line that decides whether a buyer should act before an offer.
        "action_needed": at_risk and standing in ("excluded", "uncertain"),
    }
