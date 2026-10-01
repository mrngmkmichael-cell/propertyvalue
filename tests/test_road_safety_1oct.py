"""Reported road collisions near an address (1 Oct 2026).

The DfT's STATS19 returns are the only national source that says what
the junction outside a house is actually like, and they are police
reports rather than a census of every collision. So the card has two
jobs that pull against each other: give the count plainly, and never let
it read as a rate or as a complete record. These tests hold both, and
they hold the coverage line too, because Northern Ireland is not in the
file and a zero there would be a lie.

The data is a real local file, not a fake, so these also fail if the
import ever writes something the service cannot read.
"""
from app import main as app_main
from app.services import road_safety
from tests.conftest import fake_location

MANCHESTER = (53.45, -2.22)


def flat(text):
    return " ".join(text.split())


def test_the_file_is_there_and_says_what_it_holds():
    source = road_safety.source()
    assert source, "app/data/road_collisions.bin is missing or unreadable"
    assert source["name"].startswith("DfT road safety data")
    assert len(source["years"]) == 5, "the published file is five years"
    assert source["last_year"] - source["first_year"] == 4
    assert source["count"] > 400000, "half a million collisions in five years of GB"
    assert source["coverage"] == ["England", "Wales", "Scotland"]


def test_a_city_address_gets_counts_that_add_up():
    near = road_safety.near(*MANCHESTER)
    assert near["total"] == near["fatal"] + near["serious"] + near["slight"]
    assert near["killed_or_serious"] == near["fatal"] + near["serious"]
    assert near["total"] > 0, "central Manchester has reported collisions"
    assert near["casualties"] >= near["total"], "a collision has at least one casualty"
    assert near["radius_m"] == road_safety.DEFAULT_METRES


def test_nothing_is_counted_from_outside_the_circle():
    near = road_safety.near(*MANCHESTER)
    assert all(hit["metres"] <= near["radius_m"] for hit in near["worst"])
    tighter = road_safety.near(*MANCHESTER, metres=150)
    wider = road_safety.near(*MANCHESTER, metres=800)
    assert tighter["total"] <= near["total"] <= wider["total"]


def test_the_most_serious_come_first_and_the_nearest_first_within_a_severity():
    worst = road_safety.near(51.5101, -0.1340)["worst"]          # Piccadilly Circus
    assert worst and len(worst) <= road_safety.MOST_SERIOUS_SHOWN
    assert [hit["severity"] for hit in worst] == sorted(hit["severity"] for hit in worst)
    for first, second in zip(worst, worst[1:]):
        if first["severity"] == second["severity"]:
            assert first["metres"] <= second["metres"]
    for hit in worst:
        assert hit["label"] in ("Fatal", "Serious", "Slight")
        assert hit["when"], "a date a reader can place"


def test_empty_sea_reads_as_none_recorded_rather_than_no_data():
    near = road_safety.near(56.0, -4.9)      # open water in the Firth of Clyde
    assert near is not None and near["total"] == 0 and near["worst"] == []


def test_an_address_without_coordinates_gets_nothing_rather_than_a_guess():
    assert road_safety.near(None, None) is None


def test_northern_ireland_is_named_as_outside_the_file():
    gap = road_safety.outside_coverage("Northern Ireland")
    assert gap and gap["country"] == "Northern Ireland"
    assert "Police Service of Northern Ireland" in gap["body"]
    for country in ("England", "Wales", "Scotland", None, ""):
        assert road_safety.outside_coverage(country) is None


def test_the_check_is_in_the_free_list_and_in_risk_and_safety():
    titles = {check[1] for check in app_main.FREE_CHECKS}
    assert "Road Safety" in titles
    assert "Road Safety" not in {check[1] for check in app_main.PREMIUM_CHECKS}
    risk = dict(app_main.REPORT_GROUPS)["Risk & Safety"]
    assert "Road Safety" in risk
    assert len(app_main.FREE_CHECKS) + len(app_main.PREMIUM_CHECKS) == app_main.CHECK_COUNT
    assert app_main._SOURCE_BODIES["DfT road safety data"] == ("Department for Transport",)


def test_the_card_shows_the_count_and_names_the_department(client, fake_report):
    fake_report()
    body = flat(client.get("/property?postcode=M14+5TG").text)
    near = road_safety.near(*MANCHESTER)
    assert "Road Safety" in body
    assert f"{near['total']} collision" in body
    assert f"{near['fatal']} fatal, {near['serious']} serious, {near['slight']} slight" in body
    assert "DfT road safety data" in body
    assert "data.gov.uk" in body


def test_the_card_says_what_the_count_is_not(client, fake_report):
    fake_report()
    body = flat(client.get("/property?postcode=M14+5TG").text)
    assert "not every collision that happened" in body
    assert "under-reported" in body
    assert "A count is not a rate" in body
    # Never a rate, a score or a verdict on the street.
    for claim in ("collisions per", "accident rate", "dangerous road", "safety score"):
        assert claim not in body.lower()


def test_an_address_in_northern_ireland_is_told_rather_than_shown_a_zero(client, fake_report):
    fake_report(location=fake_location(country="Northern Ireland", postcode="BT1 5GS", outcode="BT1"))
    body = flat(client.get("/property?postcode=BT1+5GS").text)
    assert "Not covered for Northern Ireland" in body
    assert "Police Service of Northern Ireland" in body
    assert "None reported within" not in body
