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
    assert "Sign up: 1 free full report" in body
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


def test_valuation_renders_pending_then_arrives_by_follow_up_fetch(client, fake_report, monkeypatch):
    """Cold report: the valuation card renders in a pending state and the
    page carries the follow-up fetch. Measured on production 8 Sep 2026,
    the comparables chain took 4,910, 4,822 and 4,939 ms on three cold
    reports, the slowest source every time and 1.3 to 1.5 s clear of the
    next, while 79% of report starts sat through the wait."""
    from app import main as app_main

    body = _report(client, fake_report, gather=fake_gather(
        valuation_pending=True, valuation=None, price_per_sqm=None,
        valuation_error=False, valuation_floor_area_known=False,
    ))
    assert 'id="card-valuation"' in body and "dashboard-card-pending" in body
    assert "Reading nearby sales" in body
    assert "/api/property/valuation?postcode=M14%205TG" in body

    async def _fake_comparables(lat, lon):
        return [
            {"address": "2 Test Street", "postcode": "M14 5TG", "amount": "300000",
             "date": "2025-06-01", "distance_m": 40, "floor_area": 90},
            {"address": "4 Test Street", "postcode": "M14 5TG", "amount": "320000",
             "date": "2025-08-01", "distance_m": 60, "floor_area": 92},
        ]

    monkeypatch.setattr(app_main, "_comparables_fetch", _fake_comparables)
    r = client.get("/api/property/valuation?postcode=M14%205TG")
    assert r.status_code == 200
    data = r.json()
    assert {"card", "body"} <= set(data)
    assert 'id="card-valuation"' in data["card"]
    assert "dashboard-card-pending" not in data["card"]
    assert "Reading nearby sales" not in data["card"]
    assert 'id="valuation-body"' in data["body"]


def test_valuation_endpoint_rejects_bad_input(client, monkeypatch):
    from app import main as app_main
    assert client.get("/api/property/valuation").status_code == 400

    async def _none(_pc):
        return None
    monkeypatch.setattr(app_main, "lookup_postcode", _none)
    assert client.get("/api/property/valuation?postcode=ZZ99%209ZZ").status_code == 404


def test_a_document_build_never_defers_the_valuation(client, fake_report, monkeypatch):
    """The page can fill a card in afterwards; a PDF cannot. wait_for_slow
    makes the gather wait for both slow sources, and it is what the PDF
    route passes."""
    import inspect
    from app import main as app_main

    source = inspect.getsource(app_main.property_pdf)
    assert "wait_for_slow=True" in source
    gather_src = inspect.getsource(app_main._full_property_gather)
    assert "if cached is not None or wait_for_slow:" in gather_src
    # Both slow sources honour the same flag.
    assert gather_src.count("or wait_for_slow:") == 2


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
NOT_AN_OFFICIAL_SOURCE_CHECK = {"Resident Reviews", "In the News?"}


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
    # The hero used to carry this in a tracked-caps stats row. That row
    # was removed 28 Aug 2026 as an AI-generated-landing-page tell, so
    # the trust section is now where the page commits to a number.
    headline = re.search(
        r'data-target="(\d+)"[^>]*>([\d,]+)</span></p>\s*'
        r'<p class="lx-about-stat-l">Checks per property',
        home,
    )
    assert headline, "check count not found in the trust section on the landing page"
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

    # The dek spells the same number out in words, and nothing else
    # checks it. A digit is easy to remember to update; "Forty" reads as
    # prose and would sit there wrong for months.
    words = {
        30: "Thirty", 35: "Thirty-five", 36: "Thirty-six", 37: "Thirty-seven",
        38: "Thirty-eight", 39: "Thirty-nine", 40: "Forty", 41: "Forty-one",
        42: "Forty-two", 43: "Forty-three", 44: "Forty-four", 45: "Forty-five",
        46: "Forty-six", 47: "Forty-seven", 48: "Forty-eight", 49: "Forty-nine",
        50: "Fifty",
    }
    expected = words.get(checks)
    assert expected, f"no spelled-out form known for {checks}; add it to this test"
    assert f"{expected} checks on any UK address" in home, (
        f"the dek should read {expected!r} to match the {checks} checks on the report"
    )


