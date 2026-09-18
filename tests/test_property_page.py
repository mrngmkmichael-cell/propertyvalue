"""The property report, rendered from faked upstream results (see
conftest.fake_gather). These are the behaviours that have been added
or fixed by hand and had no check until now."""
import re

from tests.conftest import fake_gather, fake_location


def _locked_line(title):
    """The line a locked card carries, as the page writes it: the words
    live in main.py (LOCKED_CARD_LINES, 17 Sep 2026) and the template
    escapes the ampersand in a source like "A&E"."""
    from markupsafe import escape

    from app import main as app_main
    return str(escape(app_main.LOCKED_CARD_LINES[title]))


def _report(client, fake_report, **kw):
    fake_report(**kw)
    r = client.get("/property?postcode=M14%205TG")
    assert r.status_code == 200
    return r.text


def _unlocked_report(client, fake_report, email, **kw):
    """The same report read by an account that has spent its one free
    unlock on this address.

    From 17 Sep 2026 a locked card carries one neutral line saying what
    the check answers and who publishes it, and nothing it found
    (decision 8 of that day's first-visitor audit), so a locked card's
    own reading is on the page only for a reader who has unlocked it.
    The tests below that were written against a card's words therefore
    read them here, and keep their "no leak" assertions on the
    signed-out page from _report.

    Called more than once in a test, it signs up once and stays signed
    in: the unlock is per property and claim_unlock is idempotent."""
    from app import auth
    from app.db import get_session

    fake_report(**kw)
    with get_session() as db:
        known = auth.find_user_by_email(db, email) is not None
    if not known:
        r = client.post("/signup", data={"email": email, "password": "correct horse battery staple"},
                        follow_redirects=False)
        assert r.status_code == 303, "the test account could not be created"
    with get_session() as db:
        user = auth.find_user_by_email(db, email)
        assert user is not None
        assert auth.claim_unlock(db, user.id, "M14 5TG", "") is True
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
    assert "Lower" in values        # crime: 120 against the area's 150 (18 Sep 2026: totals, not categories)


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
    # Getting Around is locked signed out, so the fragment carries the
    # same neutral line the page it replaces does (17 Sep 2026).
    assert "9 min train to Manchester" not in data["transport_card"]
    assert _locked_line("Getting Around") in data["transport_card"]
    assert "Test Stores" in data["essentials_body"]
    # The body behind the locked card comes back empty, and so does the
    # list the map draws its station pins from (17 Sep 2026, item B3):
    # this reply used to carry the station, its distance and the live
    # city journey to a signed-out page, which then swapped them into a
    # pop-up it had rendered locked.
    assert data["transport_body"] == ""
    assert data["stations_list"] == {}
    assert "Test Station" not in data["transport_card"]

    # Unlocked, the same fragment carries the journey it found.
    from app import auth
    from app.db import get_session
    assert client.post("/signup", data={"email": "transport-fragment@example.test",
                                        "password": "correct horse battery staple"},
                       follow_redirects=False).status_code == 303
    with get_session() as db:
        user = auth.find_user_by_email(db, "transport-fragment@example.test")
        assert auth.claim_unlock(db, user.id, "M14 5TG", "") is True
    unlocked = client.get("/api/property/amenities?postcode=M14%205TG").json()
    card = unlocked["transport_card"]
    assert "9 min train to Manchester" in card and "dashboard-card-locked" not in card
    # And the body and the map's stations come with it.
    assert 'id="transport-body"' in unlocked["transport_body"] and "Test Station" in unlocked["transport_body"]
    assert unlocked["stations_list"]["rail"][0]["name"] == "Test Station"


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
    # Locked signed out, so the card carries its neutral line from the
    # first render rather than a working-on-it line for an answer this
    # reader will not be shown either way (17 Sep 2026). "Reading nearby
    # sales" is what an unlocked reader sees while it runs.
    assert "Reading nearby sales" not in body
    assert _locked_line("Valuation Estimate") in body
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


