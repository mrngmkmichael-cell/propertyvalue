"""Brownfield register sites near a home, read from the planning data
platform: distance, size, homes, permission, and the council's standing."""
import asyncio

from app.services import brownfield

LAT, LON = 51.3700, 0.0900


def _entities():
    return [
        {"name": "BLR12", "reference": "BLR12", "site-address": "Small Halls York Rise, Orpington, BR6 8AD", "hectares": "0.46",
         "minimum-net-dwellings": None, "maximum-net-dwellings": "24", "planning-permission-status": "not-permissioned",
         "ownership-status": "owned-by-a-public-authority", "deliverable": "yes", "point": "POINT (0.0910 51.3720)",
         "entry-date": "2017-12-31", "end-date": "", "site-plan-url": "http://example.test/plan.jpg"},
        {"name": "BLR30", "reference": "BLR30", "site-address": "Ontario Centre, Helegan Close, BR6 9XJ", "hectares": "0.18",
         "planning-permission-status": "permissioned", "planning-permission-type": "outline-planning-permission",
         "ownership-status": "unknown-ownership", "point": "POINT (0.0880 51.3690)", "entry-date": "2019-12-31", "end-date": ""},
        {"name": "far", "site-address": "Too far", "point": "POINT (0.1300 51.3700)", "entry-date": "2020-01-01", "end-date": ""},
        {"name": "closed", "site-address": "Built out", "point": "POINT (0.0901 51.3701)", "entry-date": "2017-12-31", "end-date": "2022-01-01"},
    ]


def test_sites_within_the_radius_nearest_first_and_closed_entries_dropped():
    sites = brownfield.parse_sites(_entities(), LAT, LON)
    assert [s["reference"] for s in sites] == ["BLR30", "BLR12"]
    assert sites[0]["permission"] == "Has planning permission" and sites[0]["permission_type"] == "outline planning permission"
    assert sites[1]["max_dwellings"] == 24 and sites[1]["min_dwellings"] is None and sites[1]["ownership"] == "Public authority"
    assert 100 < sites[0]["distance_m"] < 200 and sites[1]["distance_m"] < 300


def test_the_summary_carries_the_council_and_the_flags():
    sites = brownfield.parse_sites(_entities(), LAT, LON)
    out = brownfield.summarise(sites, {"entity": 65, "name": "London Borough of Bromley"}, 88)
    assert out["count"] == 2 and out["dwellings"] == 24 and out["dwellings_stated"] == 1 and out["permissioned"] == 1
    assert out["hectares"] == 0.64 and out["newest_entry"] == "2019-12-31"
    assert out["council"] == {"name": "London Borough of Bromley", "entity": 65, "register_count": 88, "published": True}
    quiet = brownfield.summarise([], {"entity": 1, "name": "Somewhere"}, None)
    assert quiet["count"] == 0 and quiet["council"]["published"] is False


def test_the_lookup_covers_england_only_and_knows_the_councils():
    assert asyncio.run(brownfield.sites_near(51.48, -3.18, "W06000015", "Wales")) == {"covered": False, "country": "Wales"}
    assert brownfield.ORGANISATIONS["E09000006"]["name"] == "London Borough of Bromley"
    assert brownfield.ORGANISATIONS["E08000003"]["entity"] == 207
    assert brownfield.bounding_polygon(LAT, LON).startswith("POLYGON((")