def test_a_shared_report_carries_the_sender_s_note(client, fake_report):
    """Buying a house is a conversation between two people. A bare link
    makes the recipient guess what they were meant to look at."""
    from app import auth, main as app_main
    from app.db import get_session
    from app.models import ShareLink

    fake_report()
    client.post("/signup", data={"email": "sharer@example.test",
                                 "password": "correct horse battery staple"},
                follow_redirects=False)
    with get_session() as db:
        user = auth.find_user_by_email(db, "sharer@example.test")
        auth.claim_unlock(db, user.id, "M14 5TG", "")

    client.post("/share", data={"postcode": "M14 5TG", "house_number": "",
                                "note": "  the one I mentioned,   look at the flood bit  "},
                follow_redirects=False)

    with get_session() as db:
        link = db.query(ShareLink).filter(ShareLink.postcode == "M14 5TG").first()
        assert link is not None
        # Whitespace collapsed, so a pasted note cannot wreck the layout.
        assert link.note == "the one I mentioned, look at the flood bit"
        token = link.token

    client.cookies.clear()          # open it as a stranger
    body = client.get(f"/s/{token}").text
    assert "the one I mentioned, look at the flood bit" in body
    assert "Someone shared this report with you" in body


def test_resharing_updates_the_note_instead_of_minting_a_second_link(client, fake_report):
    from app import auth
    from app.db import get_session
    from app.models import ShareLink

    fake_report()
    client.post("/signup", data={"email": "resharer@example.test",
                                 "password": "correct horse battery staple"},
                follow_redirects=False)
    with get_session() as db:
        user = auth.find_user_by_email(db, "resharer@example.test")
        auth.claim_unlock(db, user.id, "M14 5TG", "")

    for note in ("first note", "second note"):
        client.post("/share", data={"postcode": "M14 5TG", "house_number": "", "note": note},
                    follow_redirects=False)

    with get_session() as db:
        links = db.query(ShareLink).filter(ShareLink.user_id == user.id).all()
        assert len(links) == 1, "one property should have one share link"
        assert links[0].note == "second note"


def test_viewing_checklist_is_built_from_this_property_s_findings(client, fake_report):
    """A generic checklist off a blog is no use. Every flagged item has
    to be triggered by something this address's own report found."""
    fake_report(gather=fake_gather(
        flood_zone={"zone": 3, "label": "Zone 3 (high probability)", "source": None},
        noise={"road_db": 71, "rail_db": None, "airport_db": None},
    ))
    body = client.get("/property/checklist?postcode=M14%205TG").text
    assert "Viewing checklist" in body
    # Flood was flagged, so the flood prompt appears...
    assert "Tide marks" in body
    assert "Listen with the windows open" in body
    # ...and the always-ask items are there regardless.
    assert "Water pressure" in body


def test_viewing_checklist_never_leaks_a_locked_finding(client, fake_report):
    """It is built from the same lock-aware concern list the score uses,
    so a free reader must not learn a Premium finding from it."""
    fake_report(gather=fake_gather(
        air_quality={"pollutants": [
            {"name": "no2", "label": "NO2", "value": 34.0, "who_guideline": 10, "times_guideline": 3.4},
        ]},
    ))
    body = client.get("/property/checklist?postcode=M14%205TG").text
    assert "Air quality well above WHO guideline" not in body
    assert "The road at the front" not in body