def test_the_plan_lists_match_what_a_signed_out_report_locks(client, fake_report):
    """FREE_CHECKS and PREMIUM_CHECKS feed the pricing page's two lists
    and the landing page's "N free, N more" line. They must be the
    report's own cards, split the way the report splits them. Until 14
    Sep 2026 the landing page typed 23 free beside the pricing page's
    26, and 23 plus 18 is not 44."""
    import html

    from app.main import CHECK_COUNT, FREE_CHECKS, PREMIUM_CHECKS

    report = _report(client, fake_report)
    free, locked = set(), set()
    for m in re.finditer(r'<(?:button|a|div)[^>]*class="dashboard-card [^"]*"[^>]*>', report):
        title = html.unescape(re.search(r'dashboard-card-title">(.*?)</span>', report[m.end():m.end() + 3000]).group(1))
        (locked if "dashboard-card-locked" in m.group(0) else free).add(title)
    free -= NOT_AN_OFFICIAL_SOURCE_CHECK

    assert {c[1] for c in FREE_CHECKS} == free
    assert {c[1] for c in PREMIUM_CHECKS} == locked
    assert len(FREE_CHECKS) + len(PREMIUM_CHECKS) == CHECK_COUNT

    home = client.get("/").text
    assert f"{len(FREE_CHECKS)} free on every report" in home
    assert f"{len(PREMIUM_CHECKS)} more with Premium" in home


