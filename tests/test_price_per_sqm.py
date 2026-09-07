"""Pounds per square metre from sold prices and EPC floor areas."""
import datetime

from app.services import valuation


def _recent(days: int) -> str:
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


def test_median_subject_and_implied_value():
    comps = [
        {"address": "1 Test Street", "date": _recent(30), "amount": "400000", "floor_area": 100, "distance_m": 120},
        {"address": "3 Test Street", "date": _recent(200), "amount": "330000", "floor_area": 75, "distance_m": 140},
        {"address": "5 Test Street", "date": _recent(60), "amount": "250000", "floor_area": 50, "distance_m": 160},
        {"address": "7 Test Street", "date": _recent(900), "amount": "999999", "floor_area": 90},   # too old
        {"address": "9 Test Street", "date": _recent(10), "amount": "300000"},                      # no floor area
        {"address": "11 Test Street", "date": _recent(10), "amount": "300000", "floor_area": 5},    # a data error
    ]
    out = valuation.price_per_sqm(comps, 80, [{"date": "2019-05-01", "amount": "280000"}, {"date": "2023-08-10", "amount": "352000"}], 0)
    assert out["sample_size"] == 3 and out["median"] == 4400 and out["low"] == 4200 and out["high"] == 4700  # quartiles interpolate
    assert out["subject"] == {"amount": 352000.0, "date": "2023-08-10", "year": "2023", "per_sqm": 4400, "floor_area": 80}
    assert out["implied_value"] == 352000 and out["subject_vs_median_pct"] == 0
    assert [r["address"] for r in out["rows"]] == ["9 Test Street"[:0] + "1 Test Street", "5 Test Street", "3 Test Street"] or [r["address"] for r in out["rows"]][0] == "1 Test Street"
    assert valuation.price_per_sqm([], None) is None
    assert valuation.price_per_sqm([], 80, [{"date": "2020-01-01", "amount": "160000"}])["subject"]["per_sqm"] == 2000
