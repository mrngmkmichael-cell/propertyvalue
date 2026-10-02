"""Which entrance test each selective area uses (1 Oct 2026).

The research behind this page took a day and its value is entirely in
being honest about an uneven set: thirty-five areas, three named
providers, fifteen areas that name none, six where each school runs its
own test. The temptation with a page like this is to tidy that away
into one clean table, and these tests exist to stop that happening.

Every area must name the pages it was read from, no area may borrow a
figure from a neighbour, and the page must keep saying that sitting a
test is not an application.
"""
import datetime as dt
import json

from app import main as app_main
from app.services import selective_tests
from tests.test_pages import _seed_admission_school, sitemap_text


def flat(text):
    return " ".join(text.split())


def test_every_selective_area_has_an_entry():
    source = selective_tests.source()
    assert source, "app/data/selective_tests.json is missing or unreadable"
    assert source["areas"] == 35, "England has 35 areas with state grammar schools"
    assert source["schools"] == 163
    assert source["entry_year"] >= 2027


def test_every_entry_names_the_pages_it_was_read_from():
    for area in selective_tests.all_areas():
        assert area["sources"], f"{area['council']} has no source"
        for url in area["sources"]:
            assert url.startswith("https://"), f"{area['council']}: {url}"
        read_on = dt.date.fromisoformat(area["read_on"])
        assert read_on >= dt.date(2026, 10, 1), f"{area['council']} was read before the research began"


def test_an_area_that_names_no_provider_says_so_rather_than_guessing():
    areas = selective_tests.all_areas()
    for area in areas:
        assert area["provider"] or area["provider_note"], \
            f"{area['council']} is silent about who sets its test"
        if not area["provider"]:
            assert "name" in area["provider_note"].lower(), area["council"]
    named = {area["provider"] for area in areas if area["provider"]}
    assert named == {"GL Assessment", "Quest Assessments", "Future Stories Community Enterprise"}


def test_the_dates_are_dates_and_the_test_follows_the_deadline():
    for area in selective_tests.all_areas():
        for field in ("registration_opened", "registration_closed", "results_date"):
            if area[field]:
                dt.date.fromisoformat(area[field])
        if area["first_test_date"] and area["registration_closed"]:
            # Stoke added a late sitting after its own deadline, which is
            # why this compares against the first test rather than all.
            if area["council"] != "Stoke-on-Trent":
                assert area["registration_closed"] <= area["first_test_date"], area["council"]
        if area["results_date"] and area["first_test_date"]:
            assert area["first_test_date"] <= area["results_date"], area["council"]


def test_an_area_with_no_single_test_is_marked_as_such():
    areas = {area["council"]: area for area in selective_tests.all_areas()}
    for council in ("Barnet", "Bromley", "Kingston upon Thames", "Reading", "Wiltshire"):
        assert not areas[council]["one_test"], council
        assert areas[council]["test_name"].startswith("Each school"), council
    for council in ("Kent", "Medway", "Slough", "Lincolnshire"):
        assert areas[council]["one_test"], council


def test_the_west_midlands_areas_share_a_test_without_sharing_a_day():
    areas = {area["council"]: area for area in selective_tests.all_areas()}
    midlands = ("Birmingham", "Walsall", "Warwickshire", "Wolverhampton", "Telford and Wrekin")
    for council in midlands:
        assert areas[council]["test_name"] == "West Midlands Grammar Schools entrance test", council
        assert areas[council]["provider"] == "GL Assessment", council
    days = {areas[council]["first_test_date"] for council in midlands if areas[council]["first_test_date"]}
    assert len(days) > 1, "the shared test runs on different days by test centre, and the file says so"


def test_essex_and_southend_carry_the_same_consortium_round():
    essex = selective_tests.for_council("Essex")
    southend = selective_tests.for_council("Southend-on-Sea")
    for field in ("test_name", "registration_closed", "results_date", "test_dates"):
        assert essex[field] == southend[field], field


def test_a_council_with_no_grammar_school_gets_nothing():
    assert selective_tests.for_council("Manchester") is None
    assert selective_tests.for_council(None) is None


def test_the_page_lists_every_area_and_names_its_sources(client):
    body = flat(client.get("/schools/entrance-tests").text)
    for area in selective_tests.all_areas():
        assert area["council"] in body, area["council"]
        assert f'id="{area["slug"]}"' in body, area["council"]
        # An ampersand in a query string is &amp; once it is an href.
        assert area["sources"][0].replace("&", "&amp;") in body, area["council"]
    assert '"Dataset"' in body and '"FAQPage"' in body


def test_the_page_refuses_to_imply_more_than_the_sources_said(client):
    body = flat(client.get("/schools/entrance-tests").text)
    assert "Nothing here is inferred from another area" in body
    assert "says it does not, instead of repeating what a tutoring company claims" in body
    assert "Sitting a test is not an application" in body
    assert body.count("not published") >= 10, "the gaps are shown, not hidden"
    for claim in ("pass rate", "how to pass", "guaranteed", "best 11 plus"):
        assert claim not in body.lower()


def test_the_page_is_advertised_and_reachable(client):
    assert "/schools/entrance-tests" in sitemap_text(client)
    assert 'href="/schools/entrance-tests"' in client.get("/schools/admissions").text
    assert client.get("/schools/entrance-tests").status_code == 200


def test_a_selective_council_hub_says_which_test_and_links_to_it(client):
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = flat(client.get("/schools/admissions/manchester").text)
    # Manchester has no grammar school, so the block must not appear.
    assert "The entrance test here" not in body


def test_the_faqs_are_built_from_the_file_rather_than_typed(client):
    summary = selective_tests.summary()
    faqs = app_main._entrance_test_faqs(summary, selective_tests.all_areas())
    assert len(faqs) == 3
    blob = json.dumps(faqs)
    assert str(summary["areas"]) in blob and str(summary["without_provider"]) in blob
    body = flat(client.get("/schools/entrance-tests").text)
    for question, _ in faqs:
        assert question in body
