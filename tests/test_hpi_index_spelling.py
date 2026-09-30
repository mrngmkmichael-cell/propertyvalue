"""The House Price Index is searched by the index's own spelling of a
council's name (18 Sep 2026). postcodes.io says "Bristol, City of",
"Glasgow City" and "Aberdeen City"; the index says "City of Bristol",
"City of Glasgow" and "City of Aberdeen". The CONTAINS filter matched
nothing for either spelling of the other, so every report in those cities
had no council price row and no price trend. See hpi._PLACED_WORDING."""
import asyncio
import re

from app import main as app_main
from app.services import hpi

# The index's own labels, spelt as published for June 2026, for every
# place these tests ask about and the neighbours a substring search also
# finds. The prices are made up.
_INDEX = {
    "City of Bristol": (362000.0, 2.1),
    "City of Aberdeen": (139000.0, -3.8),
    "Aberdeenshire": (201000.0, -1.2),
    "City of Glasgow": (171000.0, 4.4),
    "City of Dundee": (148000.0, 3.0),
    "City of Kingston upon Hull": (139500.0, 2.7),
    "Kingston upon Thames": (590000.0, 3.3),
    "City of London": (812000.0, -6.1),
    "London": (556000.0, -1.0),
    "Inner London": (650000.0, -1.5),
    "Outer London": (505000.0, -0.6),
    "City of Edinburgh": (318000.0, 1.8),
    "Herefordshire": (296000.0, 0.4),
    "St Helens": (176000.0, 3.9),
    "Newry Mourne and Down": (201000.0, 6.2),
    "Armagh City Banbridge and Craigavon": (184000.0, 7.0),
    "Bournemouth Christchurch and Poole": (340000.0, 1.1),
    "City of Nottingham": (187500.0, 4.6),
    "Nottinghamshire": (268000.0, 1.9),
}


def _index(monkeypatch, areas=None):
    """The Land Registry SPARQL endpoint, faked as in _d6_index: the
    CONTAINS filter applied as the endpoint would. Returns the list of
    names each query searched for."""
    areas = _INDEX if areas is None else areas
    months = [f"{y:04d}-{m:02d}" for y in range(2016, 2027) for m in range(1, 13)]
    months = [m for m in months if "2016-07" <= m <= "2026-07"]
    searched = []

    def bindings(query):
        wanted = re.search(r'LCASE\("([^"]*)"\)', query).group(1)
        searched.append(wanted)
        matched = [label for label in areas if wanted.lower() in label.lower()]
        if "percentageAnnualChange" in query:
            return [{"label": {"value": label}, "refMonth": {"value": "2026-07"},
                     "averagePrice": {"value": str(areas[label][0])},
                     "percentageAnnualChange": {"value": str(areas[label][1])}} for label in matched]
        return [{"label": {"value": label}, "refMonth": {"value": month},
                 "averagePrice": {"value": str(areas[label][0] - 500 * (len(months) - 1 - i))}}
                for i, month in enumerate(months) for label in matched]

    class _Response:
        def __init__(self, rows):
            self.rows = rows

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": {"bindings": self.rows}}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, **k):
            return _Response(bindings(params["query"]))

    monkeypatch.setattr(hpi.httpx, "AsyncClient", lambda *a, **k: _Client())
    return searched


def _district(outcode):
    return next(o for o in app_main.ALL_OUTCODES if o["outcode"] == outcode)["district"]


def test_the_index_spelling_drops_only_the_wording_the_index_places_differently():
    for name, spelling in (
        ("Bristol, City of", "Bristol"), ("Kingston upon Hull, City of", "Kingston upon Hull"),
        ("Aberdeen City", "Aberdeen"), ("Glasgow City", "Glasgow"), ("Dundee City", "Dundee"),
        ("City of Edinburgh", "Edinburgh"), ("City of London", "London"),
        ("Herefordshire, County of", "Herefordshire"), ("St. Helens", "St Helens"),
        ("Newry, Mourne and Down", "Newry Mourne and Down"),
        ("Armagh City, Banbridge and Craigavon", "Armagh City Banbridge and Craigavon"),
        ("Bournemouth, Christchurch and Poole", "Bournemouth Christchurch and Poole"),
    ):
        assert hpi._index_spelling(name) == spelling, name
    # "City" inside a name, and "County" leading one, are the index's own
    # wording too, and names without any of it are searched as they were.
    for name in ("Derry City and Strabane", "County Durham", "Westminster", "Nottingham",
                 "Yorkshire and The Humber", "Northern Ireland", "Brighton and Hove"):
        assert hpi._index_spelling(name) == name, name


