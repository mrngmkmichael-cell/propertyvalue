"""The seven ideas of the 8 Oct 2026 brainstorm (Michael: "Do all")."""
import asyncio
import datetime
import html
import pathlib
import re

from app import auth, db
from app import main as app_main
from app.services import email as email_service
from app.services import orientation
from tests.conftest import fake_location
from tests.test_audit_fixes_17sep import _banner, _e3_page, _flat
from tests.test_email_verification import _signup

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"


# ---- 3. Land Registry's codes and capitals, in words ---------------------

def test_a_home_type_reads_as_words():
    assert app_main._home_type("flat-maisonette") == "Flat or maisonette"
    assert app_main._home_type("semi-detached") == "Semi-detached"
    assert app_main._home_type("terraced") == "Terraced"
    assert app_main._home_type("") == "Not recorded"
    assert app_main._home_type(None) == "Not recorded"


def test_a_sold_address_reads_as_an_address_is_written():
    assert app_main._sale_address("FLAT 4 21 BAINBRIGGE ROAD") == "Flat 4 21 Bainbrigge Road"
    assert app_main._sale_address("12A ST JOHN'S CLOSE") == "12A St John's Close"
    assert app_main._sale_address("3 O'BRIEN COURT") == "3 O'Brien Court"
    assert app_main._sale_address("7 SMITH-JONES WAY") == "7 Smith-Jones Way"
    # Already mixed case is someone else's spelling and is left alone.
    assert app_main._sale_address("Flat 2, Park Lane Central") == "Flat 2, Park Lane Central"


def test_the_sold_tables_use_both_filters():
    guide = (TEMPLATES / "area_guide.html").read_text(encoding="utf-8")
    assert "{{ s.property_type | home_type }}" in guide and "{{ s.address | sale_address }}" in guide
    comps = (TEMPLATES / "comparables.html").read_text(encoding="utf-8")
    assert "{{ tx.property_type | home_type }}" in comps and "{{ tx.address | sale_address }}" in comps
    assert 'class="capitalize" data-st="half"' not in comps  # would read "Flat Or Maisonette"
    report = (TEMPLATES / "property.html").read_text(encoding="utf-8")
    assert "{{ tx.address | sale_address }}" in report


# ---- 4. Titles Google does not cut ---------------------------------------

def test_the_alternatives_title_fits(client):
    body = client.get("/alternatives").text
    title = html.unescape(re.search(r"<title>(.*?)</title>", body, re.S).group(1).strip())
    assert len(title) <= 65, title


def test_the_audit_fails_a_long_title():
    audit = (ROOT / "scripts" / "audit_site.py").read_text(encoding="utf-8")
    assert '"title over 65 characters"' in audit


# ---- 6. The council's hub beside the school's figure ---------------------

def test_the_school_page_links_its_council_hub_beside_the_figure(client):
    body = _e3_page(client, "")
    start = body.index("<h2>Will an address get in?</h2>")
    section = body[start:body.index("</section>", start)]
    assert 'class="section-sub school-hub-link"><a href="/schools/admissions/' in section
    # Above the reasons, which are where most readers stop.
    assert section.index("school-hub-link") < section.index('<details class="howto"')


# ---- 1 and 2. The wall: plans on it, shorter, counted by day -------------