def test_an_area_with_no_reviews_shows_no_reviews_card(client, fake_report):
    """The empty "No reviews yet. Be the first" card came off every
    report on 14 Sep 2026: the table has never held a row."""
    body = _report(client, fake_report)
    assert "No reviews yet. Be the first" not in body
    assert '<span class="dashboard-card-title">Resident Reviews</span>' not in body


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

    # Resident Reviews only appears once an area has a review (14 Sep
    # 2026), so it is subtracted when present rather than required.
    assert "In the News?" in titles, "'In the News?' is excluded from the count but is no longer on the report"
    checks = len(titles) - len([n for n in NOT_AN_OFFICIAL_SOURCE_CHECK if n in titles])

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

    # The hero pillar strip carries the same claim a few hundred pixels
    # higher up. On 12 Sep 2026 it read "40 checks" on the live site
    # while the trust section on the same page said 44, because it took
    # its number from a literal in main.py that the bump script never
    # touched. One page cannot hold two answers to the same question.
    from app.main import CHECK_COUNT
    assert CHECK_COUNT == checks, (
        f"main.CHECK_COUNT is {CHECK_COUNT}, report has {checks}"
    )
    assert f"{checks} checks," in home, (
        f"the hero pillar strip does not say {checks} checks"
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
    The wait is a synthetic pageview, like the paywall moment.

    Since 11 Sep 2026 it is the page's first poll that records it, not
    the 202 render. That day the 202 was rendered 144 times and produced
    2 views of a finished report, because something was requesting the
    URL and never running the page."""
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

    # Asking for the page is not a wait. Only a client that comes back
    # for the answer is.
    with get_session() as s:
        assert s.query(PageView).filter(PageView.path == BUILDING_PATH).count() == before, (
            "rendering the wait page must not record a wait on its own")

    client.get("/api/report-ready?postcode=M14%205TG", headers=browser)
    with get_session() as s:
        after = s.query(PageView).filter(PageView.path == BUILDING_PATH).count()
    assert after == before + 1, "the poll that starts the build must leave a row"

    # Polling again while the same build runs is one wait, not many.
    client.get("/api/report-ready?postcode=M14%205TG", headers=browser)
    with get_session() as s:
        assert s.query(PageView).filter(PageView.path == BUILDING_PATH).count() == after

    # The crawler path is unchanged: blocking render, no synthetic row.
    r2 = client.get("/property?postcode=M14%205TG")
    assert r2.status_code == 200
    with get_session() as s:
        assert s.query(PageView).filter(PageView.path == BUILDING_PATH).count() == after


def test_a_report_page_request_does_not_start_a_gather_on_its_own(client, fake_report,
                                                                  monkeypatch):
    """11 Sep 2026: 144 wait pages rendered against 2 finished report
    views, at 8 to 24 an hour without a pause from 23:00 the night
    before. Each 202 render started a full ~30-service gather, so one
    plain GET was enough to make the site do all of that work, on an
    instance that runs two builds at a time. The first poll starts it
    now, which costs a browser one round trip and costs a client that
    never runs the page nothing at all."""
    from app import main

    fake_report()
    browser = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"}

    spawned = []
    monkeypatch.setattr(main, "_spawn_gather",
                        lambda location, hn: spawned.append((location["postcode"], hn)))

    r = client.get("/property?postcode=M14%205TG", headers=browser)
    assert r.status_code == 202
    assert spawned == [], "the wait page itself must not start a gather"

    client.get("/api/report-ready?postcode=M14%205TG", headers=browser)
    assert len(spawned) == 1, "the first poll starts it"


def test_a_crawler_is_not_held_for_a_whole_cold_build(client, fake_report, monkeypatch):
    """11 Sep 2026, production, with a crawler user agent: M1 1AE took
    25.39 s and LS6 3AA 21.52 s, against 0.32 s for the same address
    warm. On a two-worker instance that is a request a person is queued
    behind, and the report page is noindex, follow in any case, so the
    blocking render was buying link discovery rather than a place in the
    index. Past the deadline the crawler gets the same interim page a
    person sees, with the district's real figures on it."""
    import asyncio

    from app import main

    fake_report()

    async def _never(*_args, **_kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr(main, "CRAWLER_RENDER_DEADLINE_S", 0.3)
    monkeypatch.setattr(main, "_gather_with_cap", _never)

    r = client.get("/property?postcode=M14+5TG", headers={"User-Agent": "Googlebot/2.1"})
    assert r.status_code == 200
    assert "building" in r.text.lower()
    # Still noindex, so an interim page cannot take a report's place.
    assert 'content="noindex, follow"' in r.text


def test_a_crawler_never_takes_a_build_slot_from_a_person(monkeypatch):
    """Both slots busy means people are already waiting for a build. A
    crawler starts nothing of its own then, and is handed the district
    page instead of being queued in front of them."""
    import asyncio

    from app import main
    from app.services import _cache

    _cache._store.clear()
    _cache._bytes = 0
    main._gather_progress.clear()

    spawned = []
    monkeypatch.setattr(main, "_spawn_gather", lambda location, hn: spawned.append(1))

    async def go():
        async with main._GATHER_CONCURRENCY:
            async with main._GATHER_CONCURRENCY:
                assert main._GATHER_CONCURRENCY.locked()
                return await main._gather_within_deadline({"postcode": "M14 5TG"}, "")

    assert asyncio.run(go()) is False
    assert spawned == [], "a crawler must not start a gather while people are waiting"


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


def test_the_wait_page_draws_the_sources_not_the_district(client, fake_report):
    """Half of the people who started a report on 2 Sep 2026 left during
    the wait, and until 14 Sep the page answered with a box of the
    district's area guide figures. It read as clutter and came out. The
    wait now has one moving object: a dial with a mark per source, in
    the checklist's order, which fills as each source really comes back.
    A district with a built guide must not bring the box back."""
    import html
    import re

    from app import main as app_main
    from app.services import _cache

    fake_report()
    _cache.set(("area_guide", app_main.AREA_GUIDE_PAYLOAD_VERSION, "M14"), {
        "local_sales": {"enough_for_median": True, "median": 250000, "count": 40, "years": 2},
        "flood_zone": {"label": "Flood zone 1"},
    })
    browser = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"}
    r = client.get("/property?postcode=M14%205TG", headers=browser)
    assert r.status_code == 202
    body = r.text
    assert "M14 at a glance" not in body
    assert "250,000" not in body and "Flood zone 1" not in body
    # The guide is linked once, as the way out offered after a minute's
    # wait and hidden until then (18 Sep 2026, first-visitor audit D5),
    # never as the box's footer.
    assert body.count('href="/area/M14"') == 1
    assert re.search(r'<div class="building-help" id="building-help"[^>]*hidden>.*?href="/area/M14"', body, re.S)

    expected = [html.escape(s) for s in app_main.GATHER_SOURCE_ORDER]
    assert re.findall(r'<rect class="dial-mark" data-source="([^"]+)"', body) == expected
    assert re.findall(r'<li class="building-item" data-source="([^"]+)"', body) == expected


def test_the_wait_dial_holds_still_for_reduced_motion():
    """DESIGN.md: prefers-reduced-motion is respected on every animation.
    The dial is the wait page's only moving object, so it is the one to
    pin."""
    import re
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    still = re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", css, re.S)
    for selector in (".dial-mark", ".dial-lens", ".building-latest"):
        assert any(selector in block for block in still), selector


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
    # The full table for this postcode, one click away. The link read
    # {{ postcode }}, which the report never sets, so on every report
    # it said "The full running-costs table for : every year" and
    # opened an empty form (found live on M1 1AE, 14 Sep 2026).
    assert 'href="/running-costs?postcode=M14%205TG' in body
    assert "running-costs table for M14 5TG:" in body
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
    # Locked: no Check-this tag or attention line leaks the finding to a
    # free reader, and since 17 Sep 2026 the card's own count does not
    # either: it says what the check answers and who publishes it.
    assert "Development site on the brownfield register nearby" not in body
    assert "2 register sites within half a mile" not in body
    assert _locked_line("Development Nearby") in body

    full = _unlocked_report(client, fake_report, "brownfield@example.test", gather=fake_gather(brownfield=data))
    assert "2 register sites within half a mile" in full
    assert "Ontario Centre, Helegan Close" in full and "Has planning permission (outline planning permission)" in full
    assert "register holds 88 sites on the platform" in full
    quiet = brownfield.summarise([], {"entity": 1, "name": "Quiet Council"}, None)
    full = _unlocked_report(client, fake_report, "brownfield@example.test", gather=fake_gather(brownfield=quiet))
    assert "Register not on the national platform" in full and "Quiet Council has not published" in full
    full = _unlocked_report(client, fake_report, "brownfield@example.test", gather=fake_gather(brownfield={"covered": False, "country": "Wales"}))
    assert "England only" in full


def test_bus_service_card_leads_with_the_best_stop(client, fake_report):
    """Idea 3 of 7 Sep 2026: buses an hour at the nearest stops, Premium."""
    stop = {"atco_code": "A1", "name": "High Street", "distance_m": 120, "latitude": 53.45, "longitude": -2.22,
            "weekday_day": 96, "weekday_eve": 16, "sunday_day": 36, "weekday_day_per_hour": 8.0, "weekday_eve_per_hour": 4.0,
            "sunday_day_per_hour": 4.0, "weekday_first": "05:30", "weekday_last": "23:45", "routes": ["43", "X47"]}
    data = {"radius_m": 500, "stops": [stop], "count": 1, "nearest": stop, "best": stop, "routes": ["43", "X47"],
            "feed_date": "2026-09-07", "ref_weekday": "2026-09-08", "ref_sunday": "2026-09-13"}
    body = _report(client, fake_report, gather=fake_gather(bus_service=data))
    assert "Bus Service" in body and 'id="modal-bus"' in body
    # Locked, the card says what the check answers, not the service it
    # found (17 Sep 2026).
    assert "8.0 an hour, weekday daytime" not in body
    assert _locked_line("Bus Service") in body
    none = dict(data, stops=[], count=0, nearest=None, best=None, routes=[])
    body = _report(client, fake_report, gather=fake_gather(bus_service=none))
    assert "Few or no scheduled buses nearby" not in body  # locked: the flag waits

    # Unlocked, the card and its pop-up read the timetable. Signed in
    # from here: the signed-out checks above run first on purpose.
    full = _unlocked_report(client, fake_report, "buses@example.test", gather=fake_gather(bus_service=data))
    assert "8.0 an hour, weekday daytime" in full and "8.0 buses an hour" in full
    assert "first bus 05:30, last 23:45" in full and "Routes at these stops: 43, X47" in full
    full = _unlocked_report(client, fake_report, "buses@example.test", gather=fake_gather(bus_service=none))
    assert "No stop within 500 m" in full


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
    assert "Nearest GP practice well above the national list size per GP" not in body  # locked: no leak
    # Locked, the card says what the check answers and who publishes it,
    # not the list size it found (17 Sep 2026).
    assert "2,821 patients per GP at the nearest practice" not in body
    assert _locked_line("Health Services") in body

    full = _unlocked_report(client, fake_report, "gp@example.test", gather=fake_gather(health=data))
    assert "2,821 patients per GP at the nearest practice" in full and "England median 2,186" in full
    assert "A&amp;E 60.0% within four hours" in full and "+35%" in full
    assert "A&amp;E four-hour performance, July 2026" in full


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
    # Free card: the flag shows, in plain words since 18 Sep 2026 (D3 in
    # test_audit_fixes_17sep.py). Croydon's support for this year is current.
    assert "Croydon council needed exceptional government support for " in body


def test_since_2011_card_names_the_biggest_mover(client, fake_report):
    """Idea 5 of 7 Sep 2026: the census neighbourhood's change since 2011,
    free, with England's change for scale."""
    rows = [{"key": "owned", "label": "Households that own their home", "short": "Owner-occupiers", "in_2011": 56.4, "in_2021": 44.4, "change": -12.0, "england_2011": 63.3, "england_2021": 61.3, "england_change": -2.0},
            {"key": "private_rented", "label": "Households renting privately", "short": "Private renting", "in_2011": 35.5, "in_2021": 45.2, "change": 9.7, "england_2011": 16.8, "england_2021": 20.5, "england_change": 3.7}]
    data = {"lsoa": "E01001000", "rows": rows, "biggest": rows[0], "residents_2011": 1267, "residents_2021": 1336, "residents_change_pct": 5.4,
            "has_2011": True, "has_2021": True, "merged_from": 1}
    body = _report(client, fake_report, gather=fake_gather(census_change=data))
    assert "Since 2011" in body and 'id="modal-census-change"' in body
    assert "Owner-occupiers down 12.0 percentage points" in body and "1,336 residents in 2021, up 5.4% on 2011" in body
    assert "How the area changed, 2011 to 2021" in body and "+9.7 pts" in body and "+3.7 pts" in body
    body = _report(client, fake_report, gather=fake_gather(census_change=dict(data, biggest=None, has_2011=False, rows=[])))
    assert "No comparable 2011 figure" in body


def test_grammar_schools_within_reach_sit_in_the_schools_modal(client, fake_report):
    """Idea 8 of 7 Sep 2026: selective state secondaries near the home,
    with the published distance that applies after the pass mark."""
    rows = [{"urn": 900001, "name": "Testshire Grammar School", "slug": "testshire-grammar-school", "council": "Testshire", "town": "Manchester",
             "gender": "Girls", "ofsted_rating_label": "Outstanding", "ofsted_note": "", "website": "", "latitude": 53.452, "longitude": -2.222,
             "last_distance_miles": 2.4, "distance_year": "2025/26", "distance_source": "Testshire", "distance_m": 2100, "type": "Academy converter", "postcode": "M14 5TG",
             "has_page": True},
            {"urn": 900093, "name": "Unpublished Grammar School", "slug": "unpublished-grammar-school", "council": "Testshire", "town": "Manchester",
             "gender": "Mixed", "ofsted_rating_label": "Good", "ofsted_note": "", "website": "", "latitude": 53.454, "longitude": -2.224,
             "last_distance_miles": None, "distance_year": "", "distance_source": "", "distance_m": 2600, "type": "Foundation school", "postcode": "M14 5TG",
             "has_page": False}]
    body = _report(client, fake_report, gather=fake_gather(grammar_schools=rows))
    assert "Grammar schools within reach" in body and "Testshire Grammar School" in body and "2.4 mi (2025/26)" in body
    assert 'href="/schools/grammar"' in body
    # Only a school with a published distance has a page to link to
    # (15 Sep 2026); the other is named, not sent to "Page not found".
    assert 'href="/school/900001/testshire-grammar-school"' in body
    assert "Unpublished Grammar School" in body and 'href="/school/900093/' not in body


def test_price_per_square_metre_sits_in_the_valuation_modal(client, fake_report):
    """Idea 9 of 7 Sep 2026: sold prices over EPC floor areas, for recent
    sales nearby and this home, inside the Premium valuation modal.

    Read here by an account that has unlocked this address: from 17 Sep
    2026 (item B3 of that day's audit) a locked pop-up renders its
    method and no figure at all, so these are on the page only for a
    reader who can see them."""
    data = {"sample_size": 3, "median": 4400, "low": 4200, "high": 4800, "years_window": 1,
            "rows": [{"address": "1 Test Street", "date": "2026-06-01", "amount": 400000.0, "floor_area": 100, "per_sqm": 4050, "sold_per_sqm": 4000, "distance_m": 120}],
            "subject": {"amount": 352000.0, "date": "2023-08-10", "year": "2023", "per_sqm": 4400, "floor_area": 80},
            "subject_floor_area": 80, "implied_value": 352000, "subject_vs_median_pct": 0}
    gather = fake_gather(price_per_sqm=data, valuation={"estimate": 350000, "low": 330000, "high": 370000, "sample_size": 2, "years_window": 1, "floor_area_variance_pct": 5})
    body = _unlocked_report(client, fake_report, "price-per-sqm@customer.test", gather=gather)
    assert "Price per square metre" in body and "£4,400 per m²" in body and "would be worth about <strong>£352,000</strong>" in body
    assert "This home last sold at <strong>£4,400 per m²</strong>" in body and "£4,400/m² locally" in body


def test_the_report_cards_sit_in_the_group_board(client, fake_report):
    """14 Sep 2026: the report opens on its groups, and a tile opens each
    group's cards. The groups are built in the browser from the server's
    full list, so the server's HTML must still carry every heading and
    every card, with no grid hidden: a reader without JavaScript gets the
    whole report, as before."""
    body = _report(client, fake_report)
    assert 'class="report-categories" id="report-categories"' in body
    region = body.split('id="report-categories"', 1)[1].split("The report's groups as tiles that open", 1)[0]
    headings = re.findall(r'<h3 class="dashboard-category-heading">(.*?)</h3>', region)
    assert headings == [
        "Value &amp; Market", "Property &amp; Condition", "Risk &amp; Safety",
        "Planning &amp; Heritage", "Location &amp; Connectivity", "Area &amp; Community",
    ]
    in_region = len(re.findall(r'<span class="dashboard-card-title">', region))
    assert in_region > 30 and in_region == len(re.findall(r'<span class="dashboard-card-title">', body))
    assert not re.search(r'<div class="dashboard-grid"[^>]*\bhidden', body)
    assert 'class="section-sub report-cards-hint"' in body
    assert "cat-board" in body and "cat-toggle" not in body
    # The phone bar (16 Sep 2026) is built into every panel and pinned by CSS.
    assert "cat-panel-bar" in body
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    bar_at = css.index(".cat-panel-bar {\n        display: flex;")
    assert "position: sticky" in css[bar_at:bar_at + 400]


def test_the_group_board_holds_still_for_reduced_motion():
    """DESIGN.md: prefers-reduced-motion is respected on every animation.
    The tiles rise in and a group's cards pop in; both must stand still."""
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    still = re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", css, re.S)
    for selector in (".cat-panel .dashboard-card", ".cat-board.is-shown .cat-tile", ".cat-tile-chevron",
                     ".cat-tile-seen.is-new", ".cat-panel .dashboard-card.status-attn::after", ".cat-complete"):
        assert any(selector in block for block in still), selector


def test_each_tile_says_what_its_group_holds_and_how_to_open_it(client, fake_report):
    """16 Sep 2026: a tile gave a first-timer four unlabelled icons, a
    count, "nothing flagged" and a chevron, and "nothing flagged" read as
    no reason to look. Now the tile leads with its first ready card's
    own line, names its cards with the count, and its control says Open.
    The board is built in the browser, so this pins the script and the
    stylesheet that build it."""
    from pathlib import Path

    body = _report(client, fake_report)
    script = body.split("The report's groups as tiles that open", 1)[1]
    # The three parts of a tile: the pill, the lead fact, the card names.
    assert '<span class="cat-tile-open" aria-hidden="true"><span class="cat-tile-open-text"></span>' in script
    assert "'<span class=\"cat-tile-cards\"></span>'" in script
    assert "line.className = 'cat-tile-fact'" in script
    # The lead fact never comes from a locked or pending card, nor a line
    # that only says the data is missing.
    fact_at = script.index("function factFor(c)")
    fact = script[fact_at:fact_at + 900]
    assert "dashboard-card-locked" in fact and "dashboard-card-pending" in fact and "skip.test(text)" in fact
    assert "unavailable|not available|search with a house" in fact
    # The first three names, with the count first and how many more there are.
    assert "names.length < 3" in script and "'and ' + (cards - 3) + ' more'" in script
    assert "' locked' : '') + ': '" in script
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert '.cat-tile-open-text::before { content: "Open"; }' in css
    assert '.cat-tile[aria-expanded="true"] .cat-tile-open-text::before { content: "Close"; }' in css
    # One column on a phone: a half-width tile could not hold the icons
    # and the pill on one row, and the new lines need the width to read.
    one_column = css.index(".cat-board { grid-template-columns: minmax(0, 1fr); }")
    assert css.rfind("@media", 0, one_column) == css.rfind("@media (max-width: 560px) {", 0, one_column)


def test_a_card_status_with_no_space_in_it_can_still_wrap(client, fake_report):
    """15 Sep 2026: "managerial/professional" ran 23px out of its card
    at five columns, because a browser will not wrap at a slash. The
    word carries a break opportunity now, and every card status may
    break anywhere as a last resort rather than leave its card."""
    from pathlib import Path

    body = _report(client, fake_report, gather=fake_gather(occupation={"professional_pct": 55.6, "breakdown": []}))
    # Reworded 16 Sep 2026: words wrap at their spaces, and a first-time
    # visitor can read them; the wrap fallback stays for any future token.
    assert "55.6% in managerial or professional jobs" in body
    css = (Path(__file__).resolve().parent.parent / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    rule = css.split("\n.dashboard-card-status {", 1)[1].split("}", 1)[0]
    assert "overflow-wrap: anywhere" in rule and "min-width: 0" in rule


def test_the_group_board_keeps_its_checklist_per_address(client, fake_report):
    """15 Sep 2026: an opened group is ticked and counted, kept on the
    device under this address, and once every group has been opened the
    page's own save offer appears. The server supplies the address key
    and the offer for whoever is reading; a shared report gets no key."""
    body = _report(client, fake_report)
    assert 'data-report-key="M14 5TG|"' in body
    offer = body.split('id="cat-complete"', 1)[1].split("</div>", 1)[0]
    assert offer.lstrip().startswith("hidden")
    assert 'href="/signup?next=/property%3Fpostcode%3DM14' in offer
    # The triggers, not "anything on it changes" (17 Sep 2026): the
    # words come from main.py so the offer and the alert job agree. This
    # report has no house number, so the sale is any at the postcode.
    from app import main as app_main
    assert "be told when " + app_main.alert_triggers_short("") in offer
    assert "a sale is recorded at this postcode" in offer
    assert "uki-report-checked" in body

    # Signed in, the report has already saved itself (watchlist.remember,
    # 8 Sep 2026), so the line points at My properties instead.
    r = client.post("/signup", data={"email": "checklist@customer.test", "password": "password123", "next": "/"}, follow_redirects=False)
    assert r.status_code == 303
    offer = _report(client, fake_report).split('id="cat-complete"', 1)[1].split("</div>", 1)[0]
    assert 'href="/watchlist"' in offer and "/signup" not in offer
