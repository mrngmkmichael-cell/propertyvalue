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
from app.services import amenities, food_hygiene, stations


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

    monkeypatch.setattr(food_hygiene, "places_near", places)
    monkeypatch.setattr(amenities.health_services, "near", lambda lat, lon, radius_m=2000: {
        "practices": [{"name": "The Doc's Surgery", "distance_m": 300}]})
    monkeypatch.setattr(amenities.bus_service, "stops_near", lambda lat, lon, radius_m=500: {
        "stops": [{"atco_code": "1800SB1", "name": "Piccadilly Station", "distance_m": 61}]})
    monkeypatch.setattr(amenities.rail_journey, "fastest_to_cities", no_journeys)
    monkeypatch.setattr(amenities.routing, "is_configured", lambda: False)


def test_the_fetch_counts_by_the_registers_and_names_what_it_covers(monkeypatch):
    _fake_sources(monkeypatch)
    result = asyncio.run(amenities._fetch_amenities_and_station(53.4776, -2.2311))
    cats = result["categories"]
    assert cats["_totals"] == {"restaurant": 1721, "supermarket": 88, "pub": 0, "gp": 1}
    assert cats["_covered"] == ["restaurant", "supermarket", "pub", "gp"]
    assert cats["pharmacy"] == [] and cats["parking"] == []
    assert cats["gp"] == [{"name": "The Doc's Surgery", "distance_m": 300, "lat": None, "lon": None}]
    assert result["stations"]["rail"]["crs"] == "MAN"
    assert result["stations_list"]["bus"] == [{"id": "1800SB1", "type": "naptan", "name": "Piccadilly Station",
                                               "network": "", "distance_m": 61}]
    assert app_main.amenity_total(cats, "restaurant") == 1721
    assert app_main.amenity_total({"restaurant": [{}, {}]}, "restaurant") == 2  # a result from before the change


def test_the_card_lists_only_what_a_register_covers_and_says_what_it_does_not(monkeypatch):
    _fake_sources(monkeypatch)
    cats = asyncio.run(amenities._fetch_amenities_and_station(53.4776, -2.2311))["categories"]
    amen = app_main.templates.get_template("_amenities.html").module
    card = str(amen.essentials_card(cats, False, False))
    assert "1,809 nearby" in " ".join(card.split())  # 1,721 + 88 + 0, pharmacies and hospitals not read
    body = " ".join(str(amen.essentials_body(cats, False, False)).split())
    assert "1,721 restaurants and cafés" in body and "88 supermarkets" in body and "1 GP surgeries" in body
    assert "car parks</summary>" not in body and "pharmacies</summary>" not in body
    assert "from the Food Standards Agency&#39;s register of food businesses" in body or \
        "from the Food Standards Agency's register of food businesses" in body
    assert "Not shown: pharmacies, dentists, hospitals, car parks" in body
    assert "OpenStreetMap." not in body.split("Not shown")[0]


def test_aspect_is_gone_and_the_count_adds_up():
    titles = [title for _, title, _, _ in app_main.FREE_CHECKS + app_main.PREMIUM_CHECKS]
    assert "Aspect" not in titles
    assert app_main.CHECK_COUNT == len(titles) == 45
    assert "Aspect" not in app_main.LOCKED_CARD_LINES and "Aspect" not in app_main.PREMIUM_REACH
    assert app_main.OPEN_DATA_SOURCES == ()