def test_viewing_checklist_says_so_when_nothing_was_flagged(client, fake_report):
    """Filler would be worse than nothing.

    The default fixture sits in deprivation decile 3, which is itself a
    finding, so this needs a genuinely unflagged property."""
    fake_report(gather=fake_gather(deprivation={"imd_decile": 8, "la_name": "Manchester"}))
    body = client.get("/property/checklist?postcode=M14%205TG").text
    assert "Nothing specific was flagged here" in body
    assert "Water pressure" in body


# ---- Following a district -----------------------------------------------
# Removed on 7 Sep 2026 with the feature itself: saved_districts held
# zero rows across every account since launch. The four tests that
# lived here covered the follow routes, the per-account leak and the
# district diff, none of which exist any more.


def test_the_cold_report_wait_is_recorded_not_invisible(client, fake_report):
    """The middleware records only status 200, so anyone who abandoned
    during the 202 "building your report" wait left no row at all: the
    funnel read "searched, never saw a report" with nothing to say why.
    The wait is now a synthetic pageview, like the paywall moment."""
    from app.db import get_session
    from app.main import BUILDING_PATH
    from app.models import PageView

    fake_report()
    browser = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"}

    with get_session() as s:
        before = s.query(PageView).filter(PageView.path == BUILDING_PATH).count()

    r = client.get("/property?postcode=M14%205TG", headers=browser)
    assert r.status_code == 202
    assert "building" in r.text.lower()

    with get_session() as s:
        after = s.query(PageView).filter(PageView.path == BUILDING_PATH).count()
    assert after == before + 1, "the wait must leave a row"

    # The crawler path is unchanged: blocking render, no synthetic row.
    r2 = client.get("/property?postcode=M14%205TG")
    assert r2.status_code == 200
    with get_session() as s:
        assert s.query(PageView).filter(PageView.path == BUILDING_PATH).count() == after


def test_the_report_loads_no_third_party_scripts_or_styles(client, fake_report):
    """The globe intro was the one script on the site served from a CDN
    (jsdelivr), with Leaflet from unpkg beside it on dev. Self-hosted 31
    Aug 2026: a decorative intro is not worth a third-party dependency
    on every report. Google Maps is the sole exception in production and
    only when the key is configured, which in tests it is not."""
    import re

    body = _report(client, fake_report)
    for src in re.findall(r'<script[^>]+src="([^"]+)"', body):
        assert src.startswith("/"), f"off-site script: {src}"
    for href in re.findall(r'<link[^>]+rel="stylesheet"[^>]+href="([^"]+)"', body) + \
                re.findall(r'<link[^>]+href="([^"]+)"[^>]+rel="stylesheet"', body):
        assert href.startswith("/"), f"off-site stylesheet: {href}"


def test_the_wait_page_shows_what_the_district_already_knows(client, fake_report):
    """Half of the people who started a report on 2 Sep 2026 left during
    the wait. The page now carries the district's cached facts, from the
    area guide in tier 2, so there is something true to read."""
    from app import main as app_main
    from app.services import _cache

    fake_report()
    _cache.set(("area_guide", app_main.AREA_GUIDE_PAYLOAD_VERSION, "M14"), {
        "local_sales": {"enough_for_median": True, "median": 250000, "count": 40, "years": 2},
        "hpi": {"local_authority": {"name": "Manchester", "annual_change_pct": 2.9}},
        "landscape": {"good_or_better_pct": 76},
        "crime": {"total": 120, "month": "June 2026", "by_category": [{"category": "Violence and sexual offences"}]},
        "flood_zone": {"label": "Flood zone 1"},
    })
    browser = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"}
    r = client.get("/property?postcode=M14%205TG", headers=browser)
    assert r.status_code == 202
    body = r.text
    assert "M14 at a glance" in body
    assert "250,000" in body and "+2.9%" in body and "76%" in body and "Flood zone 1" in body
    assert 'href="/area/M14"' in body


def test_the_report_offers_a_free_save_under_the_score(client, fake_report):
    fake_report()
    body = client.get("/property?postcode=M14%205TG").text
    assert "Save it free" in body and 'href="/signup?next=' in body


