"""The ideas of the 1 Oct 2026 brainstorm that are not pinned elsewhere."""
import html

from app import main as app_main
from tests.test_audit_fixes_17sep import _billing, _home_block, _spend_the_free_report


def _country(monkeypatch, country):
    async def _lookup(raw):
        return {"postcode": raw, "country": country}
    monkeypatch.setattr(app_main.postcodes, "lookup_postcode", _lookup)


def test_premium_names_the_nation_of_a_home_outside_england(client, monkeypatch):
    # Account 98 met the wall on a Northern Ireland home and opened
    # /premium twice in a minute; the count for its nation was the fifth
    # answer down.
    _billing(monkeypatch)
    _spend_the_free_report(client, "1oct-ni@customer.test", "BT14 8LP", "85")
    _country(monkeypatch, "Northern Ireland")
    block = _home_block(client.get("/premium?home=BT36+4AY&hn=43").text)
    reach = app_main.premium_reach("Northern Ireland")
    assert app_main.premium_reach_sentence("Northern Ireland") in " ".join(html.unescape(block).split())
    assert f"{reach['reach']} of the {reach['total']} Premium checks" in block
    # Before the buttons, so it is read before a plan is chosen.
    assert block.index("premium-home-reach") < block.index("/premium/checkout")


def test_premium_says_nothing_extra_for_a_home_in_england(client, monkeypatch):
    _billing(monkeypatch)
    _spend_the_free_report(client, "1oct-en@customer.test", "M35 4AA", "10")
    _country(monkeypatch, "England")
    block = _home_block(client.get("/premium?home=M35+1AA&hn=7").text)
    assert "Open 7 M35 1AA in full" in block and "premium-home-reach" not in block


def test_premium_stands_when_the_lookup_fails(client, monkeypatch):
    _billing(monkeypatch)
    _spend_the_free_report(client, "1oct-down@customer.test", "M35 4AB", "11")

    async def _down(raw):
        raise RuntimeError("postcodes.io down")
    monkeypatch.setattr(app_main.postcodes, "lookup_postcode", _down)
    response = client.get("/premium?home=M35+1AA&hn=7")
    assert response.status_code == 200
    assert "Open 7 M35 1AA in full" in _home_block(response.text)


def _record_catchment_queries(monkeypatch):
    from app.services import catchment
    asked = []

    async def _query(client, url, field, lat, lon):
        asked.append(url)
        return [{"school_name": "Somewhere School", "rings": None}]
    monkeypatch.setattr(catchment, "_query_source", _query)
    monkeypatch.setattr(catchment._cache, "get", lambda key, ttl: None)
    monkeypatch.setattr(catchment._cache, "set", lambda key, value: None)
    return catchment, asked


def test_catchments_ask_only_the_councils_whose_maps_reach_the_address(monkeypatch):
    # Every report asked all 21 council layers until 1 Oct 2026, so
    # Exeter waited 11.2 s on servers that could never answer for it.
    import asyncio
    catchment, asked = _record_catchment_queries(monkeypatch)

    assert asyncio.run(catchment.catchments_for(50.7236, -3.5275)) == []  # Exeter
    assert asked == []

    matches = asyncio.run(catchment.catchments_for(53.3811, -1.4950))  # Sheffield S10
    assert {m["authority"] for m in matches} == {"Sheffield"}
    assert len(asked) == 2


def test_every_catchment_source_has_an_extent_and_stirling_is_gone():
    from app.services import catchment
    assert {a for a, _, _, _ in catchment._SOURCES} == set(catchment._EXTENTS)
    # Its layer was Dundee's school locations as points.
    assert "Stirling" not in catchment.covered_authorities()
    assert not any("SchoolsAndCatchments" in url for _, _, url, _ in catchment._SOURCES)


def test_internal_fetchers_names_families_behind_the_secret(client, monkeypatch):
    monkeypatch.setenv("ALERTS_CRON_SECRET", "s3cret")
    monkeypatch.setattr(app_main, "_agent_counts", {})
    for _ in range(3):
        app_main._record_agent("Mozilla/5.0 (compatible; Googlebot/2.1)", "/school/1/x", False)
    app_main._record_agent("Mozilla/5.0 (compatible; AhrefsBot/7.0)", "/school/2/y", False)

    assert client.get("/internal/fetchers").status_code == 404
    assert client.get("/internal/fetchers", headers={"x-alerts-secret": "wrong"}).status_code == 404
    data = client.get("/internal/fetchers", headers={"x-alerts-secret": "s3cret"}).json()
    assert data["total"] == 4
    school = next(p for p in data["pages"] if p["page"] == "/school/…")
    assert school["families"] == [["Googlebot", 3], ["AhrefsBot", 1]]
    # Families only: no user agent string and no single page.
    assert "Mozilla" not in str(data) and "/school/1" not in str(data)