def _spent_and_walled(client, fake_report, monkeypatch, email, first_pc, wall_pc):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    first_out, wall_out = first_pc.split()[0], wall_pc.split()[0]
    fake_report(location=fake_location(postcode=first_pc, outcode=first_out))
    assert _signup(client, email).status_code == 303
    client.get("/property?postcode=" + first_pc.replace(" ", "+"))
    r = client.post("/property/unlock", data={"postcode": first_pc, "house_number": "3"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")
    fake_report(location=fake_location(postcode=wall_pc, outcode=wall_out))
    from app.services import _cache
    _cache.set(("property_search_gather", wall_pc, "9"), {"warm": True})
    browser = {"user-agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Safari/537.36"}
    return lambda: client.get("/property?postcode=" + wall_pc.replace(" ", "+") + "&house_number=9", headers=browser).text


def test_the_wall_carries_both_plans_to_checkout_with_this_home(client, fake_report, monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_m")
    monkeypatch.setenv("STRIPE_PRICE_ID_QUARTERLY", "price_q")
    view = _spent_and_walled(client, fake_report, monkeypatch, "wall-plans@customer.test", "M21 7AA", "M22 7AA")
    banner = _banner(view())
    forms = re.findall(r'<form action="/premium/checkout" method="post" class="paywall-banner-form">(.*?)</form>', banner, re.S)
    assert len(forms) == 2
    assert 'name="plan" value="quarterly"' in forms[0] and 'name="plan" value="monthly"' in forms[1]
    for form in forms:
        assert '<input type="hidden" name="home" value="M22 7AA">' in form
        assert '<input type="hidden" name="hn" value="9">' in form
    assert '<a class="paywall-banner-plans-link" href="/premium?home=M22+7AA&amp;hn=9">See plans</a>' in banner \
        or '<a class="paywall-banner-plans-link" href="/premium?home=M22+7AA&hn=9">See plans</a>' in banner


def test_the_wall_without_a_configured_plan_keeps_its_one_link(client, fake_report, monkeypatch):
    monkeypatch.delenv("STRIPE_PRICE_ID_MONTHLY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID_QUARTERLY", raising=False)
    view = _spent_and_walled(client, fake_report, monkeypatch, "wall-noplans@customer.test", "M23 7AA", "M24 7AA")
    banner = _banner(view())
    assert "/premium/checkout" not in banner
    assert 'class="paywall-banner-cta" href="/premium?home=M24+7AA' in banner


def test_six_walls_in_one_evening_are_not_six_returns(client, fake_report, monkeypatch):
    view = _spent_and_walled(client, fake_report, monkeypatch, "wall-evening@customer.test", "M25 7AA", "M26 7AA")
    for _ in range(4):
        banner = _flat(_banner(view()))
        assert "You've used your free report." in banner
        assert "Welcome back." not in banner and "reached this wall" not in banner
        assert "28 websites" not in banner
    with db.get_session() as session:
        uid = auth.find_user_by_email(session, "wall-evening@customer.test").id
        session.add(app_main.PageView(path=app_main.PAYWALL_PATH, user_id=uid,
                                      created_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)))
        session.commit()
    banner = _flat(_banner(view()))
    assert "Welcome back." in banner and "would be about &pound;" in banner


def test_the_uk_day_starts_at_uk_midnight():
    start = app_main._uk_day_start()
    assert start.tzinfo is not None
    london = start.astimezone(__import__("zoneinfo").ZoneInfo("Europe/London"))
    assert (london.hour, london.minute) == (0, 0)


# ---- 5. District comparisons withdrawn from search -----------------------

def test_district_comparisons_are_not_offered_to_search():
    assert app_main.VERSUS_OFFERED_TO_SEARCH is False
    assert not app_main._versus_indexable("M20", "M21")


def test_the_sitemap_lists_no_comparison_pairs(client):
    body = client.get("/sitemap-comparisons.xml").text
    assert "/compare/" not in body


# ---- 7. Aspect waits no longer than its budget ---------------------------

def test_a_late_aspect_is_read_from_its_own_cache_at_render(monkeypatch):
    lat, lon = 50.4619, -3.5253
    answer = {"front_facing": "North", "rear_facing": "South", "nearest_road": "Union Street"}
    key = app_main._cache.coord_key("orientation", lat, lon)
    app_main._cache.set(key, answer)
    assert orientation.cached(lat, lon) == answer


def test_the_report_gather_bounds_aspect_and_the_pdf_does_not():
    src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "orientation.orientation_for(lat, lon) if wait_for_slow" in src
    assert "_bounded_strict(orientation.orientation_for(lat, lon), ORIENTATION_BUDGET_S)" in src
    assert app_main.ORIENTATION_BUDGET_S <= 5


def test_a_bounded_aspect_raises_and_keeps_running():
    async def slow():
        await asyncio.sleep(0.2)
        return {"done": True}

    async def run():
        try:
            await app_main._bounded_strict(slow(), 0.01)
        except asyncio.TimeoutError:
            return "timed out"
        return "answered"

    assert asyncio.run(run()) == "timed out"


def test_the_pages_the_audit_found_long_have_titles_that_fit(client):
    """The new audit rule's first run on production found eleven more."""
    for path in ("/estate-charges", "/running-costs/council-tax", "/running-costs", "/schools/admissions",
                 "/schools/how-admissions-work", "/tools/mortgage-calculator", "/tools/stamp-duty-calculator"):
        body = client.get(path).text
        title = html.unescape(" ".join(re.search(r"<title>(.*?)</title>", body, re.S).group(1).split()))
        assert len(title) <= 65, (path, title)
