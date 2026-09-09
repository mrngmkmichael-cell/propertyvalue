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


def test_a_retired_postcode_is_not_called_a_spelling_mistake(client, monkeypatch):
    """LS6 2AA and B29 6AA are real postcodes that Royal Mail withdrew in
    2018 and 2010. Both reached "Double-check the spelling" on 7 Sep 2026,
    which sends someone holding an old deed away from a search we can
    still half answer. postcodes.io puts the retirement date in the body
    of its own 404, so the date is sourced, not inferred."""
    from app import main as app_main
    from app.services import postcodes as postcodes_service

    async def _no_such_postcode(_pc):
        return None

    async def _retired(_pc):
        return {
            "postcode": "LS6 2AA", "year": 2018, "month_name": "May",
            "retired_on": "May 2018", "latitude": 53.820363, "longitude": -1.576503,
        }

    monkeypatch.setattr(app_main, "lookup_postcode", _no_such_postcode)
    monkeypatch.setattr(postcodes_service, "retired_postcode", _retired)

    r = client.get("/property?postcode=LS6+2AA")
    # Still a 404: the address cannot be reported on, and crawlers read
    # the status rather than the prose.
    assert r.status_code == 404
    body = r.text
    # Collapsed, because the sentence wraps across template lines.
    assert "retired in May 2018" in re.sub(r"\s+", " ", body)
    assert "Double-check the spelling" not in body
    # The district guide covers the same ground, so the search is not a
    # dead end.
    assert 'href="/area/LS6"' in body
    assert "postcodes.io" in body


def test_a_postcode_that_never_existed_still_gets_the_spelling_advice(client, monkeypatch):
    """The typo case is the common one and must not lose its message."""
    from app import main as app_main
    from app.services import postcodes as postcodes_service

    async def _no_such_postcode(_pc):
        return None

    async def _never_existed(_pc):
        return None

    monkeypatch.setattr(app_main, "lookup_postcode", _no_such_postcode)
    monkeypatch.setattr(postcodes_service, "retired_postcode", _never_existed)

    r = client.get("/property?postcode=ZZ99+9ZZ")
    assert r.status_code == 404
    assert "Double-check the spelling" in r.text


def test_the_postcode_headline_is_mono_and_keeps_its_space():
    """The h1 inherits letter-spacing -0.02em from the global heading
    rule, which closed the gap in "M1 1AE" until the headline read as
    "M11AE" at 375px and at desktop (7 Sep 2026). The postcode is data,
    so it takes the mono face and normal tracking."""
    import pathlib
    css = (pathlib.Path(__file__).resolve().parents[1] / "app/static/css/style.css").read_text(encoding="utf-8")
    block = css[css.index(".report-head h1 {"):]
    block = block[:block.index("}")]
    assert "var(--font-mono)" in block
    assert "letter-spacing: normal" in block


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


