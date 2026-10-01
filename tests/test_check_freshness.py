"""The freshness check, tested on the two failures that prompted it.

On 1 October 2026 the Ofsted import was pinned to the 30 June file and
the rent import was pinned to an edition whose newest month was June.
Both passed every other check we run. These tests fix the verdicts
that would have caught them, so the check cannot quietly lose its
teeth later.

No network: the pure verdict functions are what is pinned here.
"""
import datetime as dt
import importlib

freshness = importlib.import_module("scripts.check_freshness")


def test_the_month_names_ofsted_actually_uses_all_parse():
    # The file names run Jan, Feb, ... June, July, August, Sept, Dec.
    for name, number in [("Jan", 1), ("Feb", 2), ("June", 6), ("July", 7),
                         ("August", 8), ("Sept", 9), ("Sep", 9), ("Dec", 12)]:
        assert freshness.month_number(name) == number, name


def test_an_unreadable_month_is_skipped_rather_than_guessed():
    assert freshness.month_number("Smarch") is None


def test_the_june_ofsted_pin_reads_as_behind_the_august_file():
    verdict = freshness.exact("Ofsted inspection outcomes",
                              dt.date(2026, 6, 30), dt.date(2026, 8, 31), "gov.uk")
    assert verdict["verdict"] == "BEHIND"
    # The same file on both sides is current, not behind.
    assert freshness.exact("Ofsted", dt.date(2026, 8, 31), dt.date(2026, 8, 31),
                           "gov.uk")["verdict"] == "ok"


def test_an_academic_year_behind_the_release_reads_as_behind():
    assert freshness.exact("KS4", "2023/24", "2024/25", "DfE")["verdict"] == "BEHIND"
    assert freshness.exact("KS4", "2024/25", "2024/25", "DfE")["verdict"] == "ok"


def test_rents_three_months_old_breach_the_budget_and_two_months_do_not():
    assert freshness.budget("Private rents", "2026-06", 92, 75, "ONS")["verdict"] == "BEHIND"
    assert freshness.budget("Private rents", "2026-08", 61, 75, "ONS")["verdict"] == "ok"


def test_a_budget_verdict_never_claims_to_know_the_publisher():
    row = freshness.budget("Private rents", "2026-08", 61, 75, "ONS")
    assert "not stated" in row["publisher"], "a budget is a prompt to look, not a fact"


def test_the_age_of_a_period_is_counted_from_the_first_of_that_month():
    freshness.TODAY = dt.date(2026, 10, 1)
    try:
        assert freshness.month_age("2026-08") == 61
        assert freshness.month_age("2026-06") == 122
    finally:
        freshness.TODAY = dt.date.today()
