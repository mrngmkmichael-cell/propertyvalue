"""The property report, rendered from faked upstream results (see
conftest.fake_gather). These are the behaviours that have been added
or fixed by hand and had no check until now."""
import re

from tests.conftest import fake_gather, fake_location


def _report(client, fake_report, **kw):
    fake_report(**kw)
    r = client.get("/property?postcode=M14%205TG")
    assert r.status_code == 200
    return r.text


def test_report_renders_end_to_end(client, fake_report):
    body = _report(client, fake_report)
    assert "Traceback" not in body and "TemplateAssertionError" not in body
    assert body.count('<link rel="canonical"') == 1
    assert 'content="https://' in body or 'href="http://testserver/property?postcode=M14%205TG"' in body


def test_invalid_postcode_is_a_404(client, monkeypatch):
    from app import main as app_main

    async def _none(_pc):
        return None

    monkeypatch.setattr(app_main, "lookup_postcode", _none)
    r = client.get("/property?postcode=ZZ99%209ZZ")
    assert r.status_code == 404
    assert "couldn't find that postcode" in r.text


def test_highlights_strip_shows_real_unlocked_facts(client, fake_report):
    body = _report(client, fake_report)
    assert "What stands out" in body
    values = re.findall(r'<span class="highlight-value">(.*?)</span>', body)
    assert 2 <= len(values) <= 4
    assert "79%" in values          # schools
    assert "+4.1%" in values        # area prices
    assert "Lower" in values        # crime comparison: 2 lower vs 1 higher


def test_highlights_never_leak_locked_values(client, fake_report):
    """Valuation and price trend are Premium-only. Even when present in
    the gather, they must not surface in the free highlights strip."""
    body = _report(client, fake_report, gather=fake_gather(
        valuation={"estimate": 999999, "low": 900000, "high": 1100000},
    ))
    strip = body.split("What stands out", 1)[1].split("Tap a card", 1)[0]
    assert "999,999" not in strip
    # ...while the locked card itself is still on the page (tagged, not shown)
    assert "dashboard-card-locked" in body


def test_locked_findings_stay_behind_the_lock(client, fake_report):
    """A Premium-only check that comes back flagged must not leak to an
    anonymous reader: the score's verdict deliberately excludes locked
    checks (it says "+N more with Premium" instead), so the attention
    banner, the red ring and the Check-this tag must not reveal the
    finding either, or the two counts contradict each other on the same
    screen."""
    body = _report(client, fake_report, gather=fake_gather(
        air_quality={"pollutants": [
            {"name": "no2", "label": "NO2", "value": 34.0, "who_guideline": 10, "times_guideline": 3.4},
        ]},
    ))
    assert "Air quality well above WHO guideline" not in body
    # No card may be both locked and wearing the attention ring.
    assert not re.search(r'class="dashboard-card status-attn[^"]*dashboard-card-locked', body)


def test_locked_cards_are_tagged_not_blurred(client, fake_report):
    body = _report(client, fake_report)
    assert "dashboard-card-locked" in body
    assert "Sign up: 3 free full reports" in body
    assert "blur(5px)" not in body  # the old locked-card treatment


def test_keep_exploring_links_and_figures(client, fake_report):
    body = _report(client, fake_report)
    assert 'href="/area/M14"' in body
    assert 'href="/property/comparables?postcode=M14%205TG"' in body
    assert 'href="/schools/guide?q=M14"' in body
    assert "<strong>40</strong> sales within a short walk" in body
    assert "<strong>12</strong> schools nearby" in body
    assert "<strong>+4.1%</strong> prices this year in Manchester" in body


def test_epc_hero_uses_certificate_detail(client, fake_report):
    body = _report(client, fake_report)
    assert 'class="epc-hero"' in body
    assert "Potential rating" in body
    assert "£1,099" in body and "£216" in body  # heating, hot water costs


def test_scotland_gets_the_data_gap_notice(client, fake_report):
    body = _report(client, fake_report, location=fake_location(country="Scotland", postcode="EH1 1BB", outcode="EH1"))
    assert 'id="modal-scotland-notice"' in body
    assert "British Transport Police" in body
    assert "getElementById('modal-scotland-notice')" in body and "showModal()" in body


def test_england_does_not_get_the_scotland_notice(client, fake_report):
    body = _report(client, fake_report)
    assert "modal-scotland-notice" not in body