def test_pricing_page_lists_every_check_the_landing_page_claims(client, monkeypatch):
    """The pricing page's two tiers mirror the report card-for-card: free
    plus Premium equals the number the landing page commits to (41 on
    7 Sep 2026: 25 free, 16 Premium). The tier block only renders when
    billing is configured, as it is in production."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_m")
    monkeypatch.setenv("STRIPE_PRICE_ID_QUARTERLY", "price_q")
    home = client.get("/").text
    claimed = int(re.search(r'data-target="(\d+)"[^>]*>[\d,]+</span></p>\s*<p class="lx-about-stat-l">Checks per property', home).group(1))
    body = client.get("/premium").text
    assert body.count('class="lx-check"') == claimed
    free = int(re.search(r"(\d+) free on every report", body).group(1))
    assert f"{claimed - free} more with Premium" in body


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


def test_the_site_has_no_scheduled_send(client, monkeypatch):
    """Every change-alert email tells its reader it arrives only when
    something actually changed, never on a schedule. The weekly digest
    was the one feature that could have contradicted that, and it was
    removed on 9 Sep 2026 after zero of 48 real accounts opted in. This
    pins the promise rather than the feature: no route may exist that
    mails a list of people on a timer."""
    monkeypatch.setenv("ALERTS_CRON_SECRET", "s3cret")
    for path in ("/internal/send-weekly-digest", "/watchlist/weekly-digest"):
        r = client.post(path, headers={"x-alerts-secret": "s3cret"}, follow_redirects=False)
        assert r.status_code == 404, f"{path} still answers {r.status_code}"

    from app import watchlist
    assert not hasattr(watchlist, "digest_subscribers")
    assert not hasattr(watchlist, "set_weekly_digest")


def test_opening_a_report_keeps_the_property_and_says_so(client, monkeypatch):
    """Only 5 of 46 accounts had ever saved a property, 12 rows in total,
    while 35 of the 38 accounts that spent a free unlock opened exactly one
    property and never returned. The one thing both paying accounts had in
    common was coming back on another day, so the page to come back to now
    exists without anyone having to accept an offer first. It is said out
    loud on the report, with the way out beside it."""
    from app import auth, watchlist
    from app.db import get_session

    r = client.post("/signup", data={
        "email": "remembers@example.test", "password": "correct horse battery staple",
    }, follow_redirects=False)
    assert r.status_code in (302, 303), r.status_code
    with get_session() as db:
        user_id = auth.find_user_by_email(db, "remembers@example.test").id

    assert watchlist.list_items(user_id) == []
    body = client.get("/property?postcode=M1+1AE").text
    items = watchlist.list_items(user_id)
    assert [i["postcode"] for i in items] == ["M1 1AE"]
    assert "Kept in" in body and 'href="/watchlist"' in body
    assert 'action="/watchlist/remove"' in body

    # Opening it again is not a second row, and does not re-announce it.
    again = client.get("/property?postcode=M1+1AE").text
    assert len(watchlist.list_items(user_id)) == 1
    assert "Kept in" not in again


def test_remembering_never_overwrites_a_note_someone_typed(client):
    """The note is theirs. An automatic save must not touch an existing
    row, or a second visit would wipe what they wrote."""
    from app import auth, watchlist
    from app.db import get_session

    client.post("/signup", data={
        "email": "keeps-notes@example.test", "password": "correct horse battery staple",
    }, follow_redirects=False)
    with get_session() as db:
        user_id = auth.find_user_by_email(db, "keeps-notes@example.test").id

    watchlist.save_item(user_id, "M1 1AE", "", "chain free, offer in")
    assert watchlist.remember(user_id, "M1 1AE", "") is False
    assert watchlist.get_item(user_id, "M1 1AE", "")["note"] == "chain free, offer in"


def test_the_undo_only_ever_returns_to_this_site(client):
    """The remove button carries where to go back to. An open redirect is
    one careless form field away, and "//evil.example" is a relative URL
    to a browser."""
    from app import auth, watchlist
    from app.db import get_session

    client.post("/signup", data={
        "email": "undo-tester@example.test", "password": "correct horse battery staple",
    }, follow_redirects=False)
    with get_session() as db:
        user_id = auth.find_user_by_email(db, "undo-tester@example.test").id

    for target, expected in (
        ("/property?postcode=M1+1AE", "/property?postcode=M1+1AE"),
        ("//evil.example/", "/watchlist"),
        ("https://evil.example/", "/watchlist"),
        ("", "/watchlist"),
    ):
        watchlist.save_item(user_id, "M1 1AE", "", "")
        item_id = watchlist.get_item(user_id, "M1 1AE", "")["id"]
        r = client.post("/watchlist/remove", data={"item_id": item_id, "next": target},
                        follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == expected, target


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


def test_calculator_pages_render_but_no_longer_compete(client):
    """Both tool pages still work and are driven by the same file, so the
    tax bands have one home. What changed on 8 Sep 2026 is that they are
    noindexed and out of the sitemap: 1,562 Search Console impressions at
    an average position around 90 and not one click in three months, for
    two commodity pages holding none of this site's own data. They stay
    linked and usable, and they point at the running costs pages, which
    are the version of this question only we can answer."""
    for slug in ("stamp-duty-calculator", "mortgage-calculator"):
        body = client.get(f"/tools/{slug}").text
        assert "/static/js/calculators.js" in body, slug
        assert 'id="calc-price"' in body, slug
        assert 'content="noindex, follow"' in body, slug
        assert "/running-costs" in body, slug
    sitemap = client.get("/sitemap.xml").text
    assert "/tools/mortgage-calculator" not in sitemap
    assert "/tools/stamp-duty-calculator" not in sitemap
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


def test_no_em_dash_reaches_a_reader(client):
    """DESIGN.md forbids the em-dash in user-facing copy, and CLAUDE.md's
    first non-negotiable says a missing figure states the gap in words
    rather than showing a blank. A bare dash in a table cell was both at
    once, and on 9 Sep 2026 there were 37 of them across nine templates,
    including the report, the homepage and the pricing table. The
    templates and the site's own JavaScript are the two places a dash
    can reach a reader from, so both are checked here."""
    import pathlib

    for path in ("/", "/premium", "/privacy", "/running-costs", "/schools/admissions"):
        assert "—" not in client.get(path).text, f"{path} renders an em-dash"

    root = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for f in list((root / "templates").glob("*.html")) + list((root / "static" / "js").glob("*.js")):
        text = f.read_text(encoding="utf-8")
        if "—" in text or "&mdash;" in text:
            offenders.append(f.name)
    assert not offenders, f"em-dash in user-facing source: {offenders}"


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
    assert 'property="og:image" content="https://testserver/school/990002/catchment.png"' in body
    # The grade strip carries the admission figure.
    assert "Admitted from, 2025" in body
    # The school's own postcode leads to the running-costs table for it.
    assert 'href="/running-costs?postcode=' in body

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
    assert "catchment area: 1.7 miles, 2025" in title
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


def test_the_estate_directory_is_withdrawn_until_its_data_is_checked(client):
    """Michael, 7 Sep 2026: the office attribution looked inaccurate, so
    the directory is down for now. Nothing links to its routes and they
    are out of the sitemap; the table and importer are kept for when the
    data has been checked. From 8 Sep 2026 the routes redirect to the
    explainer instead of 404ing: Google holds these pages and people were
    still arriving on them, and /estate-charges answers what they came
    for. 301, because they only come back if the data is approved."""
    _seed_estate_companies()
    from app import main as app_main
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    assert app_main.ESTATE_DIRECTORY_ENABLED is False
    for path in ("/estate-charges/managing-agents", "/estate-charges/company/firstport", "/estate-charges/search?q=kings+hill"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 301, path
        assert r.headers["location"] == "/estate-charges", path
        assert "firstport" not in client.get(path).text.lower(), path
    assert "/estate-charges/managing-agents" not in client.get("/sitemap.xml").text
    assert "/estate-charges/managing-agents" not in client.get("/running-costs").text
    explainer = client.get("/estate-charges")
    assert explainer.status_code == 200 and "/estate-charges/managing-agents" not in explainer.text
    assert "/estate-charges/managing-agents" not in client.get("/llms.txt").text


def test_council_tax_table_lists_every_authority(client):
    body = client.get("/running-costs/council-tax").text
    assert "Adur" in body and "billing authorities" in body
    assert "Band A" in body and "Band H" in body
    assert "/running-costs/council-tax" in client.get("/sitemap.xml").text


def test_council_tax_finds_an_english_council_by_name():
    from app.services import council_tax
    by_name = council_tax.for_district(None, "Adur")
    assert by_name and by_name["authority"] == "Adur" and by_name["band_d"] > 1000
    assert council_tax.for_district(None, "No Such Council") is None


def test_running_costs_page_answers_a_postcode_on_the_spot(client, monkeypatch):
    """Michael typed a postcode on /running-costs and was sent to the
    report. The page now answers with the council's bands itself and
    links to the report's own running-costs line for the rest."""
    from app import main as app_main

    async def _lookup(_pc):
        return {"postcode": "BN15 8AA", "admin_district": "Adur", "codes": {"admin_district": "E07000223"},
                "latitude": 50.83, "longitude": -0.33}
    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    async def _certs(_pc):
        return [{"address": "1 Sea Lane", "rating": "C", "date": "2024-01-01", "certificate_number": "AAA"},
                {"address": "2 Sea Lane", "rating": "D", "date": "2023-01-01", "certificate_number": "BBB"}]

    async def _detail(number):
        return {"AAA": {"heating_cost_current": 700, "hot_water_cost_current": 150, "lighting_cost_current": 100, "current_band": "C"},
                "BBB": {"heating_cost_current": 900, "hot_water_cost_current": 200, "lighting_cost_current": 120, "current_band": "D"}}[number]

    async def _sales(_pc):
        return [{"address": "1 Sea Lane", "date": "2025-03-01", "amount": 300000, "tenure": "freehold"},
                {"address": "2 Sea Lane", "date": "2019-06-01", "amount": 250000, "tenure": "leasehold"}]
    monkeypatch.setattr(app_main.epc, "certificates_for_postcode", _certs)
    monkeypatch.setattr(app_main.epc, "certificate_detail", _detail)
    monkeypatch.setattr(app_main, "sold_prices_for_postcode", _sales)

    async def _hpi(*_a):
        return {"local_authority": {"name": "Adur", "average_price": 350000.0, "annual_change_pct": 2.5, "period": "2026-06-01"}}

    async def _zone(_lat, _lon):
        return {"zone": 1, "label": "Flood zone 1: low risk"}
    monkeypatch.setattr(app_main.hpi, "area_comparison", _hpi)
    monkeypatch.setattr(app_main.flood_zones, "zone_for", _zone)
    monkeypatch.setattr(app_main.rental, "rental_for_laua", lambda _c: {"la_name": "Adur", "period": "2026-07", "price_all": 1250, "change_all_pct": 3.0, "by_bedroom": [{"label": "2 bed", "price": 1300, "change_pct": 2.0}]})
    monkeypatch.setattr(app_main.area_stats, "income_for_msoa", lambda _c: {"here": 42000, "la_name": "Adur", "la_average": 45000, "region_name": "South East", "region_average": 47000})
    monkeypatch.setattr(app_main.broadband, "coverage_for_postcode", lambda _pc: {"label": "Gigabit", "gigabit_pct": 97.0, "ultrafast_pct": 98.0, "superfast_pct": 99.0, "below_uso_pct": 0.0})
    monkeypatch.setattr(app_main, "_district_price_rows_by_outcode", lambda: {"BN15": {"outcode": "BN15", "median": 320000, "count": 55, "low": 150000, "high": 900000, "district": "Adur"}})
    body = client.get("/running-costs?postcode=BN15+8AA").text
    assert "What it costs to live in BN15 8AA" in body
    assert "Band A" in body and "Band H" in body and "Adur" in body
    assert "1,220" in body and "2 homes with a certificate" in body   # median of 950 and 1,220
    assert "1</strong> freehold" in body and "1</strong> leasehold" in body
    assert "Prices paid here" in body and "300,000" in body and "in 2025" in body
    assert "Prices in BN15" in body and "320,000" in body
    assert "Prices across Adur" in body and "350,000" in body and "up 2.5%" in body
    assert "Rent" in body and "1,250" in body and "2 bed" in body
    assert "Household income" in body and "42,000" in body
    assert "A typical year" in body and "% of the typical household income" in body
    assert "Flood zone 1" in body and "Gigabit" in body
    # National context stays off the page once a postcode is answered.
    assert "Cheapest twenty" not in body
    assert "Cheapest twenty" in client.get("/running-costs").text

    # With a house number the table leads with that home's own EPC and sale.
    async def _detail_full(number):
        base = await _detail(number)
        return {**base, "dwelling_type": "semi-detached house", "total_floor_area": 92, "habitable_room_count": 4,
                "year_built": "1950-1966", "potential_band": "B", "heating_cost_potential": 500,
                "hot_water_cost_potential": 100, "lighting_cost_potential": 80}
    monkeypatch.setattr(app_main.epc, "certificate_detail", _detail_full)
    body = client.get("/running-costs?postcode=BN15+8AA&house_number=2+Sea").text
    assert "2 Sea Lane" in body and "92 m" in body and "EPC band D" in body
    assert "Energy, this home" in body and "1,220" in body and "680" in body   # now, and after improvements
    assert "This home's last sale" in body and "250,000" in body and "2019" in body and "Leasehold" in body
    assert "this home's own energy estimate" in body
    # The three groups, and stamp duty on the home's own last sale (250,000):
    # 2% of the 125,000 above the nil band = 2,500; nil for a first-time buyer; 15,000 with the surcharge.
    assert "Every year" in body and "Once, when you buy" in body and "Worth knowing" in body
    # The map beside the box, pinned at the postcode centre (Leaflet branch in tests: no Google key).
    assert 'id="rc-map"' in body and "window.RC_MAP = { lat: 50.83" in body and "leaflet.js" in body
    assert 'id="rc-map"' not in client.get("/running-costs").text
    assert "Stamp duty" in body and "2,500" in body and "15,000" in body
    from app.main import _stamp_duty
    assert _stamp_duty(250000) == 2500 and _stamp_duty(250000, first_time=True) == 0
    assert _stamp_duty(400000, first_time=True) == 5000 and _stamp_duty(600000, first_time=True) is None
    assert _stamp_duty(1_000_000) == 2500 + 33750 + 7500
    assert 'name="house_number"' in body
    missing = client.get("/running-costs?postcode=BN15+8AA&house_number=99").text
    assert "No EPC or recorded sale matched" in missing
    assert 'href="/property?postcode=BN15%208AA#running-costs"' in body
    assert 'action="/running-costs"' in body


