"""How this council decides planning applications (1 Oct 2026).

Part of whether a house can be changed is the authority, not the house,
and MHCLG's live tables are the only national source that says so. The
risk in a card like this is that it reads as a league table: a council
granting 94% is not better than one granting 78%, it is a different
place. So these tests hold the figures, the one stated comparison rule,
and the sentence that refuses the verdict.

They also hold the two things the data genuinely cannot do: the
statutory-period split is published for England and never by council,
and the tables stop at the English border.
"""
from app import auth, db
from app import main as app_main
from app.services import email as email_service
from app.services import planning_decisions
from tests.test_email_verification import _signup

MANCHESTER = "E08000003"


def flat(text):
    return " ".join(text.split())


def test_the_file_is_there_and_covers_england():
    source = planning_decisions.source()
    assert source, "app/data/planning_decisions.json is missing or unreadable"
    assert source["name"] == "MHCLG planning application statistics"
    assert source["authorities"] > 250, "England has around 310 planning authorities"
    assert source["tables"] == ["P134", "P120"]
    assert source["period"].startswith("year ending")


def test_a_council_reads_as_the_table_published_it():
    council = planning_decisions.for_council(MANCHESTER)
    assert council["name"] == "Manchester"
    assert council["decisions"] > 0
    assert council["granted"] <= council["decisions"], "fewer granted than decided"
    assert council["granted_pct"] == round(100 * council["granted"] / council["decisions"], 1)
    assert 0 <= council["extension_pct"] <= 100
    assert 0 <= council["delegated_pct"] <= 100


def test_england_is_beside_the_council_and_is_not_a_council():
    council = planning_decisions.for_council(MANCHESTER)
    england = council["england"]
    assert england["decisions"] > council["decisions"]
    assert "England" not in {entry["name"] for entry
                             in planning_decisions._data["authorities"].values()}


def test_the_comparison_has_one_stated_rule_and_no_verdict():
    assert planning_decisions._against(87.5, 87.0) == "about the same as"
    assert planning_decisions._against(92.0, 87.0) == "higher than"
    assert planning_decisions._against(80.0, 87.0) == "lower than"
    assert planning_decisions._against(None, 87.0) == ""
    assert planning_decisions.SAME_WITHIN_PP == 2.0


def test_a_council_the_tables_do_not_hold_is_none_rather_than_a_guess():
    assert planning_decisions.for_council("W06000011") is None
    assert planning_decisions.for_council(None) is None
    assert planning_decisions.for_council("", "Nowhere District") is None


def test_a_differently_spelled_name_still_finds_its_council():
    assert planning_decisions.for_council("nope", "Bristol, City of")["name"] == "Bristol, City of"
    assert planning_decisions.for_council("nope", "bristol city of")["name"] == "Bristol, City of"


def test_the_statutory_split_is_england_and_says_so():
    national = planning_decisions.for_council(MANCHESTER)["national_speed"]
    assert national["major_weeks"] == 13 and national["minor_weeks"] == 8
    assert 0 < national["minor_in_time_pct"] <= 100
    assert national["quarter"], "the quarter it covers is named"


def test_the_nations_outside_england_are_named_with_who_publishes_there():
    assert planning_decisions.outside_coverage("England") is None
    assert planning_decisions.outside_coverage(None) is None
    for country, body in (("Wales", "Welsh Government"),
                          ("Scotland", "Scottish Government"),
                          ("Northern Ireland", "Department for Infrastructure")):
        gap = planning_decisions.outside_coverage(country)
        assert gap["country"] == country and body in gap["body"]


def test_the_check_is_premium_in_planning_and_heritage_and_england_only():
    assert "Planning Decisions" in {check[1] for check in app_main.PREMIUM_CHECKS}
    assert "Planning Decisions" in dict(app_main.REPORT_GROUPS)["Planning & Heritage"]
    assert app_main.PREMIUM_REACH["Planning Decisions"] == app_main._REACH_ENGLAND
    assert app_main.premium_reach_label("Planning Decisions") == "England only"
    assert app_main._SOURCE_BODIES["MHCLG planning application statistics"] == ("MHCLG",)
    assert "Planning Decisions" in app_main.LOCKED_CARD_LINES


def test_a_signed_out_report_shows_the_method_and_never_the_figure(client, fake_report):
    fake_report()
    body = flat(client.get("/property?postcode=M14+5TG").text)
    council = planning_decisions.for_council(MANCHESTER)
    assert "Planning Decisions" in body
    assert f"{council['granted_pct']}% of applications granted" not in body
    assert str(council["extension_pct"]) + "%" not in body
    assert "MHCLG's planning live tables" in body, "the method is shown, the answer is not"


def test_an_unlocked_report_shows_the_council_against_england(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, "planning-decisions@customer.test").status_code == 303
    with db.get_session() as session:
        user = auth.find_user_by_email(session, "planning-decisions@customer.test")
        user.is_premium, user.plan = True, "monthly"
        session.commit()
    fake_report()
    body = flat(client.get("/property?postcode=M14+5TG").text)
    council = planning_decisions.for_council(MANCHESTER)
    assert f"{council['granted_pct']}%" in body
    assert f"{council['england']['granted_pct']}%" in body
    assert "An extension of time was agreed" in body
    assert "A high grant rate is not a good council" in body
    assert "published for England as a whole and never by council" in body
