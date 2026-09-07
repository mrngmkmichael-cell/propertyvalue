"""The cost of reaching EPC Band C, read from the certificate's own
recommendation report rather than modelled."""
from app.services import epc


def _suggested():
    return [
        {"sequence": 1, "indicative_cost": "£4,000 - £14,000", "typical_saving": {"value": 207, "currency": "GBP"},
         "improvement_details": {"improvement_number": 7}, "energy_performance_rating": 66},
        {"sequence": 2, "indicative_cost": "£8,000 - £10,000", "typical_saving": {"value": 253, "currency": "GBP"},
         "improvement_details": {"improvement_number": 34}, "energy_performance_rating": 72},
        {"sequence": 3, "indicative_cost": "£100 - £200", "typical_saving": {"value": 30, "currency": "GBP"},
         "improvement_details": {"improvement_number": 5}, "energy_performance_rating": 74},
    ]


def test_bands_follow_the_sap_thresholds():
    assert [epc.band_for_score(s) for s in (92, 91, 81, 80, 69, 68, 55, 54, 39, 38, 21, 20)] == list("ABBCCDDEEFFG")
    assert epc.band_for_score(None) is None and epc.band_for_score("x") is None


def test_cost_ranges_parse_as_the_certificate_prints_them():
    assert epc.parse_cost_range("£7,500 - £11,000") == (7500, 11000)
    assert epc.parse_cost_range("£50") == (50, 50)
    assert epc.parse_cost_range("") == (None, None)


def test_the_plan_stops_at_the_first_step_that_reaches_c():
    plan = epc.improvement_plan(_suggested(), 62)
    assert plan["already_c"] is False
    assert plan["to_c"] == {"count": 2, "cost_low": 12000, "cost_high": 24000, "priced": 2, "saving": 460, "rating_after": 72, "band_after": "C"}
    assert plan["all"]["cost_high"] == 24200 and plan["all"]["band_after"] == "C" and plan["all"]["saving"] == 490
    # Names come from the imported codes file, not from us.
    assert plan["steps"][0]["name"] == "50 mm internal or external wall insulation"
    assert plan["steps"][1]["name"].startswith("Solar photovoltaic panels")


def test_already_c_and_never_c_are_told_apart():
    assert epc.improvement_plan(_suggested(), 75)["already_c"] is True
    assert epc.improvement_plan(_suggested(), 75)["to_c"] is None
    weak = [{"sequence": 1, "indicative_cost": "£300", "typical_saving": {"value": 20}, "improvement_details": {"improvement_number": 5}, "energy_performance_rating": 60}]
    plan = epc.improvement_plan(weak, 45)
    assert plan["to_c"] is None and plan["all"]["band_after"] == "D" and plan["all"]["cost_low"] == 300
    assert epc.improvement_plan([], 45) == {"steps": [], "to_c": None, "already_c": False, "all": None}