# ---- school search on the admissions index, 5 Sep 2026 -------------------

def test_admissions_index_has_a_school_search_that_finds_schools(client):
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    index = client.get("/schools/admissions").text
    assert 'action="/schools/admissions/search"' in index and 'id="sa-suggest"' in index
    assert "prefers-reduced-motion" in index  # the typing placeholder respects it
    page = client.get("/schools/admissions/search?q=riverside")
    assert page.status_code == 200
    assert 'href="/school/990002/riverside-academy"' in page.text and "Manchester" in page.text
    assert 'name="robots" content="noindex' in page.text
    assert "No school matches" in client.get("/schools/admissions/search?q=zzzqqq").text
    api = client.get("/api/school-search?q=river").json()
    assert api["results"][0]["url"] == "/school/990002/riverside-academy" and api["results"][0]["has_page"]
    assert client.get("/api/school-search?q=r").json() == {"results": []}
    # The literal path wins over the council catch-all.
    assert client.get("/schools/admissions/search").status_code == 200
    # A school with no published distance is found too, and opens its area on the guide.
    from app import db
    from app.models import School
    with db.get_session() as session:
        if session.get(School, 990091) is None:
            session.add(School(urn=990091, name="Willow Bank Primary School", phase="Primary", type_name="Community school",
                               postcode="LS6 2AB", latitude=53.82, longitude=-1.57, ofsted_rating=2, ofsted_rating_label="Good"))
            session.commit()
    api = client.get("/api/school-search?q=willow+bank").json()
    assert api["results"][0]["url"] == "/schools/guide?q=LS6+2AB" and api["results"][0]["has_page"] is False
    page = client.get("/schools/admissions/search?q=willow+bank").text
    assert 'href="/schools/guide?q=LS6+2AB"' in page and "opens its area on the guide" in page
    # The same box sits on the schools guide.
    guide = client.get("/schools/guide").text
    assert 'id="sa-suggest"' in guide and "Find a school by name" in guide