def test_report_shows_what_it_costs_to_live_here(client, fake_report):
    """The third pillar on the report: council tax, the EPC's energy
    estimate and tenure, read from what the gather already holds."""
    from tests.conftest import fake_gather
    fake_report(gather=fake_gather(
        council_tax={"authority": "Manchester", "year": "2026-27", "band_d": 2107.5, "bands": {"D": 2107.5}},
        property_detail={"heating_cost_current": 900, "lighting_cost_current": 120, "hot_water_cost_current": 180, "year_built": 1990},
        transactions=[{"address": "1 Test St", "date": "2021-05-01", "amount": 250000, "tenure": "freehold"}],
    ))
    body = client.get("/property?postcode=M14%205TG").text
    assert "What it costs to live here" in body
    assert 'href="/running-costs?postcode=' in body  # the full table for this postcode, one click away
    assert "2,108" in body or "2,107" in body
    assert "1,200" in body and "Freehold" in body
    assert 'href="/running-costs"' in body
    assert 'href="/estate-charges/managing-agents"' not in body  # directory withdrawn 7 Sep 2026



def test_a_welsh_report_names_the_country_and_the_missing_school_data(client, monkeypatch):
    """A Welsh postcode printed "Vale of Glamorgan, None" in the byline
    (postcodes.io fills region for English regions only) and told the
    reader "No schools found nearby", which reads as an absence of
    schools rather than an absence of data. Found on 5 Sep 2026 in a
    report a real sign-up had just unlocked."""
    from app import main as app_main

    async def _lookup(_pc):
        return {
            "postcode": "CF63 4PT", "outcode": "CF63", "country": "Wales", "region": None,
            "admin_district": "Vale of Glamorgan", "latitude": 51.4, "longitude": -3.27,
            "codes": {"admin_district": "W06000014"}, "lsoa": "Vale of Glamorgan 010A",
            "msoa": "Vale of Glamorgan 010", "admin_ward": "Court",
        }

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    body = client.get("/property?postcode=CF63+4PT").text
    assert "Vale of Glamorgan, None" not in body
    assert "Vale of Glamorgan, Wales" in body
    assert "No schools found nearby." not in body
    assert "covers England only" in body and "Estyn" in body


def test_the_news_card_says_what_is_missing_and_how_to_check(client, fake_report):
    """Michael's ask of 7 Sep 2026: was this house in the news (a murder,
    a suicide, a haunting)? No source exists, so the card says so, links
    a street-level news search the reader judges for themselves, and
    gives the one step that binds a seller: the question in writing."""
    body = _report(client, fake_report)
    assert "In the News?" in body and 'id="modal-news"' in body
    assert 'data-modal-target="modal-news" data-animate>' in body  # free: no lock redirect
    assert "Sykes v Taylor-Rose" in body and "tbm=nws" in body
    # The fake record has only an address line ("1 Test Street"): the street is
    # read off it, the house number never reaches the search.
    assert "%22Test+Street%22+Manchester" in body and "Search the news for Test Street, Manchester" in body
    assert "%221+Test" not in body
    # A record with its own street and town (Land Registry writes in capitals).
    gather = fake_gather(transactions=[{"address": "FLAT 2 37 AVALON ROAD", "street": "AVALON ROAD", "town": "ORPINGTON",
                                        "postcode": "M14 5TG", "amount": "250000", "date": "2024-06-01"}])
    body = _report(client, fake_report, gather=gather)
    assert "%22Avalon+Road%22+Orpington" in body and "Search the news for Avalon Road, Orpington" in body
    # A named house with no number and no street field: postcode and district, not a guess.
    gather = fake_gather(transactions=[{"address": "Rose Cottage", "postcode": "M14 5TG", "amount": "250000", "date": "2024-06-01"}])
    body = _report(client, fake_report, gather=gather)
    assert "q=M14+5TG+Manchester&amp;tbm=nws" in body




