"""Flood Re standing from the EPC build date and dwelling type."""
from app.services import flood_re


def test_build_dates_against_the_2009_cutoff():
    assert flood_re.built_after_cutoff("2015") is True
    assert flood_re.built_after_cutoff("2012 onwards") is True
    assert flood_re.built_after_cutoff("2003–2006") is False
    assert flood_re.built_after_cutoff("Before 1900") is False
    assert flood_re.built_after_cutoff("2007–2011") is None
    assert flood_re.built_after_cutoff("") is None and flood_re.built_after_cutoff(None) is None


def test_the_standing_and_whether_action_is_needed():
    zone2 = {"zone": 2, "label": "Zone 2 (medium probability)"}
    zone1 = {"zone": 1, "label": "Zone 1 (low probability)"}
    out = flood_re.assess("2012 onwards", "Semi-detached house", zone2, None)
    assert out["standing"] == "excluded" and out["at_risk"] and out["action_needed"]
    out = flood_re.assess("2007–2011", "House", zone2, None)
    assert out["standing"] == "uncertain" and out["action_needed"]
    out = flood_re.assess("2012 onwards", "House", zone1, {"label": "Low risk"})
    assert out["standing"] == "excluded" and not out["at_risk"] and not out["action_needed"]
    out = flood_re.assess("1996–2002", "Flat", zone2, None)
    assert out["standing"] == "block" and not out["action_needed"]
    out = flood_re.assess("1930–1949", "Detached house", zone1, {"label": "High risk"})
    assert out["standing"] == "eligible" and out["at_risk"] and not out["action_needed"]
    assert flood_re.assess(None, "", None, None) is None
    assert flood_re.assess("", "House", zone2, None)["standing"] == "unknown"