def test_the_area_lead_answers_the_question_in_sentences(client):
    """People search "is M20 a good place to live" and "M20 area reviews"
    and landed on a page that opened with a House prices heading. The lead
    is the same figures already further down, each naming its source, and
    no verdict of our own."""
    from app import main as app_main

    lead = app_main._area_lead("M20", {
        "local_sales": {"enough_for_median": True, "median": 412500, "count": 63},
        "hpi": {"local_authority": {"name": "Manchester", "annual_change_pct": 3.4}},
        "landscape": {"good_or_better_pct": 84, "total_schools": 61, "radius_miles": 3},
        "flood_zone": {"label": "Flood Zone 1, low risk"},
        "finance": {"name": "Manchester", "latest_label": "2026-27",
                    "history": [{"band_d": 1978.0}]},
        "crime": {"total": 214, "month": "May 2026",
                  "by_category": [{"category": "Violence and sexual offences"}]},
    })
    joined = " ".join(lead)
    assert "\u00a3412,500" in joined and "63 recorded sales" in joined
    assert "up 3.4% on a year ago" in joined
    assert "84%" in joined and "Ofsted" in joined
    assert "Environment Agency" in joined and "MHCLG" in joined and "Police.uk" in joined
    # Every sentence names where it came from, and none of them judges.
    assert all(s.endswith(".") for s in lead)
    for word in ("good place", "desirable", "sought-after", "leafy", "vibrant"):
        assert word not in joined.lower(), word


def test_the_area_lead_drops_a_sentence_rather_than_inventing_one(client):
    """The site's rule everywhere: a source with nothing for this area
    means one fewer sentence, never a placeholder or an estimate."""
    from app import main as app_main

    assert app_main._area_lead("ZZ9", {}) == []
    only_crime = app_main._area_lead("ZZ9", {"crime": {"total": 1}})
    assert len(only_crime) == 1 and "1 crime within" in only_crime[0]


def test_the_area_guide_renders_its_lead(client):
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/area/AB12").text
    assert 'class="area-lead"' in body
    # Above the first heading, which is what the whole point was.
    assert body.index('class="area-lead"') < body.index("<h2>House prices</h2>")


def test_outcode_private_school_pages_point_at_the_council_page(client):
    """276 of these pages took 4,860 Search Console impressions in three
    months, 46% of the site's, for 4 clicks: seven Birmingham outcodes
    bidding against each other and against /schools/independent/birmingham
    for one town-shaped query. They canonical to the council page where
    the register knows the council by the same name, and they are out of
    the sitemap. Still live, still linked."""
    _seed_independent_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    r = client.get("/area/M14/private-schools")
    assert r.status_code == 200
    import re
    canonical = re.search(r'<link rel="canonical" href="([^"]+)"', r.text).group(1)
    assert canonical.endswith("/schools/independent/manchester"), canonical
    assert "/area/M14/private-schools" not in client.get("/sitemap.xml").text
    # The page says the same thing to a reader that it says to a crawler.
    assert 'href="/schools/independent/manchester"' in r.text