def test_the_energy_card_prices_the_way_to_band_c(client, fake_report):
    """Idea 1 of 7 Sep 2026: the certificate's own recommendation report,
    summed to the first step that reaches C, on the card and in the modal."""
    from app.services import epc
    from tests.test_epc_plan import _suggested
    detail = dict(fake_gather()["property_detail"], current_score=62, current_band="D", potential_score=74, potential_band="C",
                  improvements=epc.improvement_plan(_suggested(), 62))
    body = _report(client, fake_report, gather=fake_gather(property_detail=detail))
    assert "£12,000 to £24,000 to reach Band C" in body            # the card
    assert "reaches C after 2 of its 3 measures" in body            # the modal
    assert "50 mm internal or external wall insulation" in body and 'class="epc-plan-c"' in body
    # A new build with nothing recommended says so instead of showing a blank.
    detail = dict(fake_gather()["property_detail"], improvements=epc.improvement_plan([], 82), current_score=82, current_band="B")
    body = _report(client, fake_report, gather=fake_gather(property_detail=detail))
    assert "lists no recommended measures" in body and "Band B, at or above C" in body


def test_development_nearby_is_a_premium_card_that_says_what_the_register_holds(client, fake_report):
    """Idea 2 of 7 Sep 2026: brownfield register sites within half a mile.
    Locked for a free reader (the flag waits behind the lock), the modal
    names each site, and a council with nothing on the platform is said."""
    from app.services import brownfield
    from tests.test_brownfield import _entities, LAT, LON
    sites = brownfield.parse_sites(_entities(), LAT, LON)
    data = brownfield.summarise(sites, {"entity": 65, "name": "London Borough of Bromley"}, 88)
    body = _report(client, fake_report, gather=fake_gather(brownfield=data))
    assert "Development Nearby" in body and 'id="modal-brownfield"' in body
    assert "2 register sites within half a mile" in body
    assert "Ontario Centre, Helegan Close" in body and "Has planning permission (outline planning permission)" in body
    assert "register holds 88 sites on the platform" in body
    # Locked: no Check-this tag or attention line leaks the finding to a free reader.
    assert "Development site on the brownfield register nearby" not in body
    quiet = brownfield.summarise([], {"entity": 1, "name": "Quiet Council"}, None)
    body = _report(client, fake_report, gather=fake_gather(brownfield=quiet))
    assert "Register not on the national platform" in body and "Quiet Council has not published" in body
    body = _report(client, fake_report, gather=fake_gather(brownfield={"covered": False, "country": "Wales"}))
    assert "England only" in body


def test_bus_service_card_leads_with_the_best_stop(client, fake_report):
    """Idea 3 of 7 Sep 2026: buses an hour at the nearest stops, Premium."""
    stop = {"atco_code": "A1", "name": "High Street", "distance_m": 120, "latitude": 53.45, "longitude": -2.22,
            "weekday_day": 96, "weekday_eve": 16, "sunday_day": 36, "weekday_day_per_hour": 8.0, "weekday_eve_per_hour": 4.0,
            "sunday_day_per_hour": 4.0, "weekday_first": "05:30", "weekday_last": "23:45", "routes": ["43", "X47"]}
    data = {"radius_m": 500, "stops": [stop], "count": 1, "nearest": stop, "best": stop, "routes": ["43", "X47"],
            "feed_date": "2026-09-07", "ref_weekday": "2026-09-08", "ref_sunday": "2026-09-13"}
    body = _report(client, fake_report, gather=fake_gather(bus_service=data))
    assert "Bus Service" in body and 'id="modal-bus"' in body
    assert "8.0 an hour, weekday daytime" in body and "8.0 buses an hour" in body
    assert "first bus 05:30, last 23:45" in body and "Routes at these stops: 43, X47" in body
    none = dict(data, stops=[], count=0, nearest=None, best=None, routes=[])
    body = _report(client, fake_report, gather=fake_gather(bus_service=none))
    assert "No stop within 500 m" in body
    assert "Few or no scheduled buses nearby" not in body  # locked: the flag waits


