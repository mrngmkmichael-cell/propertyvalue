"""The council's finances from the built data file: Band D history, EFS,
section 114."""
from app.services import council_finance


def test_a_supported_council_is_flagged_and_a_sound_one_is_not():
    croydon = council_finance.for_council("E09000008", "Croydon")
    assert croydon["name"] == "Croydon" and croydon["flag"] is True
    assert any(e["year"] == "2026-27" for e in croydon["efs"]) and croydon["efs_current"]
    assert len(croydon["s114"]) == 3 and croydon["s114"][0]["date"] == "2020-11-11"
    assert len(croydon["history"]) == 6 and croydon["history"][-1]["year"] == croydon["latest_year"]
    assert croydon["rise_latest"] is not None and croydon["median_rise_latest"] is not None
    bromley = council_finance.for_council("E09000006", "Bromley")
    assert bromley["flag"] is False and not bromley["efs"] and not bromley["s114"]
    assert council_finance.for_council("X99", "Nowhere") is None


def test_a_county_with_support_reaches_its_districts():
    lewes = council_finance.for_council("E07000063", "Lewes", "East Sussex")
    assert lewes and lewes["county_efs"] and lewes["county_name"] == "East Sussex" and lewes["flag"] is True
    assert council_finance.for_council("E07000063", "Lewes", "")["county_efs"] == []