def test_a_404_is_counted_so_it_can_be_found(client):
    """Search Console reported 1,161 missing pages on 8 Sep 2026 and will
    not say which. Path and count, in memory, nothing that identifies a
    visitor. Both shapes of 404 are counted: raised, and returned as a
    template response with a 404 status."""
    from app import main as app_main

    app_main._missing_paths.clear()
    client.get("/no-such-page-at-all")
    client.get("/no-such-page-at-all")
    client.get("/area/NOTAPOSTCODE")   # returns a template, does not raise
    assert app_main._missing_paths["/no-such-page-at-all"] == 2
    assert "/area/NOTAPOSTCODE" in app_main._missing_paths


def test_the_missing_path_list_cannot_be_flooded_out(client):
    """A scanner walking /wp-admin/... must not push the real 404s out of
    a list that is read by eye."""
    from app import main as app_main

    app_main._missing_paths.clear()
    app_main._record_missing("/the-one-that-matters")
    for i in range(app_main._MISSING_PATHS_CAP + 50):
        app_main._record_missing(f"/wp-admin/{i}")
    assert len(app_main._missing_paths) == app_main._MISSING_PATHS_CAP
    assert "/the-one-that-matters" in app_main._missing_paths
    app_main._missing_paths.clear()


def test_area_guide_leads_with_an_address_check(client, monkeypatch):
    """Search lands most visitors on area guides; the first thing offered
    is the report for an address there. It used to have to beat an
    account-only Follow button to that spot; the Follow row was removed
    on 7 Sep 2026 after zero accounts ever used it, so the check is now
    simply the only offer on the page."""
    from app import main as app_main
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/area/AB12").text
    assert 'id="area-check-postcode"' in body and 'placeholder="e.g. AB12 1AA"' in body
    assert "follow-row" not in body and "/districts/follow" not in body


def test_school_page_leads_with_the_figure_then_the_checker(client):
    """The published distance is the answer the search was asking for, so
    it comes first and the postcode box comes under it. Until 8 Sep 2026
    the order was the other way round and on a 375px screen the figure
    sat at document y=732, below the fold behind the header, the share
    row and the box. Mobile is 27 of the site's 37 clicks."""
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/school/990002/riverside-academy").text
    dek = body.index('class="dek"')
    figure = body.index('class="scorecard-row"')
    checker = body.index('id="check-postcode-top"')
    share = body.index('class="share-send-row"')
    assert dek < figure < checker < share
    assert 'action="/school/990002/riverside-academy#verdict"' in body


def test_the_school_distance_reads_the_same_everywhere_on_its_page(client):
    """The page used to render three decimals while its own title, badge
    and share text used two, so the tab said 2.05 miles and the tile said
    2.048. The third decimal was never the council's precision either:
    1,072 of the 3,627 stored figures carry six decimals because the
    council published metres and the importer converted. One rounding for
    every surface a reader sees; the map circle keeps the full value."""
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    from app.services import schools_db
    profile = schools_db.admission_profile(990002)
    body = client.get("/school/990002/riverside-academy").text
    label = f"{profile['miles']:.2f}".rstrip("0").rstrip(".")
    assert f"{label} mi<" in body
    assert f"against {label} miles" in body
    # The precise figure survives where it is maths, not copy.
    assert f"miles: {profile['miles']}" in body


def test_council_hub_offers_an_address_check(client):
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/schools/admissions/manchester").text
    assert 'id="hub-check-postcode"' in body and "against every Manchester school" in body



def test_a_sixth_form_college_is_not_counted_as_a_secondary_school():
    """A reader reported on 23 Aug 2026 that Worcester Sixth Form College
    was listed under the secondary schools near WR4 0EW. GIAS gives it
    PhaseOfEducation "16 plus", which the phase grouping folded into
    Secondary, so 82 sixth form colleges nationally appeared as secondary
    schools. They take nobody under 16."""
    from app.services import schools_db

    assert schools_db._phase_group("16 plus") is None
    assert schools_db._phase_group("Secondary") == "Secondary"
    assert schools_db._phase_group("All-through") == "Secondary"
    assert schools_db._is_sixteen_plus("16 plus", "Academy 16-19 converter")
    assert schools_db._is_sixteen_plus("16 plus", "Free schools 16 to 19")
    assert schools_db._is_sixteen_plus("Not applicable", "Sixth form centres")
    assert schools_db._is_sixteen_plus("16 plus", "Further education")
    assert not schools_db._is_sixteen_plus("Secondary", "Academy converter")
    assert not schools_db._is_sixteen_plus("Primary", "Community school")


def test_admin_dashboard_renders_for_the_owner_and_404s_for_everyone_else(client, monkeypatch):
    """/admin had no test at all, so a Jinja slip or a bad query on it
    would only be found by opening it. It is one page, gated on one
    email, and it is the page the whole business is read from."""
    from app import auth
    from app.db import get_session

    # A stranger is not told the route exists.
    client.cookies.clear()
    assert client.get("/admin").status_code == 404

    monkeypatch.setenv("ADMIN_EMAIL", "boss@example.test")
    client.post("/signup", data={"email": "boss@example.test",
                                 "password": "correct horse battery staple"},
                follow_redirects=False)
    r = client.get("/admin")
    assert r.status_code == 200
    body = r.text
    assert "Daily overview" in body
    # The 7 Sep 2026 addition: the two tables that say whether one free
    # report per account is holding.
    assert "Is one free report enough?" in body
    assert "Same address, more than one account, same day" in body
    assert "Free reports by mailbox provider" in body