def test_health_services_card_shows_list_pressure_and_a_and_e(client, fake_report):
    """Idea 4 of 7 Sep 2026: patients per fully qualified GP at the nearest
    practices against the England median, and the board's A&E four-hour
    performance. Premium; the flag waits behind the lock."""
    g = {"code": "A1", "name": "High Street Surgery", "postcode": "M14 5TG", "distance_m": 220, "patients": 8744, "gp_fte": 5.2,
         "qualified_gp_fte": 3.1, "patients_per_qualified_gp": 2821, "vs_median": 1.35, "gp_source": "Fully provided", "estimated": False,
         "pcn_name": "Central PCN", "icb_code": "QOP", "icb_name": "NHS Greater Manchester Integrated Care Board"}
    t = {"org_code": "R0A", "name": "Manchester University Nhs Foundation Trust", "period": "July 2026", "type1_attendances": 30000,
         "type1_within_4h_pct": 60.0, "all_within_4h_pct": 67.5}
    data = {"radius_m": 3000, "practices": [g], "count": 1, "nearest": g, "median_patients_per_qualified_gp": 2186,
            "patients_date": "2026-08-01", "workforce_date": "2026-07-01", "icb_code": "QOP", "icb_name": g["icb_name"],
            "trusts": [t], "ae_period": "July 2026", "national_type1_within_4h_pct": 61.5, "pressure": 1.35}
    body = _report(client, fake_report, gather=fake_gather(health=data))
    assert "Health Services" in body and 'id="modal-health"' in body
    assert "2,821 patients per GP at the nearest practice" in body and "England median 2,186" in body
    assert "A&amp;E 60.0% within four hours" in body and "+35%" in body
    assert "A&amp;E four-hour performance, July 2026" in body
    assert "Nearest GP practice well above the national list size per GP" not in body  # locked: no leak


def test_flood_re_is_flagged_for_a_post_2009_home_at_risk(client, fake_report):
    """Idea 7 of 7 Sep 2026: a home built in 2009 or later in Flood Zone 2
    or 3 cannot lean on Flood Re, and the free flood card says so."""
    detail = dict(fake_gather()["property_detail"], year_built="2012 onwards", dwelling_type="Semi-detached house")
    body = _report(client, fake_report, gather=fake_gather(property_detail=detail, flood_zone={"zone": 2, "label": "Zone 2 (medium probability)", "source": "river"}))
    assert "Flood Re not available: built 2012 onwards" in body
    assert "Not available: built in 2009 or later" in body and "get a buildings insurance quote in writing before making an offer" in body
    assert "Flood Re insurance not available for this home" in body   # free card: the attention line shows
    detail = dict(fake_gather()["property_detail"], year_built="1930–1949", dwelling_type="Detached house")
    body = _report(client, fake_report, gather=fake_gather(property_detail=detail, flood_zone={"zone": 1, "label": "Zone 1 (low probability)"}))
    assert "Available: built before 2009" in body and "Flood Re not available" not in body


def test_the_council_tax_card_carries_the_councils_finances(client, fake_report):
    """Idea 6 of 7 Sep 2026: Band D bill history, exceptional financial
    support and section 114 notices on the free council tax card."""
    body = _report(client, fake_report)   # Manchester: history, no support
    assert "The council's finances" in body and "No exceptional financial support from government since 2020-21" in body
    loc = fake_location(postcode="CR0 1AA", outcode="CR0")
    loc.update(admin_district="Croydon", region="London", codes={"admin_district": "E09000008", "lsoa": "E01001000"})
    fake_report(location=loc)
    body = client.get("/property?postcode=CR0%201AA").text
    assert "Exceptional Financial Support" in body and "Section 114 notices" in body
    assert "Council under exceptional financial support or a section 114 notice" in body  # free card: the flag shows


