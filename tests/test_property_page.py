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
# The watchlist only ever held addresses, and most people choose an area
# long before they have a door number. These cover the two things that
# can go wrong: a follow that leaks between accounts, and a diff that
# manufactures news out of a data refresh.


def test_following_a_district_needs_an_account_and_keeps_the_intent(client):
    """A stranger clicking Follow must land on login pointed back at the
    guide, not at a generic page that loses what they wanted."""
    client.cookies.clear()
    r = client.post("/districts/follow", data={"outcode": "M14"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/area/M14"


def test_following_a_district_twice_does_not_raise(client):
    """The button is a plain form post, so a double submit or a
    back-and-resubmit hits the unique constraint."""
    from app import auth, watchlist
    from app.db import get_session

    client.post("/signup", data={"email": "follower@example.test",
                                 "password": "correct horse battery staple"},
                follow_redirects=False)
    with get_session() as db:
        user_id = auth.find_user_by_email(db, "follower@example.test").id

    client.post("/districts/follow", data={"outcode": "m14"}, follow_redirects=False)
    client.post("/districts/follow", data={"outcode": "M14"}, follow_redirects=False)

    districts = watchlist.list_districts(user_id)
    # Stored uppercase, once, whichever case was typed.
    assert [d["outcode"] for d in districts] == ["M14"]

    client.post("/districts/unfollow", data={"outcode": "M14"}, follow_redirects=False)
    assert watchlist.list_districts(user_id) == []


def test_one_account_s_followed_district_never_shows_on_another_s_guide(client):
    """The area guide payload is cached and shared by every visitor, so
    the follow state has to be set outside it."""
    from app import auth, watchlist
    from app.db import get_session

    client.post("/signup", data={"email": "followera@example.test",
                                 "password": "correct horse battery staple"},
                follow_redirects=False)
    with get_session() as db:
        user_id = auth.find_user_by_email(db, "followera@example.test").id
    watchlist.follow_district(user_id, "M14")

    from app import main as app_main
    context = {"current_user": {"id": user_id}}
    app_main._following_district(context, "M14")
    assert context["following_district"] is True

    other = {"current_user": {"id": user_id + 9999}}
    app_main._following_district(other, "M14")
    assert other["following_district"] is False

    anonymous = {"current_user": None}
    app_main._following_district(anonymous, "M14")
    assert anonymous["following_district"] is False


def test_district_diff_stays_quiet_unless_something_really_moved():
    """A few offences either way is a data refresh, not news. Flagging
    it would train people to ignore the one that matters."""
    from app import main as app_main

    old = {"median_price": 250000, "sales_count": 40, "crime_total": 120}

    assert app_main._district_changes(old, dict(old)) == []
    assert app_main._district_changes(old, {**old, "crime_total": 128}) == []

    moved = app_main._district_changes(
        old, {"median_price": 262000, "sales_count": 43, "crime_total": 145}
    )
    assert any("Median sold price up" in c for c in moved)
    assert any("3 new sales lodged" in c for c in moved)
    assert any("Recorded crime up by 25" in c for c in moved)


def test_district_diff_never_invents_a_median_the_guide_would_not_show():
    """_district_summary drops the median below the threshold the area
    guide itself needs, so a diff must cope with it being absent rather
    than comparing against nothing."""
    from app import main as app_main

    assert app_main._district_changes({"sales_count": 3}, {"median_price": 250000, "sales_count": 4}) == [
        "1 new sale lodged with Land Registry around here"
    ]
    # A sale count that went down (a correction upstream) is not "new sales".
    assert app_main._district_changes({"sales_count": 9}, {"sales_count": 4}) == []


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
    assert "2,108" in body or "2,107" in body
    assert "1,200" in body and "Freehold" in body
    assert 'href="/running-costs"' in body

