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
    """The banner promises free Premium reports; that number must match
    what a new account actually gets."""
    from app import auth
    body = client.get("/").text
    assert f"get {auth.FREE_PREMIUM_UNLOCKS} full Premium property reports" in body


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


def test_pricing_page_lists_all_37_checks(client, monkeypatch):
    """The pricing page's two tiers mirror the report card-for-card:
    22 free + 15 Premium = the 37 the hero claims. The tier block only
    renders when billing is configured, as it is in production."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_m")
    monkeypatch.setenv("STRIPE_PRICE_ID_QUARTERLY", "price_q")
    body = client.get("/premium").text
    assert body.count('class="lx-check"') == 37
    assert "22 free on every report" in body
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
