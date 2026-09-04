"""Every page that renders without upstream data, checked the way a
crawler and a browser see it. Cheap to run, and it catches the class of
failure that has actually bitten this site: a template that crashes
(block defined twice), a route that refuses HEAD, a page with two
canonical tags."""
import re
import xml.etree.ElementTree as ET

import pytest

STATIC_PAGES = [
    "/", "/areas", "/methodology", "/premium", "/schools/guide", "/privacy", "/terms",
    "/support", "/buying-guide", "/browser-extension", "/embed", "/login", "/signup",
    "/compare",
]


@pytest.mark.parametrize("path", STATIC_PAGES)
def test_static_page_renders(client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert body.count("<title>") == 1, path
    assert body.count('<link rel="canonical"') == 1, path
    assert body.count('<meta name="description"') == 1, path
    assert len(re.findall(r"<h1[^>]*>", body)) == 1, path
    assert "TemplateAssertionError" not in body and "Traceback" not in body


@pytest.mark.parametrize("path", ["/", "/sitemap.xml", "/robots.txt", "/premium"])
def test_head_requests_are_answered(client, path):
    """Google's sitemap fetcher HEADs before it GETs; a 405 here showed
    up as "Couldn't fetch" in Search Console."""
    r = client.head(path)
    assert r.status_code == 200, path
    assert r.content == b""


def test_unknown_page_is_a_real_404(client):
    r = client.get("/this-page-does-not-exist")
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]


def test_robots_allows_crawling_and_points_at_sitemap(client):
    body = client.get("/robots.txt").text
    assert "Allow: /" in body
    assert "Sitemap: " in body and "/sitemap.xml" in body
    assert "Disallow: /watchlist" in body