def test_amenities_render_pending_then_arrive_by_follow_up_fetch(client, fake_report, monkeypatch):
    """Cold report: amenities cards render in a pending state and the page
    carries the follow-up fetch. The endpoint then returns the four
    fragments rendered from the same template."""
    from app import main as app_main
    from app.services import amenities as amenities_service

    body = _report(client, fake_report, gather=fake_gather(amenities_pending=True, amenities_error=False))
    assert 'id="card-amenities"' in body and "dashboard-card-pending" in body
    assert "Finding what" in body
    assert "/api/property/amenities?postcode=M14%205TG" in body

    async def _fake_fetch(lat, lon, lite=False):
        return {
            "categories": {k: [] for k in ("restaurant", "supermarket", "pharmacy", "pub", "hospital", "parking", "ev_charging", "gp", "dentist", "green_space", "wind_turbine", "solar_farm")}
            | {"supermarket": [{"name": "Test Stores", "distance_m": 120, "lat": 53.45, "lon": -2.22}]},
            "stations": {"rail": {"name": "Test Station", "distance_m": 400, "city_journeys": [{"minutes": 9, "city": "Manchester", "departs": "08:00", "arrives": "08:09", "operator": None}]}},
            "stations_list": {"rail": [{"name": "Test Station", "distance_m": 400}], "tube": [], "tram": [], "bus": []},
        }

    monkeypatch.setattr(amenities_service, "nearby_amenities_and_station", _fake_fetch)
    r = client.get("/api/property/amenities?postcode=M14%205TG")
    assert r.status_code == 200
    data = r.json()
    assert {"essentials_card", "transport_card", "essentials_body", "transport_body"} <= set(data)
    assert "1 nearby" in data["essentials_card"] and "dashboard-card-pending" not in data["essentials_card"]
    assert "9 min train to Manchester" in data["transport_card"]
    assert "Test Stores" in data["essentials_body"]
    assert 'id="transport-body"' in data["transport_body"] and "Test Station" in data["transport_body"]


def test_amenities_endpoint_rejects_bad_input(client, monkeypatch):
    from app import main as app_main
    assert client.get("/api/property/amenities").status_code == 400

    async def _none(_pc):
        return None
    monkeypatch.setattr(app_main, "lookup_postcode", _none)
    assert client.get("/api/property/amenities?postcode=ZZ99%209ZZ").status_code == 404


# Cards on the report that are not checks against an official dataset.
# The site's promise is that every figure traces back to a named public
# body, so these are excluded from the count the landing page quotes.
# Undercounting is never a credibility risk; counting an arguable card
# is.
NOT_AN_OFFICIAL_SOURCE_CHECK = {"Resident Reviews"}


def test_landing_page_check_count_matches_the_report(client, fake_report):
    """The hero says "N checks". N has to be a number a visitor can
    verify by counting cards on a real report.

    It said 23 for months, which was the count of the FREE cards
    presented as the total, and undersold the report by fourteen. The
    report page is the source of truth, so if a card is added or removed
    this fails until the headline is updated with it."""
    report = _report(client, fake_report)
    # Two cards (Nearby Essentials, Getting Around) arrive from the
    # follow-up amenities fetch and render as pending placeholders in the
    # first response, so they are already in this list.
    titles = re.findall(r'<span class="dashboard-card-title">(.*?)</span>', report)
    assert len(titles) > 30, f"only {len(titles)} cards found - has the grid changed shape?"

    for name in NOT_AN_OFFICIAL_SOURCE_CHECK:
        assert name in titles, f"{name!r} is excluded from the count but is no longer on the report"
    checks = len(titles) - len(NOT_AN_OFFICIAL_SOURCE_CHECK)

    home = client.get("/").text
    headline = re.search(
        r'<span class="stat-count" data-target="(\d+)">([\d.]+)</span> checks', home
    )
    assert headline, "hero check count not found on the landing page"
    target, shown = int(headline.group(1)), headline.group(2)

    # The visible text carries the real figure rather than a 0 that only
    # becomes right once the count-up animation runs, so a crawler or a
    # reader without JavaScript sees the truth. Both have to agree.
    assert shown == str(target), (
        f"hero renders {shown!r} but counts up to {target}: no-JS readers see the wrong number"
    )
    assert target == checks, (
        f"landing page claims {target} checks, report has {checks} "
        f"({len(titles)} cards less {sorted(NOT_AN_OFFICIAL_SOURCE_CHECK)})"
    )