def test_since_2011_card_names_the_biggest_mover(client, fake_report):
    """Idea 5 of 7 Sep 2026: the census neighbourhood's change since 2011,
    free, with England's change for scale."""
    rows = [{"key": "owned", "label": "Households that own their home", "short": "Owner-occupiers", "in_2011": 56.4, "in_2021": 44.4, "change": -12.0, "england_2011": 63.3, "england_2021": 61.3, "england_change": -2.0},
            {"key": "private_rented", "label": "Households renting privately", "short": "Private renting", "in_2011": 35.5, "in_2021": 45.2, "change": 9.7, "england_2011": 16.8, "england_2021": 20.5, "england_change": 3.7}]
    data = {"lsoa": "E01001000", "rows": rows, "biggest": rows[0], "residents_2011": 1267, "residents_2021": 1336, "residents_change_pct": 5.4,
            "has_2011": True, "has_2021": True, "merged_from": 1}
    body = _report(client, fake_report, gather=fake_gather(census_change=data))
    assert "Since 2011" in body and 'id="modal-census-change"' in body
    assert "Owner-occupiers down 12.0 points" in body and "1,336 residents in 2021, up 5.4% on 2011" in body
    assert "How the area changed, 2011 to 2021" in body and "+9.7 pts" in body and "+3.7 pts" in body
    body = _report(client, fake_report, gather=fake_gather(census_change=dict(data, biggest=None, has_2011=False, rows=[])))
    assert "No comparable 2011 figure" in body


def test_grammar_schools_within_reach_sit_in_the_schools_modal(client, fake_report):
    """Idea 8 of 7 Sep 2026: selective state secondaries near the home,
    with the published distance that applies after the pass mark."""
    rows = [{"urn": 900001, "name": "Testshire Grammar School", "slug": "testshire-grammar-school", "council": "Testshire", "town": "Manchester",
             "gender": "Girls", "ofsted_rating_label": "Outstanding", "ofsted_note": "", "website": "", "latitude": 53.452, "longitude": -2.222,
             "last_distance_miles": 2.4, "distance_year": "2025/26", "distance_source": "Testshire", "distance_m": 2100, "type": "Academy converter", "postcode": "M14 5TG"}]
    body = _report(client, fake_report, gather=fake_gather(grammar_schools=rows))
    assert "Grammar schools within reach" in body and "Testshire Grammar School" in body and "2.4 mi (2025/26)" in body
    assert 'href="/schools/grammar"' in body


def test_price_per_square_metre_sits_in_the_valuation_modal(client, fake_report):
    """Idea 9 of 7 Sep 2026: sold prices over EPC floor areas, for recent
    sales nearby and this home, inside the Premium valuation modal."""
    data = {"sample_size": 3, "median": 4400, "low": 4200, "high": 4800, "years_window": 1,
            "rows": [{"address": "1 Test Street", "date": "2026-06-01", "amount": 400000.0, "floor_area": 100, "per_sqm": 4050, "sold_per_sqm": 4000, "distance_m": 120}],
            "subject": {"amount": 352000.0, "date": "2023-08-10", "year": "2023", "per_sqm": 4400, "floor_area": 80},
            "subject_floor_area": 80, "implied_value": 352000, "subject_vs_median_pct": 0}
    body = _report(client, fake_report, gather=fake_gather(price_per_sqm=data, valuation={"estimate": 350000, "low": 330000, "high": 370000, "sample_size": 2, "years_window": 1, "floor_area_variance_pct": 5}))
    assert "Price per square metre" in body and "£4,400 per m²" in body and "would be worth about <strong>£352,000</strong>" in body
    assert "This home last sold at <strong>£4,400 per m²</strong>" in body and "£4,400/m² locally" in body