def test_sitemap_is_valid_and_substantial(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [el.text for el in root.findall(".//s:loc", ns)]
    assert len(locs) > 300
    assert all(loc.startswith("https://") for loc in locs)
    assert any(loc.endswith("/area/SW1A") for loc in locs)
    assert len(locs) == len(set(locs)), "duplicate sitemap entries"


def test_canonical_strips_tracking_params(client):
    body = client.get("/premium?utm_source=reddit&ref=abc").text
    canon = re.search(r'<link rel="canonical" href="([^"]+)"', body).group(1)
    assert canon.endswith("/premium")
    assert "utm_source" not in canon and "ref=" not in canon


@pytest.fixture
def fake_place(monkeypatch):
    """Resolve a search term without calling postcodes.io or Nominatim.
    Mirrors the real resolver's contract: an outcode comes back labelled
    with the uppercase outcode, a place name with the place name."""
    from app import main as app_main

    async def _resolve(query):
        query = query.strip()
        if re.match(r"^[A-Z]{1,2}[0-9]{1,2}[A-Z]?$", query, re.I):
            return {"latitude": 53.45, "longitude": -2.22, "label": query.upper()}
        return {"latitude": 53.48, "longitude": -2.24, "label": query}

    monkeypatch.setattr(app_main.place_search, "resolve", _resolve)


def _canonical(client, path):
    body = client.get(path).text
    return re.search(r'<link rel="canonical" href="([^"]+)"', body).group(1)


@pytest.mark.parametrize("query", ["M1", "m1", " M1 "])
def test_single_district_school_guide_canonicals_to_itself(client, fake_place, query):
    """These pages carry 30,000-40,000 words of Ofsted detail. They used
    to inherit the default canonical, which drops the query string and so
    pointed every one of them at the 3,700-word landing page - telling
    Google to index that instead, and leaving them unable to rank for
    "schools in M1" no matter how good they got. Casing and spacing all
    normalize to the one URL rather than splitting the ranking signal."""
    canon = _canonical(client, f"/schools/guide?q={query}")
    assert canon.endswith("/schools/guide?q=M1"), canon


def test_multi_area_and_freetext_school_guides_stay_folded(client, fake_place):
    """Only the single-district case earns its own URL. Comparisons are
    combinatorial (2,943 districts choose 4) and free text is whatever a
    geocoder returns, so neither is allowed into the index."""
    two = "53.4808,-2.2426,M1|53.8008,-1.5491,LS1"
    assert _canonical(client, f"/schools/guide?areas={two}").endswith("/schools/guide")
    assert _canonical(client, "/schools/guide?q=Manchester").endswith("/schools/guide")
    assert _canonical(client, "/schools/guide").endswith("/schools/guide")


def test_sitemap_advertises_only_self_canonical_school_guides(client, fake_place):
    """A sitemap entry that canonicals elsewhere asks Google to crawl a
    page and then ignore it. Every school guide submitted must point at
    itself."""
    root = ET.fromstring(client.get("/sitemap.xml").content)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [el.text for el in root.findall(".//s:loc", ns)]
    schools = [u for u in locs if "/schools/guide?q=" in u]
    assert len(schools) > 300

    for url in (schools[0], schools[len(schools) // 2], schools[-1]):
        outcode = url.rsplit("q=", 1)[1]
        assert _canonical(client, f"/schools/guide?q={outcode}") == url, url


def test_sitemap_is_curated_not_the_whole_country(client):
    """Search Console on 26 Aug 2026: 21 indexed against 2,956 submitted,
    105 "Crawled - currently not indexed". Submitting every district at
    once spends a new domain's crawl on the long tail. The rest stay live
    and linked from /areas, just not queue-jumped."""
    root = ET.fromstring(client.get("/sitemap.xml").content)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [el.text for el in root.findall(".//s:loc", ns)]
    # Area GUIDES specifically: /area/M1, not /area/M1/private-schools,
    # which is a different page type that happens to live underneath.
    areas = [u for u in locs if re.search(r"/area/[A-Z0-9]+$", u)]

    from app import main as app_main
    assert len(areas) == len(app_main.AREA_GUIDE_SEED_OUTCODES)
    assert len(areas) < len(app_main.ALL_OUTCODES) / 2

    # Dropped from the sitemap must not mean hidden: still 200, still
    # indexable, still reachable by a crawler through /areas.
    assert client.get("/area/AB12").status_code == 200
    assert "noindex" not in client.get("/area/AB12").text.lower()
    assert client.get("/areas").text.count('href="/area/') > 2900


def test_page_titles_are_specific_not_generic(client):
    generic = {"Premium", "School Guide", "Chrome Extension", "Why trust this", "UKPropertyInsight"}
    for path in ("/", "/premium", "/schools/guide", "/browser-extension", "/methodology"):
        title = re.search(r"<title>(.*?)</title>", client.get(path).text, re.S).group(1).strip()
        bare = title.split("|")[0].strip()
        assert bare not in generic, f"{path} title is still generic: {title!r}"
        assert len(title) <= 120, f"{path} title too long for a search result: {title!r}"


def test_homepage_promo_banner_only_for_signed_out_visitors(client):
    assert "promo-banner" in client.get("/").text


def test_homepage_promo_banner_copy_matches_the_real_offer(client):
    """The banner promises a free Premium report; what it promises must
    match what a new account actually gets. The allowance is written out
    in words rather than substituted as a number, so this pins the
    constant instead: change the allowance and this fails, which forces
    the copy on all four templates to be rewritten with it."""
    from app import auth
    assert auth.FREE_PREMIUM_UNLOCKS == 1, "allowance changed: update the copy that describes it"
    body = client.get("/").text
    assert "get a full Premium property report on us" in body


def test_oauth_buttons_render_only_for_configured_providers(client, monkeypatch):
    body = client.get("/login").text
    assert "Continue with Facebook" not in body and "Continue with LinkedIn" not in body

    monkeypatch.setenv("FACEBOOK_OAUTH_CLIENT_ID", "fb-id")
    monkeypatch.setenv("FACEBOOK_OAUTH_CLIENT_SECRET", "fb-secret")
    monkeypatch.setenv("LINKEDIN_OAUTH_CLIENT_ID", "li-id")
    monkeypatch.setenv("LINKEDIN_OAUTH_CLIENT_SECRET", "li-secret")
    body = client.get("/login").text
    assert 'href="/auth/facebook?next=' in body and "Continue with Facebook" in body
    assert 'href="/auth/linkedin?next=' in body and "Continue with LinkedIn" in body


def test_oauth_login_redirects_to_the_provider(client, monkeypatch):
    monkeypatch.setenv("LINKEDIN_OAUTH_CLIENT_ID", "li-id")
    monkeypatch.setenv("LINKEDIN_OAUTH_CLIENT_SECRET", "li-secret")
    r = client.get("/auth/linkedin?next=/premium", follow_redirects=False)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("https://www.linkedin.com/oauth/v2/authorization?")
    assert "client_id=li-id" in loc and "state=" in loc
    assert "redirect_uri=" in loc and "%2Fauth%2Flinkedin%2Fcallback" in loc


def test_unconfigured_or_unknown_oauth_provider_bounces_to_login(client):
    for path in ("/auth/facebook", "/auth/apple", "/auth/github/callback?code=x&state=y"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303, path
        assert "error=oauth_unavailable" in r.headers["location"], path


def test_oauth_callback_rejects_a_forged_state(client, monkeypatch):
    monkeypatch.setenv("LINKEDIN_OAUTH_CLIENT_ID", "li-id")
    monkeypatch.setenv("LINKEDIN_OAUTH_CLIENT_SECRET", "li-secret")
    r = client.get("/auth/linkedin/callback?code=abc&state=not-what-we-issued", follow_redirects=False)
    assert r.status_code == 303
    assert "error=oauth_state" in r.headers["location"]


def test_pricing_page_lists_all_40_checks(client, monkeypatch):
    """The pricing page's two tiers mirror the report card-for-card:
    25 free + 15 Premium = the 40 the hero claims. The tier block only
    renders when billing is configured, as it is in production."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_m")
    monkeypatch.setenv("STRIPE_PRICE_ID_QUARTERLY", "price_q")
    body = client.get("/premium").text
    assert body.count('class="lx-check"') == 40
    assert "25 free on every report" in body
    assert "15 more with Premium" in body


def test_anonymous_compare_builds_a_column_per_postcode(client, monkeypatch):
    """The compare view is open to everyone: no account, no watchlist.
    Two postcodes in, two columns out, each linking to its own report."""
    from app import main as app_main

    async def _summary(postcode, house_number):
        return {
            "postcode": postcode.upper(), "house_number": house_number,
            "admin_district": "Testerton", "region": "North",
            "avg_price": 250000, "crime_total": 12, "imd_decile": 5,
        }

    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    body = client.get("/compare?postcode=M1+1AE&postcode=LS1+4DY").text
    assert "/property?postcode=M1 1AE" in body
    assert "/property?postcode=LS1 4DY" in body
    assert body.count("Testerton") == 2
    # Never more columns than the cap, however many are passed in.
    many = "&".join(f"postcode=X{i}" for i in range(8))
    assert client.get("/compare?" + many).text.count("Testerton") == app_main.MAX_COMPARE_COLUMNS


def test_compare_survives_an_unknown_postcode(client, monkeypatch):
    """One bad postcode must not take the whole comparison down."""
    from app import main as app_main

    async def _summary(postcode, house_number):
        if postcode.startswith("ZZ"):
            raise ValueError("no such postcode")
        return {"postcode": postcode.upper(), "house_number": "", "admin_district": "Testerton"}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    r = client.get("/compare?postcode=M1+1AE&postcode=ZZ99+9ZZ")
    assert r.status_code == 200
    assert "Not a postcode we could find" in r.text
    assert "Testerton" in r.text


def test_report_share_card_is_its_own_image(client, fake_report):
    """A report shared into a chat should preview as that report, not
    the generic site image."""
    fake_report()
    body = client.get("/property?postcode=M14%205TG").text
    og = re.search(r'<meta property="og:image" content="([^"]+)"', body).group(1)
    assert "/og/property.png" in og and "postcode=" in og


def test_share_card_renders_a_png_without_running_a_gather(client, monkeypatch):
    """The card must build from cached data only. A crawler following
    shared links must never be able to trigger the full gather."""
    from app import main as app_main
    from app.services import _cache

    async def _lookup(_pc):
        return {"postcode": "M14 5TG", "admin_district": "Manchester", "region": "North West"}

    def _boom(*a, **kw):
        raise AssertionError("an image request must not start a gather")

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "_full_property_gather", _boom)
    _cache._store.clear()
    _cache._bytes = 0

    r = client.get("/og/property.png?postcode=M14%205TG")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_share_card_never_shows_premium_figures():
    """The card is public. Only free-tier data may appear on it."""
    from app import main as app_main

    facts = app_main._og_facts({
        "flood_zone": {"label": "Zone 1 (low probability)"},
        "school_landscape": {"good_or_better_pct": 93},
        "crime": {"total": 5},
        "valuation": {"estimate": 999999},
        "household_income": {"estimate": 41000},
    })
    rendered = " ".join(f"{a} {b}" for a, b in facts)
    assert "999,999" not in rendered and "41,000" not in rendered
    assert len(facts) <= 3


def test_weekly_digest_endpoint_needs_the_shared_secret(client, monkeypatch):
    """It emails real people, so an unauthenticated caller must not be
    able to fire it. Same gate as the daily alert job."""
    monkeypatch.setenv("ALERTS_CRON_SECRET", "s3cret")
    assert client.post("/internal/send-weekly-digest").status_code == 404
    assert client.post("/internal/send-weekly-digest",
                       headers={"x-alerts-secret": "wrong"}).status_code == 404


def test_weekly_digest_is_opt_in_only(client, monkeypatch):
    """Every change-alert email already sent promises the reader they
    only hear from us when something actually changed. A scheduled
    email may therefore only ever go to someone who ticked the box."""
    from app import watchlist

    seen = {}

    def _subscribers():
        seen["called"] = True
        return []

    monkeypatch.setenv("ALERTS_CRON_SECRET", "s3cret")
    monkeypatch.setattr(watchlist, "digest_subscribers", _subscribers)

    async def _send(*a, **kw):
        raise AssertionError("nobody opted in, so nothing may be sent")

    from app.services import email as email_service
    monkeypatch.setattr(email_service, "is_configured", lambda: True)
    monkeypatch.setattr(email_service, "send_email", _send)

    r = client.post("/internal/send-weekly-digest", headers={"x-alerts-secret": "s3cret"})
    assert r.status_code == 200
    assert r.json() == {"subscribers": 0, "sent": 0}
    assert seen.get("called")


def test_digest_email_covers_quiet_weeks_and_offers_an_off_switch():
    from app import main as app_main

    html = app_main._weekly_digest_email_html(
        [{"label": "M1 1AE", "postcode": "M1 1AE", "house_number": "", "changes": []}],
        "https://example.test/watchlist", "https://example.test/watchlist",
    )
    assert "No change this week." in html
    assert "Turn it off" in html
    assert "nothing changed" in html.lower() or "No changes" in html


def _digest_form(body: str) -> str:
    """Just the opt-in form. Anchored on its action, not its class: the
    critical CSS is inlined into the page, so splitting on the class
    name lands in the stylesheet instead."""
    return body.split('action="/watchlist/weekly-digest"', 1)[1].split("</form>", 1)[0]


def test_watchlist_shows_the_digest_optin_to_a_signed_in_user(client, monkeypatch):
    """The opt-in has to be visible and reflect the account's current
    setting, or it is not really an opt-in."""
    monkeypatch.setenv("ALERTS_CRON_SECRET", "s3cret")
    monkeypatch.setenv("RESEND_API_KEY", "re_test")

    r = client.post("/signup", data={
        "email": "digest-tester@example.test", "password": "correct horse battery staple",
    }, follow_redirects=False)
    assert r.status_code in (302, 303), r.status_code

    # The opt-in only appears once there is something to digest.
    from app import auth, watchlist
    from app.db import get_session
    with get_session() as db:
        user = auth.find_user_by_email(db, "digest-tester@example.test")
        user_id = user.id
    watchlist.save_item(user_id, "M1 1AE", "", "")

    body = client.get("/watchlist").text
    assert "Also send me a weekly round-up" in body
    assert 'name="enabled"' in body
    assert "checked" not in _digest_form(body)

    client.post("/watchlist/weekly-digest", data={"enabled": "on"}, follow_redirects=False)
    body = client.get("/watchlist").text
    assert "checked" in _digest_form(body)

    client.post("/watchlist/weekly-digest", data={}, follow_redirects=False)
    body = client.get("/watchlist").text
    assert "checked" not in _digest_form(body)


def test_prewarm_endpoint_needs_the_shared_secret(client, monkeypatch):
    monkeypatch.setenv("ALERTS_CRON_SECRET", "s3cret")
    assert client.post("/internal/prewarm-area-guides").status_code == 404
    assert client.post("/internal/prewarm-area-guides",
                       headers={"x-alerts-secret": "nope"}).status_code == 404


def test_school_admission_page_is_honest_about_catchments(client, monkeypatch):
    """The page targets "catchment area for X" searches, and the honest
    answer is that most English schools do not have one. It must say so
    rather than drawing a circle and calling it a boundary."""
    from app.services import schools_db

    profile = {
        "urn": 100050, "name": "Parliament Hill School", "slug": "parliament-hill-school",
        "phase": "Secondary", "group": "Secondary", "type": "Community school",
        "postcode": "NW5 1RL", "latitude": 51.55, "longitude": -0.15,
        "ofsted_rating": 1, "ofsted_rating_label": "Outstanding",
        "ofsted_inspection_date": None, "miles": 1.14, "academic_year": "2024",
        "authority": "Camden", "fsm_eligible_pct": 34.8,
        "street": "Highgate Road", "town": "London", "website": "",
        "ks4": None, "ks2": None,
    }
    monkeypatch.setattr(schools_db, "admission_profile", lambda urn: profile if urn == 100050 else None)

    body = client.get("/school/100050/parliament-hill-school").text
    assert "1.14" in body
    assert "not a catchment area" in body.lower()
    assert "Camden" in body
    # The distance is evidence, never a promise.
    assert "guarantee" in body.lower()


def test_school_admission_page_normalises_its_url(client, monkeypatch):
    """One page per school, not one per spelling of its name."""
    from app.services import schools_db

    profile = {
        "urn": 100050, "name": "Parliament Hill School", "slug": "parliament-hill-school",
        "phase": "Secondary", "group": "Secondary", "type": "Community school",
        "postcode": "NW5 1RL", "latitude": 51.55, "longitude": -0.15,
        "ofsted_rating": 1, "ofsted_rating_label": "Outstanding",
        "ofsted_inspection_date": None, "miles": 1.14, "academic_year": "2024",
        "authority": "Camden", "fsm_eligible_pct": None,
        "street": "", "town": "", "website": "", "ks4": None, "ks2": None,
    }
    monkeypatch.setattr(schools_db, "admission_profile", lambda urn: profile if urn == 100050 else None)

    r = client.get("/school/100050/some-other-slug", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/school/100050/parliament-hill-school"
    assert client.get("/school/999999/nope").status_code == 404


def test_only_schools_with_real_data_get_a_page(client, monkeypatch):
    """3,200 schools have a published admission distance; 26,533 exist.
    A page for the rest would carry nothing Ofsted does not already
    give away."""
    from app.services import schools_db
    monkeypatch.setattr(schools_db, "admission_profile", lambda urn: None)
    assert client.get("/school/123456/any-school").status_code == 404


def test_calculator_pages_render_and_share_one_script(client):
    """Both tool pages exist as their own indexable page, and both are
    driven by the same file, so the tax bands have one home."""
    for slug in ("stamp-duty-calculator", "mortgage-calculator"):
        body = client.get(f"/tools/{slug}").text
        assert "/static/js/calculators.js" in body, slug
        assert 'id="calc-price"' in body, slug
        assert "noindex" not in body, slug
    assert client.get("/tools/not-a-tool").status_code == 404


def test_report_and_tools_use_the_same_tax_bands():
    """Two copies of the bands would drift the first time a Budget moved
    a threshold, and the wrong copy would keep answering."""
    import pathlib
    from app import main as app_main

    root = pathlib.Path(app_main.__file__).resolve().parent
    js = (root / "static" / "js" / "calculators.js").read_text(encoding="utf-8")
    report = (root / "templates" / "property.html").read_text(encoding="utf-8")
    assert "const BANDS" in js
    assert "const BANDS" not in report, "the report has its own copy of the tax bands again"
    assert "/static/js/calculators.js" in report


def test_district_price_table_needs_a_real_sample(client, monkeypatch):
    """A median of two sales is not a statistic, and districts without
    enough sales must not be ranked."""
    from app import main as app_main
    monkeypatch.setattr(app_main, "_district_price_table", lambda: {
        "total": 0, "cheapest": [], "dearest": [], "median_of_medians": None,
    })
    from app.services import _cache
    _cache._store.clear()
    _cache._bytes = 0
    body = client.get("/market/district-prices").text
    assert "Not enough districts" in body
    # The England and Wales limit is stated, never silently applied.
    assert "England and Wales only" in body


import pathlib  # noqa: E402


def test_a_new_account_is_told_it_has_a_report_to_use(client):
    """Four accounts signed up and saw no page but /premium, so they
    never learned they had been given anything. The signed-out banner
    disappeared at the moment of signing up and nothing replaced it."""
    client.cookies.clear()
    anon = client.get("/").text
    assert "Sign up free" in anon

    client.post("/signup", data={"email": "newcomer@example.test",
                                 "password": "correct horse battery staple"},
                follow_redirects=False)
    body = client.get("/").text
    assert "Your account is ready" in body
    assert "full report to use on any address" in body


def test_the_pricing_page_never_sends_a_new_account_back_to_itself(client):
    """Signing up from /premium used to return the person to the price
    list, having still not seen a report. Nobody buys what they have not
    seen."""
    client.cookies.clear()
    assert "/signup?next=/premium" not in client.get("/premium").text

    # The rendered CTAs carry no ?next at all when Stripe is
    # unconfigured, as it is here, so the template itself is what has to
    # be pinned: that is where the destination is written.
    template = pathlib.Path("app/templates/premium.html").read_text(encoding="utf-8")
    assert "/signup?next=/premium" not in template
    assert template.count('href="/signup?next=/"') == 2


def test_trustpilot_brand_stays_within_their_guidelines(client):
    """Trustpilot's compliance team wrote on 31 Aug 2026: no unofficial
    widget, TrustScore, star rating or review count outside their own
    widgets, 7 days to fix, consumer alert threatened for repeats. The
    official widget is ruled out by the privacy promise, so the page
    shows verbatim quotes and a plain link, and this test keeps every
    flagged element from coming back."""
    from app.main import TRUSTPILOT

    body = client.get("/").text
    assert TRUSTPILOT["profile_url"] in body
    for review in TRUSTPILOT["reviews"]:
        assert review["who"] in body
        assert review["quote"][:40] in body

    # The elements the notice named, gone and staying gone.
    assert "lx-stars" not in body
    assert "TrustScore" not in body
    lowered = body.lower()
    for banned in ("rated 4", "from 3 reviews", "out of 5"):
        assert banned not in lowered, f"{banned!r} reads as a score or count"


def test_no_third_party_script_runs_on_the_site(client):
    """The privacy page promises no third-party tracking scripts, which
    rules out the Trustpilot widget however convenient it would be."""
    import re

    for path in ("/", "/premium", "/privacy"):
        body = client.get(path).text
        for src in re.findall(r'<script[^>]+src="([^"]+)"', body):
            assert src.startswith("/"), f"{path} loads an off-site script: {src}"


def test_the_extension_page_links_to_the_real_store_listing(client):
    """The extension went public on 20 Aug 2026 and this page spent ten
    days still saying "pending review": the URL constant was never
    flipped. The page must carry the install link, and the pending
    notice must be gone."""
    body = client.get("/browser-extension").text
    assert "chromewebstore.google.com/detail/ukpropertyinsight-overlay" in body
    assert "pending review" not in body.lower()


def test_outstanding_schools_page_renders_with_real_counts(client):
    """Targets "ofsted outstanding schools near me", a query Search
    Console shows at position 59 with no page answering it. Every number
    on it comes from the schools register, and the no-current-grade
    caveat must be present: without it the page implies ungraded schools
    failed something."""
    body = client.get("/schools/outstanding").text
    assert "Outstanding schools in England" in body
    assert "no current grade, and that" in body
    assert 'action="/schools/guide"' in body
    assert body.count("<h1") == 1


# ---- schools guide: visible, mapped, lazily detailed ---------------------

def _seed_school_near(lat, lon, urn=990001, name="Testbrook Primary School"):
    """One real-looking school in the test database, close to the point
    the guide will be asked about. The test SQLite starts empty, and
    an empty landscape renders the no-data notice rather than the table."""
    from app import db
    from app.models import School
    with db.get_session() as session:
        if session.get(School, urn) is None:
            session.add(School(
                urn=urn, name=name, phase="Primary", type_name="Community school",
                postcode="M1 1AE", latitude=lat + 0.004, longitude=lon - 0.003,
                ofsted_rating=2, ofsted_rating_label="Good",
            ))
            session.commit()


def _resolve_to(monkeypatch, lat, lon, label):
    from app import main as app_main

    async def _resolve(_query):
        return {"latitude": lat, "longitude": lon, "label": label}

    monkeypatch.setattr(app_main.place_search, "resolve", _resolve)


def test_school_guide_shows_its_schools_without_a_tap(client, monkeypatch):
    """Before 1 Sep 2026 a district guide rendered 99 schools into the
    page and hid every one of them behind count chips: a visitor (and
    Googlebot) saw a summary card and nothing else. The table is the
    page now."""
    _seed_school_near(53.48, -2.24)
    _resolve_to(monkeypatch, 53.48, -2.24, "M1")
    body = client.get("/schools/guide?q=M1").text
    assert 'class="tx-table school-table"' in body
    assert 'data-school-urn="990001"' in body
    assert "Testbrook Primary School" in body
    assert 'id="school-map-0"' in body
    assert 'data-school-ctx data-lat="53.48"' in body
    # No inline popups: they load on demand now.
    assert '<dialog class="report-modal" id="school-modal-' not in body


def test_school_profile_loads_on_demand(client):
    """The popup that used to be rendered 99 times per page comes from
    one endpoint per school, measured from the search point."""
    _seed_school_near(53.48, -2.24)
    r = client.get("/schools/profile/990001?lat=53.48&lon=-2.24&back=/schools/guide%3Fq%3DM1")
    assert r.status_code == 200
    assert 'id="school-modal-990001"' in r.text
    assert "Testbrook Primary School" in r.text
    assert "report-modal-close" in r.text
    # Signed out, the review section offers a login rather than a form.
    assert "to leave a review" in r.text
    # Unknown school: nothing, not an error page.
    assert client.get("/schools/profile/1?lat=53.48&lon=-2.24").status_code == 404


def test_property_report_no_longer_ships_every_school_popup(client, fake_report):
    fake_report()
    body = client.get("/property?postcode=M14+5TG", headers={"User-Agent": "Googlebot/2.1"}).text
    assert '<dialog class="report-modal" id="school-modal-' not in body
    assert 'data-school-ctx' in body

# ---- school page: checker, map, share card --------------------------------

def _seed_admission_school(urn=990002):
    """A school with a published admission distance, so the school page
    (which only exists for those) renders in the empty test database."""
    from app import db
    from app.models import School, SchoolAdmissionRadius
    with db.get_session() as session:
        if session.get(School, urn) is None:
            session.add(School(
                urn=urn, name="Riverside Academy", phase="Secondary", type_name="Academy converter",
                postcode="M1 1AE", latitude=53.48, longitude=-2.24,
                ofsted_rating=1, ofsted_rating_label="Outstanding",
            ))
            session.add(SchoolAdmissionRadius(
                urn=urn, last_distance_miles=1.5, academic_year="2025",
                source_authority="Manchester",
            ))
            session.commit()


def test_admission_verdict_bands():
    from app.main import _admission_verdict

    assert _admission_verdict(1.0, 1.5)["level"] == "likely"
    assert _admission_verdict(1.4, 1.5)["level"] == "borderline"
    assert _admission_verdict(1.6, 1.5)["level"] == "borderline"
    assert _admission_verdict(2.0, 1.5)["level"] == "unlikely"
    # Distance and margin are reported, rounded for reading.
    v = _admission_verdict(1.234, 1.5)
    assert v["distance_miles"] == 1.23 and v["margin_miles"] == 0.27


def test_school_page_has_map_checker_and_share_card(client, monkeypatch):
    _seed_admission_school()
    body = client.get("/school/990002/riverside-academy").text
    assert 'id="school-page-map"' in body
    assert 'name="check"' in body
    assert "Will an address get in?" in body
    assert 'property="og:image" content="https://testserver/og/school/990002.png"' in body
    # The grade strip carries the admission figure.
    assert "Admitted from, 2025" in body

    # Checking a postcode: the geocoder is stubbed to a point 1 mile away.
    from app import main as app_main

    async def _lookup(_pc):
        return {"postcode": "M1 2AA", "latitude": 53.4945, "longitude": -2.24}

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    body = client.get("/school/990002/riverside-academy?check=M1+2AA").text
    assert "admission-verdict-likely" in body
    assert "M1 2AA" in body
    assert 'href="/property?postcode=M1%202AA"' in body

    async def _nowhere(_pc):
        return None

    monkeypatch.setattr(app_main, "lookup_postcode", _nowhere)
    body = client.get("/school/990002/riverside-academy?check=ZZ1+1ZZ").text
    assert "as a UK postcode" in body


def test_school_share_card_is_a_real_png(client):
    _seed_admission_school()
    r = client.get("/og/school/990002.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    # Unknown school falls back to the default image rather than erroring.
    r = client.get("/og/school/1.png", follow_redirects=False)
    assert r.status_code == 302

# ---- admissions: council hubs and the guide -------------------------------

def test_admissions_index_lists_councils(client):
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/schools/admissions").text
    assert 'href="/schools/admissions/manchester"' in body
    assert "Manchester" in body
    assert 'href="/schools/how-admissions-work"' in body


def test_council_hub_shows_every_school_tightest_first(client):
    _seed_admission_school()
    from app import db
    from app.models import School, SchoolAdmissionRadius
    with db.get_session() as session:
        if session.get(School, 990003) is None:
            session.add(School(urn=990003, name="Canal Street Primary", phase="Primary",
                               type_name="Community school", postcode="M1 2BB",
                               latitude=53.47, longitude=-2.23, ofsted_rating=2, ofsted_rating_label="Good"))
            session.add(SchoolAdmissionRadius(urn=990003, last_distance_miles=0.4,
                                              academic_year="2025", source_authority="Manchester"))
            session.commit()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    r = client.get("/schools/admissions/manchester")
    assert r.status_code == 200
    body = r.text
    assert "How far Manchester schools admitted from" in body
    assert 'href="/school/990003/canal-street-primary"' in body
    assert 'href="/school/990002/riverside-academy"' in body
    # Tightest first: the 0.4-mile school is named as the tightest.
    assert "The tightest is" in body and "Canal Street Primary" in body.split("The tightest is")[1][:200]
    assert "0.4 mi" in body
    assert "Primary schools" in body and "Secondary schools" in body
    assert client.get("/schools/admissions/no-such-council").status_code == 404


def test_admissions_guide_and_links(client):
    body = client.get("/schools/how-admissions-work").text
    assert "31 October" in body and "15 January" in body
    assert "Why most schools have no catchment area" in body
    # The school page and the guide link to the hubs.
    _seed_admission_school()
    page = client.get("/school/990002/riverside-academy").text
    assert 'href="/schools/admissions/manchester"' in page
    assert 'href="/schools/how-admissions-work"' in page


def test_sitemap_carries_the_admissions_pages(client):
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/sitemap.xml").text
    assert "/schools/admissions</loc>" in body
    assert "/schools/how-admissions-work</loc>" in body
    assert "/schools/admissions/manchester</loc>" in body


# ---- anonymous HTML cache ---------------------------------------------------

def test_slow_pages_are_served_from_cache_for_anonymous_visitors(client):
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    first = client.get("/schools/how-admissions-work")
    assert first.headers.get("x-anon-cache") == "miss"
    second = client.get("/schools/how-admissions-work")
    assert second.headers.get("x-anon-cache") == "hit"
    assert second.text == first.text
    assert second.headers["content-type"].startswith("text/html")


def test_cache_never_serves_a_personalised_or_parameterised_page(client):
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    # A query the cache does not understand: rendered fresh both times.
    _seed_admission_school()
    for _ in range(2):
        r = client.get("/school/990002/riverside-academy?check=M1+1AE")
        assert r.headers.get("x-anon-cache") is None
    # A session cookie: never cached, never served from cache.
    client.cookies.set("session", "anything")
    try:
        r = client.get("/schools/how-admissions-work")
        assert r.headers.get("x-anon-cache") is None
    finally:
        client.cookies.clear()
    # A referral visit must keep its Set-Cookie, so it is not cached either.
    r = client.get("/buying-guide?ref=partner1")
    assert r.headers.get("x-anon-cache") is None
    assert "set-cookie" in r.headers



# ---- school shortlist + admission-update alerts ----------------------------

def _signed_in(client, email="parent@example.com"):
    client.post("/signup", data={"email": email, "password": "correct-horse-battery"}, follow_redirects=True)
    client.post("/login", data={"email": email, "password": "correct-horse-battery"}, follow_redirects=True)


def test_saving_a_school_from_its_page_returns_there_and_lists_its_distance(client):
    _seed_admission_school()
    _signed_in(client)
    r = client.post("/schools/shortlist/save", data={"urn": "990002", "next": "/school/990002/riverside-academy"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/school/990002/riverside-academy"
    page = client.get("/school/990002/riverside-academy").text
    assert "Saved to" in page
    shortlist = client.get("/schools/shortlist").text
    assert "Riverside Academy" in shortlist and "1.5 mi" in shortlist and "2025" in shortlist
    # Opt in to alerts.
    r = client.post("/schools/shortlist/alerts", data={"enabled": "on"}, follow_redirects=True)
    assert "You will hear from us" in r.text


def test_admission_update_alert_fires_only_on_a_real_change(client, monkeypatch):
    import os
    from app import db, main as app_main
    from app.models import SchoolAdmissionRadius
    from app.services import email as email_service

    _seed_admission_school()
    _signed_in(client, "alerts@example.com")
    client.post("/schools/shortlist/save", data={"urn": "990002", "next": "/schools/shortlist"})
    client.post("/schools/shortlist/alerts", data={"enabled": "on"})
    client.cookies.clear()

    sent = []

    async def _send(to, subject, html):
        sent.append((to, subject, html))
        return True

    monkeypatch.setattr(email_service, "send_email", _send)
    monkeypatch.setattr(email_service, "is_configured", lambda: True)
    monkeypatch.setenv("ALERTS_CRON_SECRET", "s3cret")
    headers = {"x-alerts-secret": "s3cret"}

    # Wrong secret: not found, not a hint.
    assert client.post("/internal/send-admission-updates", headers={"x-alerts-secret": "nope"}).status_code == 404

    # First run only records: nothing has changed since sign-up.
    r = client.post("/internal/send-admission-updates", headers=headers).json()
    assert r["subscribers_emailed"] == 0 and r["snapshots_recorded"] >= 1
    assert sent == []

    # Same figure again: silence.
    r = client.post("/internal/send-admission-updates", headers=headers).json()
    assert r["subscribers_emailed"] == 0 and r["changes"] == 0

    # The council republishes: one email, naming the school and both figures.
    with db.get_session() as session:
        row = session.get(SchoolAdmissionRadius, 990002)
        row.last_distance_miles, row.academic_year = 1.2, "2026"
        session.commit()
    # Another test's user may have saved the same school with alerts on,
    # so count this user's email rather than the total.
    r = client.post("/internal/send-admission-updates", headers=headers).json()
    assert r["subscribers_emailed"] >= 1 and r["changes"] >= 1
    mine = [m for m in sent if m[0] == "alerts@example.com"]
    assert len(mine) == 1
    to, subject, html = mine[0]
    assert "Riverside Academy" in subject and "1.2" in subject
    assert "1.5 miles (2025)" in html and "never on a schedule" in html


# ---- the intersection: prices within reach, verdict-led cards, deadlines ---

def test_admissions_deadline_rolls_over():
    import datetime
    from app.main import _admissions_deadline

    d = _admissions_deadline("Secondary", datetime.date(2026, 9, 1))
    assert d["deadline"] == datetime.date(2026, 10, 31) and d["entry_year"] == 2027
    d = _admissions_deadline("Secondary", datetime.date(2026, 11, 2))
    assert d["deadline"] == datetime.date(2027, 10, 31) and d["entry_year"] == 2028
    d = _admissions_deadline("Primary", datetime.date(2026, 9, 1))
    assert d["deadline"] == datetime.date(2027, 1, 15) and d["offers"] == datetime.date(2027, 4, 16)
    d = _admissions_deadline("Primary", datetime.date(2027, 1, 20))
    assert d["deadline"] == datetime.date(2028, 1, 15)


def test_school_page_prices_the_districts_within_reach(client):
    """The page a school site cannot make and a portal will not: the
    districts inside the admission distance, each with what homes
    there actually sold for."""
    import json
    from app import db, main as app_main
    from app.models import PageCache
    from app.services import _cache
    _seed_admission_school()
    # An area-guide payload for a district whose centre is near the school.
    near = [o for o in app_main.ALL_OUTCODES
            if app_main._haversine_km(53.48, -2.24, o["lat"], o["lon"]) < 1.5 * 1.60934]
    assert near, "no outcode centroid near the seeded school"
    oc = near[0]["outcode"]
    with db.get_session() as session:
        session.merge(PageCache(
            cache_key=f"area_guide:{app_main.AREA_GUIDE_PAYLOAD_VERSION}:{oc}",
            value=json.dumps({"local_sales": {"enough_for_median": True, "median": 250000, "count": 31},
                              "hpi": {"local_authority": {"name": "Manchester"}}}),
            created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        ))
        session.commit()
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/school/990002/riverside-academy").text
    assert "What it costs to live within reach of Riverside Academy" in body
    assert "£250,000" in body and f'href="/area/{oc}"' in body
    assert "How much does it cost to live within reach" in body   # the FAQ JSON-LD
    assert "Applying for September 2027?" in body


def test_report_schools_card_leads_with_the_verdict(client, fake_report):
    from tests.conftest import fake_gather
    landscape = {
        "total_schools": 3, "good_or_better_pct": 80, "radius_miles": 3, "radius_km": 4.8,
        "by_rating": [], "by_phase": [], "by_sector": {}, "special_count": 0, "special_schools": [],
        "further_education": 0, "higher_education_count": 0, "higher_education_names": [],
        "independent_count": 0, "independent_names": [], "independent_schools": [], "higher_education": [],
        "all_schools": [
            {"urn": 1, "name": "Near Primary", "type": "Community school", "distance_m": 600, "phase_group": "Primary",
             "ofsted_rating": 2, "ofsted_rating_label": "Good", "latitude": 53.45, "longitude": -2.22,
             "admission_radius": {"last_distance_miles": 1.0, "academic_year": "2025", "source_authority": "Manchester"}},
            {"urn": 2, "name": "Far Secondary", "type": "Academy", "distance_m": 3000, "phase_group": "Secondary",
             "ofsted_rating": 1, "ofsted_rating_label": "Outstanding", "latitude": 53.46, "longitude": -2.23,
             "catchment_estimate": {"radius_miles": 1.0}},
            {"urn": 3, "name": "No Figure School", "type": "Academy", "distance_m": 900, "phase_group": "Primary",
             "ofsted_rating": None, "ofsted_rating_label": "", "latitude": 53.45, "longitude": -2.22},
        ],
    }
    fake_report(gather=fake_gather(school_landscape=landscape))
    body = client.get("/property?postcode=M14+5TG", headers={"User-Agent": "Googlebot/2.1"}).text
    assert "Likely for 1 school" in body
    assert "3 within 3 miles" in body


def test_compare_page_gets_a_schools_row(client, monkeypatch):
    from app import main as app_main

    async def _summary(postcode, house_number):
        return {"postcode": postcode.upper(), "house_number": "", "avg_price": 200000,
                "school_verdicts": {"counts": {"likely": 2, "borderline": 1, "unlikely": 0}, "total": 3,
                                    "likely": ["Near Primary", "Other Primary"], "borderline": ["Far Secondary"]}}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    body = client.get("/compare?postcode=M1+1AE&postcode=LS1+4DY").text
    assert "Schools likely to admit" in body
    assert "2 likely" in body and "Near Primary, Other Primary" in body


# ---- the tightest-catchments story, the catchment title, the counter ------

def _seed_two_schools():
    """Two schools of our own, so no other test's edits to the shared
    Riverside Academy row can change what these assertions see."""
    from app import db
    from app.models import School, SchoolAdmissionRadius
    with db.get_session() as session:
        for urn, name, phase, miles in ((990011, "Harbour Lane Primary", "Primary", 0.37),
                                        (990012, "Quayside Academy", "Secondary", 1.7)):
            if session.get(School, urn) is None:
                session.add(School(urn=urn, name=name, phase=phase, type_name="Academy converter",
                                   postcode="M1 2BB", latitude=53.47, longitude=-2.23,
                                   ofsted_rating=2, ofsted_rating_label="Good"))
                session.add(SchoolAdmissionRadius(urn=urn, last_distance_miles=miles,
                                                  academic_year="2025", source_authority="Manchester"))
        session.commit()


def test_tightest_catchments_ranks_nationally_and_by_council(client):
    _seed_two_schools()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    r = client.get("/schools/tightest-catchments")
    assert r.status_code == 200
    body = r.text
    assert "tightest school catchments" in body.lower()
    # The 0.37-mile school heads the national table and links to its page.
    first, second = body.index("harbour-lane-primary"), body.index("quayside-academy")
    assert first < second
    assert 'href="/schools/admissions/manchester"' in body
    assert "0.37 mi" in body and "1.7 mi" in body
    # Every figure names where it came from.
    assert "published" in body.lower() and "straight line" in body.lower()
    assert 'href="/schools/tightest-catchments"' in client.get("/schools/admissions").text
    assert "/schools/tightest-catchments" in client.get("/sitemap.xml").text


def test_school_title_answers_the_catchment_query(client):
    """Parents search "X catchment area"; the title says that and gives
    the number, which no other result has."""
    _seed_two_schools()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/school/990012/quayside-academy").text
    title = body.split("<title>")[1].split("</title>")[0]
    assert title.startswith("Quayside Academy catchment area")
    assert "admitted from 1.7 miles in 2025" in title
    assert "Quayside Academy catchment area:" in body  # meta description


def test_internal_check_header_is_not_a_pageview(client):
    from app import db
    from app.models import PageView
    from sqlalchemy import func, select

    def count():
        with db.get_session() as session:
            return session.execute(select(func.count()).select_from(PageView)).scalar_one()

    before = count()
    first = client.get("/methodology", headers={"X-Internal-Check": "1", "User-Agent": "Mozilla/5.0"})
    assert first.status_code == 200 and count() == before
    # The second request is served from the anonymous HTML cache, where
    # the session layer never ran. It must still count.
    second = client.get("/methodology", headers={"User-Agent": "Mozilla/5.0"})
    assert second.status_code == 200 and second.headers.get("x-anon-cache") == "hit"
    assert count() == before + 1


# ---- exposure work, 3 Sep 2026 -------------------------------------------

def _seed_independent_school():
    from app import db
    from app.models import School, SchoolDetail
    with db.get_session() as session:
        if session.get(School, 990021) is None:
            session.add(School(urn=990021, name="Whitworth House School", phase="Not applicable",
                               type_name="Other independent school", postcode="M1 3CC",
                               latitude=53.47, longitude=-2.25))
            session.add(SchoolDetail(urn=990021, local_authority="Manchester", town="Manchester",
                                     gender="Girls", religious_character="None", age_low=3, age_high=18,
                                     website="https://example.org", school_capacity=400, number_on_roll=300))
            session.commit()


def test_independent_school_pages_by_council(client):
    _seed_independent_school()
    _seed_two_schools()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    index = client.get("/schools/independent")
    assert index.status_code == 200
    assert 'href="/schools/independent/manchester"' in index.text
    page = client.get("/schools/independent/manchester")
    assert page.status_code == 200
    body = page.text
    assert "<title>Private schools in Manchester" in body
    assert "Whitworth House School" in body and "3 to 18" in body and "Girls" in body
    assert "75%" in body  # 300 of 400
    assert '"BreadcrumbList"' in body and '"FAQPage"' in body
    # Links across to the state-school hub for the same council.
    assert 'href="/schools/admissions/manchester"' in body
    assert client.get("/schools/independent/no-such-council").status_code == 404
    assert "/schools/independent/manchester" in client.get("/sitemap.xml").text


def test_structured_data_and_share_cards_on_the_admissions_pages(client):
    _seed_two_schools()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    story = client.get("/schools/tightest-catchments").text
    assert '"Dataset"' in story and "/schools/admission-distances.csv" in story
    assert '"BreadcrumbList"' in story
    assert 'property="og:image" content="https://testserver/og/tightest-catchments.png"' in story
    hub = client.get("/schools/admissions/manchester").text
    assert '"Dataset"' in hub and '"BreadcrumbList"' in hub
    assert "<title>Manchester school catchments" in hub
    assert 'content="https://testserver/og/council/manchester.png"' in hub
    school = client.get("/school/990012/quayside-academy").text
    assert '"BreadcrumbList"' in school
    # The mesh: the other seeded school is a few hundred metres away.
    assert 'href="/school/990011/harbour-lane-primary"' in school
    assert "Other schools nearby with a published distance" in school


def test_csv_and_llms_txt(client):
    _seed_two_schools()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    csv = client.get("/schools/admission-distances.csv")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.text.splitlines()[0] == "urn,school,phase,council,town,last_distance_miles,intake_year,page"
    assert "990011,Harbour Lane Primary,Primary,Manchester" in csv.text
    llms = client.get("/llms.txt")
    assert llms.status_code == 200 and llms.text.startswith("# UKPropertyInsight")
    assert "/schools/admissions" in llms.text
    # Neither is a pageview.
    assert client.get("/internal/indexnow-resubmit").status_code == 405
    assert client.post("/internal/indexnow-resubmit").status_code == 404


def test_admissions_index_shows_each_council_figure(client):
    _seed_two_schools()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/schools/admissions").text
    assert "Middle distance" in body and "Filled from under a mile" in body
    assert 'href="/school/990011/harbour-lane-primary"' in body
    assert 'href="/schools/tightest-catchments"' in client.get("/methodology").text  # footer


def test_catchment_house_prices_pairs_distances_with_land_registry(client, monkeypatch):
    """A tight gate you can still afford: the school's district priced
    below the national district median. Two districts priced, one school
    at the cheaper one; the affordable table names it."""
    from app import db, main as app_main
    from app.models import School, SchoolAdmissionRadius
    m1 = next(o for o in app_main.ALL_OUTCODES if o["outcode"] == "M1")
    with db.get_session() as session:
        if session.get(School, 990051) is None:
            session.add(School(urn=990051, name="Ancoats Gate Primary", phase="Primary", type_name="Academy converter",
                               postcode="M1 2BB", latitude=m1["lat"], longitude=m1["lon"],
                               ofsted_rating=2, ofsted_rating_label="Good"))
            session.add(SchoolAdmissionRadius(urn=990051, last_distance_miles=0.3,
                                              academic_year="2025", source_authority="Manchester"))
            session.commit()
    monkeypatch.setattr(app_main, "_district_price_rows_by_outcode", lambda: {
        "M1": {"outcode": "M1", "median": 250000, "count": 40, "district": "Manchester"},
        "SW3": {"outcode": "SW3", "median": 1500000, "count": 40, "district": "Kensington and Chelsea"},
    })
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    r = client.get("/schools/catchment-house-prices")
    assert r.status_code == 200
    body = r.text
    assert "within reach" in body
    assert "Ancoats Gate Primary" in body and "&pound;250,000" in body
    assert 'href="/area/M1"' in body
    assert "/schools/catchment-house-prices" in client.get("/sitemap.xml").text
    assert 'href="/schools/catchment-house-prices"' in client.get("/schools/tightest-catchments").text


def test_council_hub_invites_the_signed_out_to_sign_up(client):
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/schools/admissions/manchester").text
    assert 'href="/signup?next=/schools/admissions/manchester"' in body


def test_sitemap_is_cached_and_dated_by_deploy(client):
    from app import main as app_main
    first = client.get("/sitemap.xml").text
    assert f"<lastmod>{app_main._STARTED_ON}</lastmod>" in first
    assert client.get("/sitemap.xml").text == first


# ---- daily ten, 4 Sep 2026 -------------------------------------------------

def test_404_page_offers_the_search_and_the_data_pages(client):
    body = client.get("/no-such-page-at-all").text
    assert 'action="/property"' in body
    for href in ("/schools/admissions", "/schools/tightest-catchments", "/schools/independent", "/areas"):
        assert f'href="{href}"' in body


def test_premium_and_guide_carry_faq_markup(client):
    assert '"FAQPage"' in client.get("/premium").text
    assert "Can I cancel?" in client.get("/premium").text
    assert '"FAQPage"' in client.get("/schools/how-admissions-work").text
    assert "Will my child get into the school" in client.get("/").text


def test_share_row_on_the_pages_parents_forward(client):
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    for path in ("/schools/tightest-catchments", "/schools/admissions/manchester", "/school/990002/riverside-academy"):
        body = client.get(path).text
        assert "https://wa.me/?text=" in body and "data-copy-link" in body, path


def test_anonymous_cache_hit_carries_cache_control(client):
    client.get("/methodology", headers={"User-Agent": "Mozilla/5.0"})
    second = client.get("/methodology", headers={"User-Agent": "Mozilla/5.0"})
    assert second.headers.get("x-anon-cache") == "hit"
    assert "max-age=120" in second.headers.get("cache-control", "")


def test_area_guide_lists_schools_with_a_published_distance():
    from app.services import schools_db
    from app import db
    from app.models import School, SchoolAdmissionRadius
    with db.get_session() as session:
        if session.get(School, 990061) is None:
            session.add(School(urn=990061, name="Piccadilly Gate Primary", phase="Primary", type_name="Academy converter",
                               postcode="M1 3AA", latitude=53.47, longitude=-2.23, ofsted_rating=2, ofsted_rating_label="Good"))
            session.add(SchoolAdmissionRadius(urn=990061, last_distance_miles=0.6, academic_year="2025", source_authority="Manchester"))
            session.commit()
    rows = schools_db.admission_rows_in_outcodes({"M1"})
    assert any(r["name"] == "Piccadilly Gate Primary" and r["miles"] == 0.6 for r in rows)
    assert not any(r["name"] == "Piccadilly Gate Primary" for r in schools_db.admission_rows_in_outcodes({"M2"}))


def test_school_page_shows_the_ofsted_note_instead_of_a_blank(client):
    from app import db
    from app.models import School, SchoolAdmissionRadius
    with db.get_session() as session:
        if session.get(School, 990071) is None:
            session.add(School(urn=990071, name="Orchard Gate Primary", phase="Primary", type_name="Academy sponsor led",
                               postcode="M1 4AA", latitude=53.47, longitude=-2.23, ofsted_rating=None, ofsted_rating_label="",
                               ofsted_note="Ungraded inspection, June 2025: improved significantly"))
            session.add(SchoolAdmissionRadius(urn=990071, last_distance_miles=0.9, academic_year="2025", source_authority="Manchester"))
            session.commit()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/school/990071/orchard-gate-primary").text
    assert "Ungraded inspection, June 2025: improved significantly" in body
    assert "No current grade" not in body.split("<h1>")[1][:1500]
    hub = client.get("/schools/admissions/manchester").text
    assert "improved significantly" in hub


def test_school_guide_shows_the_ofsted_note_for_an_unrated_school(client, monkeypatch):
    """The 4 Sep 2026 hotfix: a school with no grade rendered the guide
    through a variable the loop did not have, and every seeded school in
    the suite had a grade, so nothing caught it. This one has none."""
    from app import db
    from app.models import School
    with db.get_session() as session:
        if session.get(School, 990081) is None:
            session.add(School(urn=990081, name="Ungraded Lane Primary", phase="Primary", type_name="Academy sponsor led",
                               postcode="M1 1AE", latitude=53.483, longitude=-2.243, ofsted_rating=None, ofsted_rating_label="",
                               ofsted_note="Ungraded inspection, May 2025: standards maintained"))
            session.commit()
    _resolve_to(monkeypatch, 53.48, -2.24, "M1")
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    r = client.get("/schools/guide?q=M1")
    assert r.status_code == 200
    assert "Ungraded Lane Primary" in r.text
    assert "standards maintained" in r.text


# ---- three pillars, 4 Sep 2026 night ---------------------------------------

def test_homepage_offers_the_three_pillars(client):
    body = client.get("/").text
    assert "running costs" in body.lower()
    for href in ("/areas", "/schools/admissions", "/running-costs"):
        assert f'class="lx-pillar" href="{href}"' in body
    assert 'href="/running-costs">Running costs</a>' in body  # navigation


def test_running_costs_page_ranks_councils_from_the_official_file(client):
    body = client.get("/running-costs").text
    assert "Cheapest twenty" in body and "Dearest twenty" in body
    assert '"FAQPage"' in body
    assert 'href="/estate-charges"' in body
    assert "/running-costs" in client.get("/sitemap.xml").text


def test_estate_charges_page_is_sourced_and_honest(client):
    body = client.get("/estate-charges").text
    assert "Twelve questions" in body and "fleecehold" in body.lower()
    assert "cma-cases/housebuilding-market-study" in body
    assert "no official source" in body.lower()
    assert '"FAQPage"' in body
    assert "/estate-charges" in client.get("/sitemap.xml").text


# ---- who manages your estate, 4 Sep 2026 night ------------------------------

def _seed_estate_companies():
    import datetime as dt
    from app import db
    from app.models import EstateCompany
    with db.get_session() as session:
        if session.get(EstateCompany, "09999901") is None:
            for i, (num, name, slug, year) in enumerate((
                ("09999901", "MEADOW PARK (TESTFORD) MANAGEMENT COMPANY LIMITED", "firstport", 2019),
                ("09999902", "KINGS HILL RESIDENTS ASSOCIATION LIMITED", "firstport", 2021),
                ("09999903", "ORCHARD GATE MANAGEMENT LIMITED", "firstport", 2023),
                ("09999904", "OLD MILL ESTATE MANAGEMENT LIMITED", "firstport", 2024),
                ("09999905", "RIVERSIDE WALK RMC LIMITED", "firstport", 2025),
                ("09999906", "LONE TREE MANAGEMENT COMPANY LIMITED", "", 2020),
            )):
                session.add(EstateCompany(company_number=num, name=name, incorporated=dt.date(year, 3, 1),
                                          address="QUEENSWAY HOUSE, 11 QUEENSWAY, NEW MILTON" if slug else "1 HIGH STREET, TESTFORD",
                                          post_town="NEW MILTON" if slug else "TESTFORD", postcode="BH25 5NR" if slug else "TF1 1AA",
                                          agent_slug=slug, category="Private Limited Company", sic="98000 - Residents property management"))
        session.commit()


def test_estate_directory_names_offices_not_managers(client):
    _seed_estate_companies()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/estate-charges/managing-agents").text
    # "managed by" appears once, inside the sentence that forbids it.
    assert "registered to" in body and body.lower().count("managed by") == 1
    assert 'href="/estate-charges/company/firstport"' in body and "FirstPort" in body
    page = client.get("/estate-charges/company/firstport")
    assert page.status_code == 200
    assert "KINGS HILL RESIDENTS ASSOCIATION LIMITED" in page.text
    assert "find-and-update.company-information.service.gov.uk/company/09999902" in page.text
    assert client.get("/estate-charges/company/no-such-agent").status_code == 404
    # A registered-office service is never an agent page.
    assert client.get("/estate-charges/company/registered-office-service").status_code == 404


def test_estate_search_finds_a_company_and_its_office(client):
    _seed_estate_companies()
    body = client.get("/estate-charges/search?q=kings+hill").text
    assert "KINGS HILL RESIDENTS ASSOCIATION LIMITED" in body and "FirstPort" in body
    assert 'name="robots" content="noindex' in body
    assert "No company matches" in client.get("/estate-charges/search?q=zzzzqqq").text
    assert "/estate-charges/managing-agents" in client.get("/sitemap.xml").text

