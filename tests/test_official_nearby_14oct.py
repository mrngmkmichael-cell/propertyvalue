"""What is nearby, from official registers (14 Oct 2026).

OpenStreetMap's Overpass service began refusing this site by name, so
"Nearby essentials" and "Getting around" moved to official sources:
stations from NaPTAN (the Department for Transport's register), with
Underground lines from TfL open data; restaurants, supermarkets and pubs
from the Food Standards Agency's register of food businesses, counted by
its own totals; GP surgeries from NHS England. Aspect, built only on
OpenStreetMap's building outlines, left the checks.
"""
import asyncio

import pytest

from app import main as app_main
from app.services import amenities, food_hygiene, nhs_places, stations


def test_overpass_is_never_asked():
    assert amenities.OVERPASS_ENABLED is False
    with pytest.raises(RuntimeError, match="refuses this site"):
        asyncio.run(amenities._query_overpass("[out:json];node(1);out;"))


def test_stations_come_from_the_register_with_codes_and_lines():
    # Manchester Piccadilly, and Oxford Circus.
    near = stations.near(53.4776, -2.2311, 3000, 5)
    assert near["rail"][0]["name"] == "Manchester Piccadilly" and near["rail"][0]["crs"] == "MAN"
    assert near["rail"][0]["distance_m"] < 100
    assert near["tram"][0]["network"] == "Manchester Metrolink"
    assert [b["name"] for b in near["tram"][0]["lines"]] == ["Manchester Metrolink"]
    assert near["tube"] == []
    london = stations.near(51.5154, -0.1410, 3000, 5)
    assert london["tube"][0]["name"] == "Oxford Circus"
    assert [b["name"] for b in london["tube"][0]["lines"]] == ["Bakerloo", "Central", "Victoria"]
    assert all(len(found) <= 5 for found in london.values())


def _fake_sources(monkeypatch):
    async def places(lat, lon):
        return {
            "restaurant": {"places": [{"name": "Piccadilly Tap", "distance_m": 82, "lat": 53.47, "lon": -2.23}], "total": 1721},
            "supermarket": {"places": [{"name": "Sainsbury's", "distance_m": 116, "lat": 53.47, "lon": -2.23}], "total": 88},
            "pub": {"places": [], "total": 0},
        }

    async def no_journeys(crs):
        return None

    def nhs(lat, lon, country=None):
        if country not in (None, "England"):
            return {}
        return {"pharmacy": {"places": [{"name": "Boots", "distance_m": 445, "lat": 53.48, "lon": -2.24}], "total": 12},
                "dentist": {"places": [], "total": 0},
                "hospital": {"places": [{"name": "Manchester Royal Infirmary", "distance_m": 2350,
                                         "lat": 53.46, "lon": -2.23}], "total": 6}}

    monkeypatch.setattr(food_hygiene, "places_near", places)
    monkeypatch.setattr(nhs_places, "near", nhs)
    monkeypatch.setattr(amenities.health_services, "near", lambda lat, lon, radius_m=2000: {
        "practices": [{"name": "The Doc's Surgery", "distance_m": 300}], "within": 10})
    monkeypatch.setattr(amenities.bus_service, "stops_near", lambda lat, lon, radius_m=500: {
        "stops": [{"atco_code": "1800SB1", "name": "Piccadilly Station", "distance_m": 61}]})
    monkeypatch.setattr(amenities.rail_journey, "fastest_to_cities", no_journeys)
    monkeypatch.setattr(amenities.routing, "is_configured", lambda: False)


def test_the_fetch_counts_by_the_registers_and_names_what_it_covers(monkeypatch):
    _fake_sources(monkeypatch)
    result = asyncio.run(amenities._fetch_amenities_and_station(53.4776, -2.2311))
    cats = result["categories"]
    # The GP count is every surgery within the mile, not the three listed.
    assert cats["_totals"] == {"restaurant": 1721, "supermarket": 88, "pub": 0,
                               "pharmacy": 12, "dentist": 0, "hospital": 6, "gp": 10}
    assert cats["_covered"] == ["restaurant", "supermarket", "pub", "pharmacy", "dentist", "hospital", "gp"]
    assert cats["parking"] == [] and cats["pharmacy"][0]["name"] == "Boots"
    assert cats["gp"] == [{"name": "The Doc's Surgery", "distance_m": 300, "lat": None, "lon": None}]
    assert result["stations"]["rail"]["crs"] == "MAN"
    assert result["stations_list"]["bus"] == [{"id": "1800SB1", "type": "naptan", "name": "Piccadilly Station",
                                               "network": "", "distance_m": 61}]
    assert app_main.amenity_total(cats, "restaurant") == 1721
    assert app_main.amenity_total({"restaurant": [{}, {}]}, "restaurant") == 2  # a result from before the change