def test_the_postcodes_io_names_matched_nothing_before():
    # The fault itself, on the real names in outcodes.json: no index label
    # contains any of them, so the old search came back empty.
    for outcode in ("BS1", "AB10", "G1", "DD1", "HU1", "HR1", "WA10", "BT34", "BT60", "BH1"):
        name = _district(outcode).lower()
        assert not [label for label in _INDEX if name in label.lower()], outcode


def test_an_address_in_each_city_reads_its_citys_price_line_and_trend(monkeypatch):
    searched = _index(monkeypatch)
    for outcode, label in (
        ("BS1", "City of Bristol"), ("AB10", "City of Aberdeen"), ("G1", "City of Glasgow"),
        ("DD1", "City of Dundee"), ("HU1", "City of Kingston upon Hull"),
        ("HR1", "Herefordshire"), ("WA10", "St Helens"), ("BT34", "Newry Mourne and Down"),
        ("BT60", "Armagh City Banbridge and Craigavon"), ("BH1", "Bournemouth Christchurch and Poole"),
    ):
        district = _district(outcode)
        local = asyncio.run(hpi.area_comparison(district, "", ""))["local_authority"]
        assert local and local["name"] == label, (outcode, local)
        assert local["average_price"] == _INDEX[label][0] and local["period"] == "2026-07"
        trend = asyncio.run(hpi.price_trend(district))
        assert trend and trend["area_name"] == label, (outcode, trend and trend["area_name"])
        assert trend["current_price"] == _INDEX[label][0]
        assert [c["years"] for c in trend["changes"]] == [1, 5, 10]
    # Each was searched for by the index's spelling, never postcodes.io's.
    assert "Bristol" in searched and "Aberdeen" in searched and "Kingston upon Hull" in searched
    assert not [s for s in searched if "City of" in s or s.endswith(" City") or "," in s]


def test_aberdeen_city_is_never_aberdeenshire(monkeypatch):
    # Aberdeenshire is shorter than "City of Aberdeen", so shortest-first
    # alone would take the county. With the city's row present the city
    # step picks it; with the row missing the answer is nothing, not the
    # county under the county's name.
    _index(monkeypatch)
    assert asyncio.run(hpi.area_comparison("Aberdeen City", "", ""))["local_authority"]["name"] == "City of Aberdeen"
    _index(monkeypatch, {"Aberdeenshire": _INDEX["Aberdeenshire"]})
    assert asyncio.run(hpi.area_comparison("Aberdeen City", "", ""))["local_authority"] is None
    assert asyncio.run(hpi.price_trend("Aberdeen City")) is None


def test_the_city_of_london_stays_the_city_and_its_region_stays_london(monkeypatch):
    # "City of London" is searched as "London", which finds the region,
    # Inner and Outer London as well: the exact name still comes first.
    _index(monkeypatch)
    district = _district("EC2V")
    assert district == "City of London"
    result = asyncio.run(hpi.area_comparison(district, "London", "England"))
    assert result["local_authority"]["name"] == "City of London"
    assert result["region"]["name"] == "London"
    assert asyncio.run(hpi.price_trend(district))["area_name"] == "City of London"
    edinburgh = _district("EH1")
    assert asyncio.run(hpi.area_comparison(edinburgh, "", ""))["local_authority"]["name"] == "City of Edinburgh"


def test_names_without_the_wording_search_and_pick_as_before(monkeypatch):
    # The market report asks for bare city names, and most councils carry
    # no city wording: each sends the same search as before and picks what
    # _pick_area alone would, so no stored market snapshot is stale.
    searched = _index(monkeypatch)
    for city, _outcode in app_main.MARKET_REPORT_AREAS:
        assert hpi._index_spelling(city) == city
        labels = [label for label in _INDEX if city.lower() in label.lower()]
        assert hpi._choose(labels, city) == hpi._pick_area(labels, city), city
    asyncio.run(hpi.area_comparison("Nottingham", "", ""))
    assert searched == ["Nottingham"]
    assert asyncio.run(hpi.area_comparison("Nottingham", "", ""))["local_authority"]["name"] == "City of Nottingham"