def test_running_costs_leads_with_the_answer_once_there_is_one(client):
    """It became the busiest single page on the site on 8 Sep 2026, and
    at 375px the answer to the postcode someone had just typed sat at
    y=1465, below the introduction, the form and a 300px map. The
    heading is now the answer, and the checker is ordered after it."""
    body = client.get("/running-costs", params={"postcode": "M1 1AE"}).text
    assert "<h1>What it costs to live in M1 1AE" in body
    # Anchored on the class attribute, not the class name: the critical
    # CSS is inlined into every page, so the bare name matches the
    # stylesheet on a page that does not use it.
    assert 'class="rc-flow rc-flow-answered"' in body
    # The long orientation paragraph belongs to someone with no answer.
    assert "A listing shows the price." not in body

    fresh = client.get("/running-costs").text
    assert "<h1>What it costs to live there</h1>" in fresh
    assert "A listing shows the price." in fresh
    assert 'class="rc-flow"' in fresh


def test_the_council_tax_pages_can_check_an_address(client):
    """350 pages built on 8 Sep 2026, the one indexable family with no
    postcode box, while area guides, school pages and council hubs each
    got one on 5 Sep 2026 because that is where people land."""
    body = client.get("/running-costs/council-tax/manchester").text
    assert 'action="/property"' in body
    assert 'name="src" value="council-tax"' in body
    assert "Check an address in Manchester" in body


def test_audience_split_separates_a_crawl_from_an_audience():
    """8 Sep 2026: 912 recorded views, 479 of them single visits to
    distinct school and area guide pages, while the busiest human page
    took 74. Reading 912 as an audience turned a traffic fall into what
    looked like a conversion fall."""
    from app.main import _audience_split

    # A crawler walking a family in order: every page once.
    crawl = {f"/school/{i}/x": 1 for i in range(100)}
    assert _audience_split(crawl) == (0, 100)

    # People: a few pages, read repeatedly.
    people = {"/": 40, "/running-costs": 30, "/property": 12}
    assert _audience_split(people) == (82, 0)

    # The real shape is both at once, and the split keeps them apart.
    mixed = {**crawl, **people}
    assert _audience_split(mixed) == (82, 100)
    assert sum(_audience_split(mixed)) == sum(mixed.values())