def test_the_card_lists_only_what_a_register_covers_and_says_what_it_does_not(monkeypatch):
    _fake_sources(monkeypatch)
    cats = asyncio.run(amenities._fetch_amenities_and_station(53.4776, -2.2311, country="England"))["categories"]
    amen = app_main.templates.get_template("_amenities.html").module
    card = str(amen.essentials_card(cats, False, False))
    assert "1,827 nearby" in " ".join(card.split())  # 1,721 + 88 + 12 + 0 + 6
    body = " ".join(str(amen.essentials_body(cats, False, False)).replace("&#39;", "'").split())
    for line in ("1,721 restaurants and cafés", "88 supermarkets", "12 pharmacies", "6 hospitals within three miles",
                 "10 GP surgeries", "0 dentists"):
        assert line in body, line
    assert "car parks</summary>" not in body
    assert ("Within a mile, hospitals within three miles. Restaurants and cafés, supermarkets and pubs from the "
            "Food Standards Agency's register of food businesses; pharmacies from NHS England's organisation "
            "register; dentists and hospitals from the Care Quality Commission's register; GP surgeries from "
            "NHS England.") in body
    assert "Not shown for this address" not in body
    assert "Not shown: car parks, EV charging points, parks, wind turbines and solar farms." in body


def test_outside_england_the_card_says_which_registers_do_not_cover_it(monkeypatch):
    _fake_sources(monkeypatch)
    cats = asyncio.run(amenities._fetch_amenities_and_station(55.9533, -3.1883, country="Scotland"))["categories"]
    assert cats["_covered"] == ["restaurant", "supermarket", "pub"]
    amen = app_main.templates.get_template("_amenities.html").module
    body = " ".join(str(amen.essentials_body(cats, False, False)).split())
    assert ("Not shown for this address: pharmacies, dentists, hospitals and GP surgeries. The NHS and Care "
            "Quality Commission registers read for them do not cover it.") in body
    assert "0 pharmacies" not in body and "pharmacies</summary>" not in body


def test_the_registers_hold_real_places_and_say_where_they_reach():
    manchester = nhs_places.near(53.4776, -2.2311, "England")
    assert "Manchester Royal Infirmary" in [p["name"] for p in manchester["hospital"]["places"]]
    assert manchester["pharmacy"]["total"] >= 5 and manchester["dentist"]["total"] >= 5
    assert all(p["distance_m"] <= nhs_places.RADIUS_M["hospital"] for p in manchester["hospital"]["places"])
    # Pharmacies cover Wales too; dentists and hospitals do not; Scotland has none.
    assert set(nhs_places.near(51.4816, -3.1791, "Wales")) == {"pharmacy"}
    assert nhs_places.near(55.9533, -3.1883, "Scotland") == {}
    # With no nation given, a list covers a point where it holds places near it.
    assert set(nhs_places.near(55.9533, -3.1883, None)) == set()
    assert set(nhs_places.near(53.4776, -2.2311, None)) == {"pharmacy", "dentist", "hospital"}
    # A provider's head office is not a hospital.
    names = [name for name, _, _ in nhs_places._load()["by_kind"]["hospital"]]
    assert not any("head office" in n.lower() or "headquarters" in n.lower() for n in names)


def test_the_register_is_asked_in_whole_miles():
    """A fractional limit is read by the FSA API as its 3-mile default."""
    assert food_hygiene.PLACE_RADIUS_MILES == 1 and food_hygiene.SEARCH_RADIUS_MILES == 1
    assert isinstance(food_hygiene.PLACE_RADIUS_MILES, int) and isinstance(food_hygiene.SEARCH_RADIUS_MILES, int)


def test_aspect_is_gone_and_the_count_adds_up():
    titles = [title for _, title, _, _ in app_main.FREE_CHECKS + app_main.PREMIUM_CHECKS]
    assert "Aspect" not in titles
    assert app_main.CHECK_COUNT == len(titles) == 45
    assert "Aspect" not in app_main.LOCKED_CARD_LINES and "Aspect" not in app_main.PREMIUM_REACH
    assert app_main.OPEN_DATA_SOURCES == ()