def test_a_report_search_records_where_it_started(client, monkeypatch):
    """923 distinct school pages were crawled in three days for at most 7
    human views each, while report starts tracked the homepage. Nothing
    recorded which page a search came from, so whether the ranked pages
    feed the funnel was unanswerable. The marker is a fixed list, never
    free text, because these values become rows in page_views."""
    from sqlalchemy import func, select

    from app.db import get_session
    from app.models import PageView

    def _count(path):
        with get_session() as db:
            return db.scalar(select(func.count()).select_from(PageView)
                             .where(PageView.path == path)) or 0

    # The test client names itself "testclient", which the crawler
    # filter excludes on purpose, so this has to look like a browser.
    browser = {"user-agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Safari/537.36"}
    # A district rather than a full postcode: it redirects to the guide
    # without building a report, and the marker is recorded either way,
    # because a search that started on a landing page started there
    # whichever page it lands on.
    search = {"postcode": "M1", "src": "council-tax"}

    before = _count("/from/council-tax")
    r = client.get("/property", params=search, headers=browser, follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/area/M1"
    assert _count("/from/council-tax") == before + 1

    # Anything not on the list writes nothing at all.
    client.get("/property", params={"postcode": "M1", "src": "../../evil"},
               headers=browser, follow_redirects=False)
    with get_session() as db:
        paths = db.scalars(select(PageView.path)
                           .where(PageView.path.like("/from/%"))).all()
    assert all(p == "/from/council-tax" for p in paths), paths


def test_the_marker_does_not_break_the_anonymous_report_cache(client):
    """Adding a query param to every landing-page search would have made
    each one a cache miss, which is a worse trade than the measurement
    is worth. src is allowed through the cache guard precisely because
    it changes nothing about the HTML."""
    from starlette.requests import Request

    from app.main import _anon_cacheable

    def _req(query):
        return Request({"type": "http", "method": "GET", "path": "/property",
                        "query_string": query.encode(), "headers": [],
                        "session": {}})

    assert _anon_cacheable(_req("postcode=M1+1AE"))
    assert _anon_cacheable(_req("postcode=M1+1AE&src=area-guide"))
    assert not _anon_cacheable(_req("postcode=M1+1AE&report=thanks"))


def test_admin_counts_one_address_unlocked_by_two_accounts(client, monkeypatch):
    """The pattern that prompted this: three accounts, one address, 45
    minutes (OX3 0SG number 7, 6 Sep 2026). With no rows the section says
    so in words rather than showing an empty table."""
    from sqlalchemy import select

    from app import auth
    from app.db import get_session
    from app.models import PremiumUnlock, User

    monkeypatch.setenv("ADMIN_EMAIL", "boss2@example.test")
    client.cookies.clear()
    client.post("/signup", data={"email": "boss2@example.test",
                                 "password": "correct horse battery staple"},
                follow_redirects=False)

    empty = client.get("/admin").text
    assert "No address has been unlocked by more than one account" in empty

    with get_session() as session:
        for addr in ("one@example.test", "two@example.test"):
            session.add(User(email=addr, password_hash="x"))
        session.commit()
        ids = [u.id for u in session.scalars(
            select(User).where(User.email.in_(("one@example.test", "two@example.test")))
        )]
        for uid in ids:
            session.add(PremiumUnlock(user_id=uid, postcode="OX3 0SG", house_number="7"))
        session.commit()

    body = client.get("/admin").text
    assert "OX3 0SG, 7" in body
    assert "example.test" in body  # the domain table counted them too


# ---- Payload v20 on the area guide (8 Sep 2026) ----------------------------
V20_STUBS = {
    "census_change": {"lsoa": "E01005200", "has_2011": True, "has_2021": True, "merged_from": 1, "residents_2021": 1834,
                      "residents_change_pct": 6.1, "biggest": {"key": "private_rented", "short": "Private renting", "change": 9.4},
                      "rows": [{"key": "private_rented", "label": "Households renting privately", "in_2011": 21.0, "in_2021": 30.4, "change": 9.4, "england_change": 3.6}]},
    "bus": {"count": 3, "radius_m": 800, "ref_weekday": "2026-09-08", "ref_sunday": "2026-09-13", "feed_date": "2026-09-07",
            "best": {"name": "Wilmslow Road", "distance_m": 140, "weekday_day_per_hour": 22.5, "weekday_eve_per_hour": 9.0,
                     "sunday_day_per_hour": 12.0, "weekday_first": "05:12", "weekday_last": "23:58", "routes": ["42", "43", "142"]},
            "stops": []},
    "health": {"count": 5, "radius_m": 2000, "median_patients_per_qualified_gp": 2186,
               "patients_date": "1 August 2026", "workforce_date": "31 July 2026",
               "nearest": {"name": "Rusholme Health Centre", "distance_m": 310, "patients": 12450, "patients_per_qualified_gp": 2610},
               "practices": [{"name": "Rusholme Health Centre", "distance_m": 310, "patients": 12450, "patients_per_qualified_gp": 2610}],
               "trusts": [{"name": "Manchester University NHS Foundation Trust", "type1_within_4h_pct": 58.2, "all_within_4h_pct": 71.0, "period": "July 2026"}]},
    "finance": {"name": "Manchester", "latest_label": "2026-27", "rise_latest": 4.99, "median_rise_latest": 4.99, "as_of": "8 September 2026",
                "history": [{"label": "2025-26", "band_d": 2145.7, "rise": 4.99}, {"label": "2026-27", "band_d": 2252.8, "rise": 4.99}],
                "efs": [], "county_efs": [], "county_name": "", "s114": []},
}


def _fresh_guide(client, monkeypatch, outcode: str, stubs: dict) -> str:
    """Render a guide with the four v20 services stubbed, from a cold cache
    so the builder runs rather than an earlier test's payload."""
    from app import db, main as app_main
    from app.models import PageCache
    from app.services import _cache
    monkeypatch.setattr(app_main.census_change, "for_lsoa", lambda lsoa: stubs["census_change"])
    monkeypatch.setattr(app_main.bus_service, "stops_near", lambda lat, lon, radius_m=500: stubs["bus"])
    monkeypatch.setattr(app_main.health_services, "near", lambda lat, lon, radius_m=2000: stubs["health"])
    monkeypatch.setattr(app_main.council_finance, "for_council", lambda code, name="", county="": stubs["finance"])
    with db.get_session() as session:
        for row in session.query(PageCache).filter(PageCache.cache_key.like(f"area_guide:%:{outcode}")).all():
            session.delete(row)
        session.commit()
    _cache._store.clear(); _cache._bytes = 0
    r = client.get(f"/area/{outcode}")
    assert r.status_code == 200
    return r.text


def test_the_guide_carries_the_v20_sections(client, monkeypatch):
    body = _fresh_guide(client, monkeypatch, "AB12", V20_STUBS)
    assert "How AB12 has changed since 2011" in body and "Households renting privately" in body and "+9.4 pts" in body
    assert "Buses from the centre of AB12" in body and "22.5 buses an hour" in body and "42, 43, 142" in body
    assert "GP practices and A&amp;E" in body and "2,610 patients per fully qualified GP" in body and "58.2%" in body
    assert "GP practice (1 August 2026)" in body and "workforce (31 July 2026)" in body   # the dates sit on the summary, not the practice
    assert "Council tax and the council" in body and "2,252" in body and "No exceptional financial support" in body


def test_the_guide_stays_quiet_without_the_v20_sources(client, monkeypatch):
    body = _fresh_guide(client, monkeypatch, "AB12", {"census_change": None, "bus": None, "health": None, "finance": None})
    assert "since 2011" not in body and "Buses from the centre" not in body
    assert "GP practices and A&amp;E" not in body and "the council's finances" not in body


# ---- One council tax page per billing authority (8 Sep 2026) -----------------
def test_each_council_has_a_council_tax_page(client):
    from app.services import council_tax
    pages = council_tax.pages()
    assert len(pages) >= 340 and "manchester" in pages and "aberdeen-city" in pages and "cardiff" in pages
    r = client.get("/running-costs/council-tax/manchester")
    assert r.status_code == 200
    body = r.text
    assert "Council tax in Manchester" in body and "Every band in Manchester" in body
    assert "Band A" in body and "Band H" in body and "highest Band D of the" in body
    assert "Six years of Band D" in body                       # England: the MHCLG history
    assert "FAQPage" in body and "per month" in body
    assert 'href="/area/M14"' in body                          # the guides inside the council
    assert client.get("/running-costs/council-tax/no-such-council").status_code == 404
    scot = client.get("/running-costs/council-tax/aberdeen-city").text
    assert "Scottish Government" in scot and "Scottish Assessors" in scot and "Six years of Band D" not in scot
    listing = client.get("/running-costs/council-tax").text
    assert 'href="/running-costs/council-tax/manchester"' in listing
    assert "/running-costs/council-tax/manchester" in client.get("/sitemap.xml").text


def test_healthz_answers_without_touching_the_database(client, monkeypatch):
    """Render polls this path before routing traffic to a new deploy, so
    it must be true the instant the process is up and must never depend
    on Neon being reachable."""
    from app import db
    monkeypatch.setattr(db, "get_session", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no database")))
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["cache-control"] == "no-store"


# ---- The 404 shapes Search Console listed on 8 Sep 2026 -------------------------
def test_the_four_404_shapes_from_search_console(client, fake_place):
    from app import db, main as app_main
    from app.models import School, SchoolDetail
    # 1. An outcode on its own goes to the guide; a full postcode still reports.
    r = client.get("/property?postcode=M14", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/area/M14"
    assert client.get("/property?postcode=m14+", follow_redirects=False).status_code == 301
    assert client.get("/property?postcode=ZZ9", follow_redirects=False).status_code != 301
    assert client.get("/property?postcode=M14&house_number=12", follow_redirects=False).status_code != 301
    # 2. The withdrawn language switcher sends each link to the page it wrapped.
    r = client.get("/set-language?lang=es&next=/area/PO16", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/area/PO16"
    r = client.get("/set-language?lang=fr&next=//evil.example/x", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/"
    # 3. A bare school website renders absolute now, and the crawled
    #    relative form bounces to the school, but only for a recorded site.
    with db.get_session() as session:
        session.merge(School(urn=900301, name="Bare Website Primary", phase="Primary", type_name="Community school",
                             postcode="M14 5TG", latitude=53.45, longitude=-2.22))
        session.merge(SchoolDetail(urn=900301, town="Manchester", website="www.bare-website-primary.sch.uk", local_authority="Manchester"))
        session.commit()
    assert app_main._external_url("www.bare-website-primary.sch.uk") == "https://www.bare-website-primary.sch.uk"
    assert app_main._external_url("https://x.org/") == "https://x.org/" and app_main._external_url("") == ""
    r = client.get("/schools/www.bare-website-primary.sch.uk", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "https://www.bare-website-primary.sch.uk/"
    assert client.get("/schools/www.not-a-school.example", follow_redirects=False).status_code == 404
    assert client.get("/schools/grammar").status_code == 200          # the literal routes still win
    # 4. The guide no longer links to an outcode-only report.
    body = client.get("/area/AB12").text
    assert "/property?postcode=AB12" not in body and 'href="/#postcode=AB12"' in body
    assert "location.hash" in client.get("/").text


# ---- Every check, side by side (9 Sep 2026) ---------------------------------
def _saved_homes(client, email: str, premium: bool) -> list[int]:
    """A signed-in user with two saved homes; Premium when asked."""
    from app import db
    from app.models import User, WatchlistItem
    _signed_in(client, email)
    with db.get_session() as session:
        user = session.query(User).filter(User.email == email).one()
        user.is_premium = premium
        ids = []
        for postcode, hn in (("M14 5TG", "12"), ("M14 5TG", "14")):
            item = WatchlistItem(user_id=user.id, postcode=postcode, house_number=hn, note="")
            session.add(item)
            session.flush()
            ids.append(item.id)
        session.commit()
    return ids


def test_every_check_side_by_side_is_premium(client, fake_report, monkeypatch):
    """The full comparison runs the report's own gather for each saved
    home and lays the PDF's rows side by side. Premium sees it; a free
    account sees what it is and no gather runs for them; signed out is
    sent to log in."""
    from app import main as app_main
    from app.services import _cache
    r = client.get("/watchlist/compare/full?item_ids=1", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]

    # A free account: the page explains, and never gathers.
    ids = _saved_homes(client, "free-compare@example.com", premium=False)
    async def _never(*a, **k):
        raise AssertionError("the gather must not run for a locked page")
    monkeypatch.setattr(app_main, "_full_property_gather", _never)
    body = client.get(f"/watchlist/compare/full?item_ids={ids[0]}&item_ids={ids[1]}").text
    assert "Premium puts every check on the report next to each other" in body
    assert 'href="/premium"' in body and f"item_ids={ids[0]}" in body and "12, M14 5TG" in body
    client.cookies.clear()

    # Premium: rows from the fake gather, side by side, with the differ count.
    fake_report()
    async def _rc(where, house_number=""):
        return {"sales": {"latest_year": 2021, "latest_amount": 250000 if house_number == "12" else 310000}}
    monkeypatch.setattr(app_main, "_running_costs_for_postcode", _rc)
    ids = _saved_homes(client, "paid-compare@example.com", premium=True)
    _cache._store.clear(); _cache._bytes = 0
    body = client.get(f"/watchlist/compare/full?item_ids={ids[0]}&item_ids={ids[1]}").text
    assert "Every check, side by side" in body and "Sold prices at this postcode" in body
    assert "Last sale £250,000 in 2021" in body and "Last sale £310,000 in 2021" in body
    assert 'class="compare-row compare-differs"' in body and 'id="differences-only"' in body
    assert "12, M14 5TG" in body and "14, M14 5TG" in body
    assert "checks for 2 homes" in body

    # The light comparison and My properties both lead here.
    light = client.get(f"/watchlist/compare?item_ids={ids[0]}&item_ids={ids[1]}").text
    assert f'href="/watchlist/compare/full?item_ids={ids[0]}&item_ids={ids[1]}"' in light
    assert 'formaction="/watchlist/compare/full"' in client.get("/watchlist").text
