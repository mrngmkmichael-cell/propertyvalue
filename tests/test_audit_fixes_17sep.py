"""Fixes from the first-time visitor audit of 17 September 2026
(docs/audits/2026-09-17-first-visitor-audit.md), one headed section per
item, each pinning the behaviour the owner approved that day."""
import html
import json
import pathlib
import re

from app import auth, db
from app import main as app_main
from app.services import email as email_service
from app.services import pdf_export
from tests.conftest import fake_location
from tests.test_email_verification import _live, _signup
from tests.test_pdf_report import _report as _full_pdf_report


# ---- A1. A new account is not told its free report is used ---------------
# The report's wall said "You've used your free report" to every signed-in
# account on a locked home, a brand-new one included, a scroll below the
# offer asking whether to use that same report. The score's "+N more
# checks" link sent that account to /premium, and a visitor without an
# account to /signup without the house.

USED_UP = "You've used your free report"


def _banner(body):
    """The wall above the cards, up to where the card groups start."""
    assert '<div class="paywall-banner" data-animate>' in body
    return body.split('<div class="paywall-banner" data-animate>', 1)[1].split('id="report-categories"', 1)[0]


def _upsell(body):
    """(href, words) of the score card's "+N more checks" link."""
    m = re.search(r'<a href="([^"]*)" class="overview-score-upsell">\s*(.*?)\s*</a>', body, re.S)
    assert m, "the score card's upsell link is missing"
    return m.group(1), " ".join(m.group(2).split())


def _without_popup(body):
    """The page minus the pop-up, which is the same offer for a visitor
    with scripts on (see the comment above it in property.html)."""
    return re.sub(r'<dialog[^>]*id="use-free-report-dialog".*?</dialog>', "", body, flags=re.S)


def test_a1_a_new_account_is_offered_its_free_report_once_and_never_told_it_is_used(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)  # confirmation not in play here
    fake_report(location=fake_location(postcode="M14 7AA", outcode="M14"))
    assert _signup(client, "a1-fresh@customer.test").status_code == 303

    body = client.get("/property?postcode=M14+7AA").text
    assert USED_UP not in body
    assert "See plans" not in body

    # One offer: one inline "yes", one form that spends the report.
    page = _without_popup(body)
    assert page.count('id="use-free-report"') == 1
    assert page.count('action="/property/unlock"') == 1
    offer = page.split('id="use-free-report"', 1)[1].split("</div>", 1)[0]
    label = re.search(r'<button type="submit">(.*?)</button>', offer).group(1)
    assert label == "Yes, unlock every card"

    # The wall adds no ask of its own. It points back at the offer and
    # names its button in the button's own words.
    banner = _banner(body)
    assert 'class="paywall-banner-cta"' not in banner and "<button" not in banner and "<form" not in banner
    assert 'href="/premium"' not in banner
    assert 'href="#use-free-report"' in banner
    assert f"&ldquo;{label}&rdquo;" in banner

    # The score's extra checks open with the free report, not Premium.
    href, words = _upsell(body)
    assert href == "#use-free-report"
    assert words == "+1 more check opens with your free full report →"


def test_a1_the_used_up_wall_is_for_an_account_that_spent_its_report(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    # Addresses of their own: two accounts unlocking one address on one
    # day is the pattern the /admin test asserts is absent.
    fake_report(location=fake_location(postcode="M20 9AA", outcode="M20"))
    assert _signup(client, "a1-spent@customer.test").status_code == 303
    client.get("/property?postcode=M20+9AA")
    r = client.post("/property/unlock", data={"postcode": "M20 9AA", "house_number": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")

    fake_report(location=fake_location(postcode="M1 9AA", outcode="M1"))
    body = client.get("/property?postcode=M1+9AA").text
    banner = _banner(body)
    assert f"{USED_UP}." in banner
    assert '<a class="paywall-banner-cta" href="/premium">See plans</a>' in banner
    assert 'id="use-free-report"' not in body

    href, words = _upsell(body)
    assert href == "/premium"
    assert words == "+1 more check factored in with Premium →"


def test_a1_an_unconfirmed_account_is_pointed_at_confirming_not_told_it_is_used(client, fake_report, monkeypatch):
    _live(monkeypatch)
    fake_report(location=fake_location(postcode="M14 8AA", outcode="M14"))
    assert _signup(client, "a1-unconfirmed@customer.test").status_code == 303
    with db.get_session() as session:
        uid = auth.find_user_by_email(session, "a1-unconfirmed@customer.test").id
        assert auth.needs_confirmation(session, uid) is True

    body = client.get("/property?postcode=M14+8AA").text
    assert 'id="verify-banner"' in body
    assert USED_UP not in body and "See plans" not in body
    banner = _banner(body)
    assert 'href="#verify-banner"' in banner
    assert 'class="paywall-banner-cta"' not in banner and "<button" not in banner

    href, words = _upsell(body)
    assert href == "#verify-banner"
    assert "Premium" not in words


def test_a1_signed_out_the_score_link_carries_the_house_to_sign_up(client, fake_report):
    fake_report()
    body = client.get("/property?postcode=M14%205TG").text
    assert USED_UP not in body

    href, _words = _upsell(body)
    assert href == "/signup?next=/property%3Fpostcode%3DM14%205TG"
    # The same house the wall's own sign-up link carries.
    wall = re.search(r'<a class="paywall-banner-cta" href="([^"]*)"', _banner(body)).group(1)
    assert href == wall


# ---- A2. The PDF promised with the free report is served ------------------
# Sign-up lists the full PDF under what you get today and the report shows
# "Download full PDF report" on a home just unlocked, but /property/pdf sent
# every account without a subscription to /premium. The owner's decision:
# the PDF comes with the free first property, and a subscriber gets every
# home's. Premium's table and How we compare now say the same.

def _pdf_route_fakes(monkeypatch):
    """Stand in for the network and the PDF engine behind /property/pdf,
    and record what the route asked of each. The lookup answers for
    whichever postcode is asked. The document itself still renders from
    the PDF test's full fixture, so a template that breaks for this path
    fails here."""
    seen = {"gathers": [], "documents": []}

    async def _lookup(postcode):
        postcode = postcode.strip().upper()
        return fake_location(postcode=postcode, outcode=postcode.split()[0])

    async def _gather(location, house_number, premium_unlocked, wait_for_amenities=False, wait_for_slow=False):
        seen["gathers"].append((location["postcode"], house_number, premium_unlocked, wait_for_slow))
        return {**_full_pdf_report(), "location": location, "epc_configured": True}

    async def _running_costs(where, house_number=""):
        return {}

    def _html_to_pdf(document):
        seen["documents"].append(document)
        return b"%PDF-1.4 stand-in"

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "_full_property_gather", _gather)
    monkeypatch.setattr(app_main, "_running_costs_for_postcode", _running_costs)
    monkeypatch.setattr(pdf_export, "html_to_pdf", _html_to_pdf)
    return seen


def test_a2_a_free_account_gets_the_pdf_of_the_home_it_unlocked(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    location = fake_location(postcode="M13 9PL", outcode="M13")
    fake_report(location=location)
    assert _signup(client, "a2-free@customer.test").status_code == 303
    r = client.post("/property/unlock", data={"postcode": "M13 9PL", "house_number": "4"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")

    # The report's own button, followed as a browser would.
    body = client.get("/property?postcode=M13+9PL&house_number=4").text
    button = re.search(r'<a class="pdf-download-btn" download href="([^"]*)">\s*<svg.*?</svg>\s*Download full PDF report', body, re.S)
    assert button, "the unlocked home has no PDF button"
    assert "PDF report &middot; Premium" not in body

    seen = _pdf_route_fakes(monkeypatch)
    r = client.get(html.unescape(button.group(1)), follow_redirects=False)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'attachment; filename="UKPropertyInsight-M139PL.pdf"'
    assert r.content == b"%PDF-1.4 stand-in"
    # Built for this home, with every check open, and handed to the engine.
    assert seen["gathers"] == [("M13 9PL", "4", True, True)]
    assert len(seen["documents"]) == 1
    assert "Every check, at a glance" in seen["documents"][0]


def test_a2_the_same_free_account_is_sent_to_premium_for_a_home_it_has_not_unlocked(client, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, "a2-other-home@customer.test").status_code == 303
    r = client.post("/property/unlock", data={"postcode": "M15 5AA", "house_number": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")

    seen = _pdf_route_fakes(monkeypatch)
    # The unlocked home's PDF is served...
    assert client.get("/property/pdf?postcode=M15+5AA", follow_redirects=False).status_code == 200
    # ...another home's is not, nor the same postcode at a house it did not unlock.
    for url in ("/property/pdf?postcode=M16+7AA", "/property/pdf?postcode=M15+5AA&house_number=9"):
        r = client.get(url, follow_redirects=False)
        assert r.status_code == 303, url
        assert r.headers["location"].startswith("/premium?postcode="), url
    assert seen["gathers"] == [("M15 5AA", "", True, True)] and len(seen["documents"]) == 1


def test_a2_a_subscriber_gets_every_homes_pdf_without_spending_an_unlock(client, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, "a2-subscriber@customer.test").status_code == 303
    with db.get_session() as session:
        user = auth.find_user_by_email(session, "a2-subscriber@customer.test")
        user.is_premium, user.plan = True, "monthly"
        session.commit()
        uid = user.id

    seen = _pdf_route_fakes(monkeypatch)
    r = client.get("/property/pdf?postcode=M19+2AA&house_number=11", follow_redirects=False)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert seen["gathers"] == [("M19 2AA", "11", True, True)]
    with db.get_session() as session:
        assert auth.unlocks_used(session, uid) == 0


def test_a2_signed_out_the_pdf_link_goes_to_log_in(client, monkeypatch):
    seen = _pdf_route_fakes(monkeypatch)
    r = client.get("/property/pdf?postcode=M13+9PL&house_number=4", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/property?postcode=M13+9PL&house_number=4"
    assert seen["gathers"] == [] and seen["documents"] == []


def test_a2_premium_and_how_we_compare_say_the_first_propertys_pdf_is_free(client, monkeypatch):
    # The plans table sits on the open-for-business page, which needs billing configured.
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_m")
    monkeypatch.setenv("STRIPE_PRICE_ID_QUARTERLY", "price_q")
    premium = client.get("/premium").text
    row = re.search(r"<tr><td>PDF report to keep or send</td>(.*?)</tr>", premium, re.S)
    assert row, "the PDF row is missing from the plans table"
    free_cell, premium_cell = re.findall(r"<td[^>]*>(.*?)</td>", row.group(1))
    assert free_cell == "Your first property"
    assert premium_cell == "Every property"
    assert "Not included" not in row.group(1)

    compare = client.get("/alternatives").text
    assert "A PDF of the full report, on Premium" not in compare
    assert "A PDF of the full report, free for your first property with a free account and for every property on Premium" in compare


# ---- A3. Premium tells the truth about alerts, cancelling and the yes -----
# Premium's FAQ sold change alerts as a Premium addition beside a table
# that has them free, and said everything unlocked stays unlocked after
# cancelling, when only the free full report is stored as an unlock. Its
# FAQ structured data was a second, drifting copy. A free account read
# "You have used all 1 free reports.", and sign-up said a named house
# "opens in full" on sign-up, where the owner kept a yes on the report
# first (decisions 4 and 5).

PREMIUM_TEMPLATE = pathlib.Path("app/templates/premium.html")


def _billing(monkeypatch, pass_on=False):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_m")
    monkeypatch.setenv("STRIPE_PRICE_ID_QUARTERLY", "price_q")
    if pass_on:
        monkeypatch.setenv("STRIPE_PRICE_ID_PASS", "price_p")
    else:
        monkeypatch.delenv("STRIPE_PRICE_ID_PASS", raising=False)


def _fresh_premium(client):
    """/premium, rendered now: a signed-out view is cached across one
    test's changes of environment otherwise."""
    from app.services import _cache
    for key in [k for k in _cache._store if isinstance(k, tuple) and k and k[0] == "anon_html"]:
        _cache._evict(key)
    return client.get("/premium").text


def _faq(body):
    """([(question, answer)] as a reader sees them, [(question, answer)]
    from the FAQPage structured data)."""
    dl = re.search(r'<dl class="faq-list">(.*?)</dl>', body, re.S)
    assert dl, "the FAQ list is missing"
    plain = lambda s: " ".join(html.unescape(re.sub(r"<[^>]+>", "", s)).split())
    visible = [(plain(q), plain(a)) for q, a in
               re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", dl.group(1), re.S)]
    data = next(json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
                if '"FAQPage"' in s)
    structured = [(q["name"], q["acceptedAnswer"]["text"]) for q in data["mainEntity"]]
    return visible, structured


def test_a3_premium_never_says_everything_unlocked_stays_unlocked(client, monkeypatch):
    template = PREMIUM_TEMPLATE.read_text(encoding="utf-8").lower()
    assert "everything you unlocked stays unlocked" not in template
    assert "stays unlocked after that" not in template
    for pass_on in (False, True):
        _billing(monkeypatch, pass_on)
        body = _fresh_premium(client).lower()
        assert "everything you unlocked stays unlocked" not in body
        assert "stays unlocked after that" not in body


def test_a3_the_visible_faq_is_word_for_word_the_structured_data(client, monkeypatch):
    for configured, pass_on in ((True, False), (True, True), (False, False)):
        if configured:
            _billing(monkeypatch, pass_on)
        else:
            monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
            monkeypatch.delenv("STRIPE_PRICE_ID_PASS", raising=False)
        visible, structured = _faq(_fresh_premium(client))
        assert len(visible) == 5, (configured, pass_on)
        assert visible == structured, (configured, pass_on)
        questions = [q for q, _ in visible]
        assert ("What is the difference between the pass and the subscription?" in questions) is pass_on
        assert ("I only have one house to check. Am I stuck with a monthly bill?" in questions) is not pass_on


def test_a3_change_alerts_are_free_and_cancelling_locks_premium_homes_again(client, monkeypatch):
    _billing(monkeypatch)
    answers = dict(_faq(_fresh_premium(client))[0])

    extra = answers["What do I get that the free report does not show?"]
    assert "change alerts on saved properties" not in extra
    # Every sentence that mentions alerts says they are free, not bought.
    for sentence in re.split(r"(?<=\.)\s+", extra):
        if "alert" in sentence.lower():
            assert "free with any account" in sentence and "Premium does not add them" in sentence, sentence
    # The count is the list's, never typed.
    assert f"the {len(app_main.PREMIUM_CHECKS)} checks locked on any other report" in extra
    assert "each property's PDF" in extra

    cancel = answers["Can I cancel?"]
    assert "Cancelling stops the next payment." in cancel
    assert "carries on until the end of the period you have already paid for" in cancel
    assert "Then those homes lock again." in cancel
    assert "The home you opened with your free full report stays open for good." in cancel
    assert "account page" not in cancel

    one_house = answers["I only have one house to check. Am I stuck with a monthly bill?"]
    assert "then lock again" in one_house and "account page" not in one_house


def test_a3_premium_meta_description_names_only_what_premium_adds(client, monkeypatch):
    _billing(monkeypatch)
    body = _fresh_premium(client)
    description = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', body).group(1))
    assert len(description) < 155, len(description)
    for sold_as_paid in ("watchlist", "track how they change", "export full PDF"):
        assert sold_as_paid not in description
    assert description.startswith("Premium opens every check on every property")


def test_a3_a_one_unlock_account_is_told_it_used_its_free_full_report(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    _billing(monkeypatch)
    assert _signup(client, "a3-one-unlock@customer.test").status_code == 303
    body = client.get("/premium").text
    assert "You have 1 free full report left." in body
    assert "Every check below is unlocked on it." in body

    r = client.post("/property/unlock", data={"postcode": "M21 0AA", "house_number": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")
    body = " ".join(client.get("/premium").text.split())
    assert "all 1 free reports" not in body
    assert "<strong>You have used your free full report.</strong>" in body
    assert "The property you opened with it stays unlocked. Subscribe to open new ones." in body


def test_a3_more_than_one_free_report_is_counted_in_words_that_agree(client, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    monkeypatch.setattr(auth, "FREE_PREMIUM_UNLOCKS", 2)
    _billing(monkeypatch)
    assert _signup(client, "a3-two-unlocks@customer.test").status_code == 303
    for postcode in ("M22 0AA", "M23 0AA"):
        r = client.post("/property/unlock", data={"postcode": postcode, "house_number": ""}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")
    body = " ".join(client.get("/premium").text.split())
    assert "<strong>You have used all 2 free full reports.</strong>" in body
    assert "The properties you opened free stay unlocked." in body


def _signup_line(body):
    m = re.search(r'<p class="dek signup-for">(.*?)</p>', body, re.S)
    assert m, "the sign-up page does not name the house"
    return " ".join(m.group(1).split())


def test_a3_sign_up_for_a_named_house_says_the_yes_comes_first(client, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    url = "/signup?next=/property%3Fpostcode%3DKT3%204HX%26house_number%3D36"
    line = _signup_line(client.get(url).text)
    assert "sign up and it opens in full" not in line
    assert line == ("Your free full report will be <strong>36, KT3 4HX</strong>, the one you came from: "
                    "sign up, say yes once on the report, and it opens in full.")

    # With confirmation switched on, a password account confirms first.
    _live(monkeypatch)
    line = _signup_line(client.get(url).text)
    assert "sign up and it opens in full" not in line
    assert line.endswith("say yes once on the report, and it opens in full. With an email and password, confirm your address first.")

    # The alert promise is the one My properties makes, not "anything".
    body = client.get(url).text
    assert "an email when anything changes" not in body
    assert "an email when something changes on a saved property" in body


# ---- A4. Two promises the report does not keep ----------------------------
# Council tax pages and area guides promised the band an address carries,
# which the site does not hold (the band list is not open data; the
# methodology page lists it under what the site does not show). They now
# say what the report does show, every band's bill at the council, and
# where a home's own band is found. And the report's Schools Nearby card
# said "Likely for 4 schools" above a popup opening "we can't say whether
# this address falls within any school's admission area"; the popup now
# says what Likely means, and the counts say how many are estimates.

BAND_PROMISES = (
    "the band an address actually carries",
    "gives the band of a specific address",
    "Its council tax band by band",
    "the EPC card gives the band",
)
GOV_UK_BANDS = 'href="https://www.gov.uk/council-tax-bands"'


def _flat(text):
    return " ".join(text.split())


def test_a4_no_template_promises_an_addresss_own_council_tax_band():
    templates = pathlib.Path(app_main.__file__).parent / "templates"
    promise = re.compile(r"band an address (actually )?carries|gives the band of (a|an|this|that|the) "
                         r"(specific )?(address|home|property)|band (this|that|the) (home|address|property) "
                         r"(actually )?carries", re.I)
    for path in templates.rglob("*.html"):
        # Template comments are for the next developer, and the ones that
        # record this change quote the old promise on purpose.
        text = _flat(re.sub(r"\{#.*?#\}", "", path.read_text(encoding="utf-8"), flags=re.S))
        for words in BAND_PROMISES:
            assert words not in text, f"{path.name} still says {words!r}"
        found = promise.search(text)
        assert found is None, f"{path.name} promises an address's band: {found.group(0)!r}"


def test_a4_a_council_tax_page_says_where_a_homes_band_is_and_what_the_report_shows(client):
    body = client.get("/running-costs/council-tax/basildon").text
    flat = _flat(body)
    for words in BAND_PROMISES:
        assert words not in flat
    assert "forty-three other checks" not in flat
    assert "Every band's bill at Basildon beside the EPC's energy estimate" in flat
    assert "The home's own band is on its council tax bill;" in flat
    assert (f'anyone can look it up free at <a {GOV_UK_BANDS} target="_blank" rel="noopener">'
            "gov.uk/council-tax-bands</a>.") in flat
    assert ("shows every band's bill at the address's council beside the home's other checks, and "
            '<a href="/running-costs">running costs by postcode</a> gives the yearly bill for the band you pick.') in flat


def test_a4_a_scottish_council_tax_page_points_at_the_scottish_assessors(client):
    from app.services import council_tax
    slug = next(s for s, v in council_tax.pages().items() if v.get("nation") == "Scotland")
    flat = _flat(client.get(f"/running-costs/council-tax/{slug}").text)
    for words in BAND_PROMISES:
        assert words not in flat
    # gov.uk's lookup covers England and Wales and sends Scotland to the assessors.
    assert 'look it up free on the <a href="https://www.saa.gov.uk/"' in flat
    assert "gov.uk/council-tax-bands" not in flat


def test_a4_the_area_guide_no_longer_says_the_report_gives_an_addresss_band(client, monkeypatch):
    import time
    from app.services import _cache
    from tests.test_ai_search_readiness import AREA_PAYLOAD, _forget_html

    async def _resolve(outcode):
        return fake_location(postcode=f"{outcode} 2AA", outcode=outcode), True

    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    key = ("area_guide", app_main.AREA_GUIDE_PAYLOAD_VERSION, "AB12")
    _cache._put(key, time.time(), dict(AREA_PAYLOAD))
    _forget_html()
    try:
        body = client.get("/area/AB12").text
    finally:
        _cache._evict(key)
    section = _flat(body.split("Council tax and the council's finances", 1)[1].split("</section>", 1)[0])
    for words in BAND_PROMISES:
        assert words not in section
    assert (f"A home's own band is on its council tax bill, free to look up at <a {GOV_UK_BANDS} "
            'target="_blank" rel="noopener">gov.uk/council-tax-bands</a>; the report shows every '
            "band's bill at the council beside the home's other checks.") in section


def test_a4_the_reports_council_tax_popup_gives_the_free_band_lookup(client, fake_report):
    from app.services import council_tax
    from tests.conftest import fake_gather
    fake_report(gather=fake_gather(council_tax=council_tax.for_district("E08000003", "Manchester")))
    body = client.get("/property?postcode=M14+5TG", headers={"User-Agent": "Googlebot/2.1"}).text
    popup = _flat(body.split('id="modal-council-tax"', 1)[1].split("</dialog>", 1)[0])
    assert "ask which band this property is in, or look it up free at" in popup
    assert GOV_UK_BANDS in popup


def _school(urn, name, distance_m, published=None, estimate=None):
    s = {"urn": urn, "name": name, "type": "Academy", "distance_m": distance_m, "phase_group": "Primary",
         "ofsted_rating": 2, "ofsted_rating_label": "Good", "latitude": 53.45, "longitude": -2.22}
    if published is not None:
        s["admission_radius"] = {"last_distance_miles": published, "academic_year": "2025", "source_authority": "Manchester"}
    if estimate is not None:
        s["catchment_estimate"] = {"radius_miles": estimate}
    return s


def _landscape(schools):
    return {
        "total_schools": len(schools), "good_or_better_pct": 80, "radius_miles": 3, "radius_km": 4.8,
        "by_rating": [], "by_phase": [], "by_sector": {}, "special_count": 0, "special_schools": [],
        "further_education": 0, "higher_education_count": 0, "higher_education_names": [],
        "independent_count": 0, "independent_names": [], "independent_schools": [], "higher_education": [],
        "all_schools": schools,
    }


# Against a 1.0 mile figure: 0.37 mi and 0.50 mi are Likely, 0.99 mi is
# Borderline, 1.86 mi is Unlikely.
MIXED_SCHOOLS = [
    _school(1, "Near Published", 600, published=1.0),
    _school(2, "Near Estimated", 800, estimate=1.0),
    _school(3, "Edge Estimated", 1600, estimate=1.0),
    _school(4, "Far Published", 3000, published=1.0),
]


def _card_status(body, title):
    card = body.split(f'<span class="dashboard-card-title">{title}</span>', 1)[1]
    return _flat(re.search(r'<span class="dashboard-card-status">(.*?)</span>', card, re.S).group(1))


def test_a4_the_verdict_summary_counts_estimated_readings():
    summary = app_main._school_verdict_summary(_landscape(MIXED_SCHOOLS))
    assert summary["counts"] == {"likely": 2, "borderline": 1, "unlikely": 1}
    assert summary["estimated"] == {"likely": 1, "borderline": 1, "unlikely": 0}


def test_a4_the_schools_card_marks_estimated_readings_and_the_popup_says_what_likely_means(client, fake_report):
    from tests.conftest import fake_gather
    fake_report(gather=fake_gather(school_landscape=_landscape(MIXED_SCHOOLS), catchment=[]))
    body = client.get("/property?postcode=M14+5TG", headers={"User-Agent": "Googlebot/2.1"}).text

    assert _card_status(body, "Schools Nearby") == "Likely for 2 schools (1 est.), borderline 1 (1 est.)"
    assert _card_status(body, "School Catchment Areas") == "2 likely (1 est.), 1 borderline (1 est.), 1 unlikely"

    popup = _flat(body.split('id="modal-schools"', 1)[1].split("</dialog>", 1)[0])
    assert "we can't say whether this address falls within any school's admission area" not in popup
    assert "No reliable free UK-wide catchment data exists" not in popup
    assert "Likely means this address is comfortably inside the distance the school last offered places to." in popup
    assert ("That distance is the council's published figure where the council publishes one, and a modelled "
            'estimate where it does not, marked "est." on the card.') in popup
    assert "It is never a guarantee: places go first to children who meet the school's other criteria" in popup
    assert "The card counts the nearest 4 schools with an admission distance, published or estimated." in popup
    # The proximity tables keep their own caveat.
    assert "The tables below are the nearest 3 of each type, by proximity only" in popup


def test_a4_published_readings_carry_no_est_mark(client, fake_report):
    from tests.conftest import fake_gather
    schools = [_school(1, "Near Published", 600, published=1.0), _school(2, "Edge Published", 1600, published=1.0)]
    fake_report(gather=fake_gather(school_landscape=_landscape(schools)))
    body = client.get("/property?postcode=M14+5TG", headers={"User-Agent": "Googlebot/2.1"}).text
    assert _card_status(body, "Schools Nearby") == "Likely for 1 school, borderline 1"


def test_a4_an_all_borderline_or_unlikely_card_marks_its_estimates_too(client, fake_report):
    from tests.conftest import fake_gather
    schools = [_school(1, "Edge Estimated", 1600, estimate=1.0), _school(2, "Far Estimated", 3000, estimate=1.0),
               _school(3, "Far Published", 3200, published=1.0)]
    fake_report(gather=fake_gather(school_landscape=_landscape(schools)))
    body = client.get("/property?postcode=M14+5TG", headers={"User-Agent": "Googlebot/2.1"}).text
    assert _card_status(body, "Schools Nearby") == "Borderline 1 (1 est.), unlikely 2 (1 est.)"


# ---- A5. The all-clear vouches only for what was read ---------------------
# The green banner said "No major red flags found across flood, noise,
# radon, air quality, contamination, planning and area indicators" to
# every reader: air quality and contamination were locked (and their flags
# turned to ok before the banner was built), and it stayed green while the
# noise service and the Coal Authority check had failed. It now names only
# checks open to this reader that ran and came back clear, names a failed
# one as not checked, and counts the locked checks from PREMIUM_CHECKS
# without a word about what they found.

LOCKED_SENTENCE = f"The {len(app_main.PREMIUM_CHECKS)} locked checks are not included."


def _all_read(**overrides):
    """A report on which every check behind the banner ran and came back
    clear. conftest's fake_gather marks most of those services failed, and
    its deprivation decile of 3 is a red flag of its own."""
    from tests.conftest import fake_gather
    read = {
        "deprivation": {"imd_decile": 7, "la_name": "Manchester"},
        "surface_water": {"label": "Very low risk", "probability": "Less than 1 in 1,000 (0.1%)"},
        "radon": {"class": "1", "label": "Low (under 1% of homes above the Action Level)"},
        "designations": {"built_up_area": {"label": "Built-up Area", "group": "planning", "present": True}},
        "planning_flags": [], "environmental_flags": [],
        "clay_risk": {"class_2030": "Improbable", "label_2030": "Improbable", "label_2050": "Possible"},
        "sewage_outfalls": [], "sewage_error": False,
        "coal_mining": {"present": False},
        "historic_landfill": {"status": "clear"},
        "air_quality": {"year": 2024, "pollutants": [
            {"name": "no2", "label": "NO2", "value": 15.6, "who_guideline": 10, "times_guideline": 1.6},
        ]},
    }
    read.update(overrides)
    return fake_gather(**read)


def _a5_banner(body):
    """(class attribute, flattened words) of the banner above the grid."""
    m = re.search(r'<div class="(attention-banner[^"]*)" data-animate>(.*?)</div>\s*</div>', body, re.S)
    assert m, "the report has no attention banner"
    words = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(2))).split())
    return m.group(1), words.replace(" .", ".")


def _cleared(words):
    """The checks the all-clear names, as a list."""
    m = re.search(r"No major red flags found across (.*?)\.", words)
    assert m, words
    return re.split(r", | and ", m.group(1))


def test_a5_a_failed_noise_service_is_not_checked_and_not_among_the_clear(client, fake_report):
    fake_report(gather=_all_read(noise=None, noise_error=True))
    classes, words = _a5_banner(client.get("/property?postcode=M14+5TG").text)

    assert "Noise could not be checked just now." in words
    assert "noise" not in _cleared(words)
    # Everything else open still came back clear, so the banner stays calm.
    assert "attention-banner-clear" in classes
    assert _cleared(words) == ["flood risk", "surface water flooding", "radon", "planning constraints",
                               "environmental designations", "area prices", "council finances",
                               "deprivation", "broadband", "mobile signal"]


def test_a5_a_locked_report_names_no_locked_check_and_counts_them_from_the_constant(client, fake_report):
    fake_report(gather=_all_read())
    classes, words = _a5_banner(client.get("/property?postcode=M14+5TG").text)

    assert "attention-banner-clear" in classes
    for locked_check in ("air quality", "contamination", "mining", "subsidence", "sewage"):
        assert locked_check not in words.lower(), locked_check
    assert words.endswith(LOCKED_SENTENCE)
    assert "could not be checked" not in words

    # Whatever the locked checks found, clear, flagged or failed, the
    # banner does not change by a word.
    fake_report(gather=_all_read(
        air_quality={"year": 2024, "pollutants": [
            {"name": "no2", "label": "NO2", "value": 34.0, "who_guideline": 10, "times_guideline": 3.4},
        ]},
        historic_landfill={"status": "on_site"},
        coal_mining=None, coal_mining_error=True,
        clay_risk=None, clay_risk_error=True,
    ))
    assert _a5_banner(client.get("/property?postcode=M14+5TG").text) == (classes, words)


def test_a5_an_open_report_with_nothing_failed_keeps_its_all_clear(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    fake_report(location=fake_location(postcode="M23 9AA", outcode="M23"), gather=_all_read())
    assert _signup(client, "a5-subscriber@customer.test").status_code == 303
    with db.get_session() as session:
        user = auth.find_user_by_email(session, "a5-subscriber@customer.test")
        user.is_premium, user.plan = True, "monthly"
        session.commit()

    classes, words = _a5_banner(client.get("/property?postcode=M23+9AA").text)
    assert "attention-banner-clear" in classes
    assert words == (
        "✓ No major red flags found across flood risk, surface water flooding, subsidence, mining, "
        "contamination, radon, air quality, noise, sewage discharges, planning constraints, "
        "environmental designations, area prices, council finances, deprivation, broadband and mobile signal."
    )

    # Open to a subscriber, a failed Coal Authority check is named, not cleared.
    fake_report(location=fake_location(postcode="M23 9AA", outcode="M23"),
                gather=_all_read(coal_mining=None, coal_mining_error=True))
    classes, words = _a5_banner(client.get("/property?postcode=M23+9AA").text)
    assert "mining" not in _cleared(words)
    assert words.endswith("Mining could not be checked just now.")


def test_a5_the_locked_sentence_holds_in_every_locked_state_and_goes_when_the_home_opens(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    fake_report(location=fake_location(postcode="M21 7AA", outcode="M21"), gather=_all_read())

    # Signed out.
    assert _a5_banner(client.get("/property?postcode=M21+7AA").text)[1].endswith(LOCKED_SENTENCE)

    # A free account with its free full report unused.
    assert _signup(client, "a5-states@customer.test").status_code == 303
    body = client.get("/property?postcode=M21+7AA").text
    assert 'id="use-free-report"' in body
    assert _a5_banner(body)[1].endswith(LOCKED_SENTENCE)

    # The same account after saying yes: this home is open, nothing is locked.
    r = client.post("/property/unlock", data={"postcode": "M21 7AA", "house_number": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")
    words = _a5_banner(client.get("/property?postcode=M21+7AA").text)[1]
    assert "locked" not in words
    assert "air quality" in _cleared(words) and "contamination" in _cleared(words)

    # Spent: the same account on another home.
    fake_report(location=fake_location(postcode="M22 8AA", outcode="M22"), gather=_all_read())
    body = client.get("/property?postcode=M22+8AA").text
    assert USED_UP in body
    words = _a5_banner(body)[1]
    assert words.endswith(LOCKED_SENTENCE)
    assert "air quality" not in words


def test_a5_nothing_read_gives_no_all_clear_and_red_flags_are_unchanged(client, fake_report, monkeypatch):
    # Every open check behind the banner failed or has nothing to read.
    gather = _all_read(
        flood_zone=None, flood_zone_error=True, flood_error=True, surface_water=None, surface_water_error=True,
        noise=None, noise_error=True, radon=None, radon_error=True, designations=None, designations_error=True,
        deprivation=None, deprivation_error=True, broadband=None, broadband_error=True, mobile=None, mobile_error=True,
        hpi=None,
    )
    fake_report(location=fake_location(postcode="M24 1AA", outcode="M24"), gather=gather)
    monkeypatch.setitem(app_main.templates.env.globals, "council_finance", lambda *a, **k: None)
    classes, words = _a5_banner(client.get("/property?postcode=M24+1AA").text)
    assert "attention-banner-clear" not in classes and "attention-banner-unread" in classes
    assert "No major red flags" not in words and "&#10003;" not in words and "✓" not in words
    # A sentence of its own: without the full stop it ran on into the
    # list of checks that could not be read.
    assert words.startswith("No all-clear to give. Flood risk, surface water flooding, radon, noise, planning constraints,")
    assert "could not be checked just now." in words
    assert words.endswith(LOCKED_SENTENCE)

    # The red-flag path is as it was: conftest's own decile of 3 is a flag.
    from tests.conftest import fake_gather
    fake_report(gather=fake_gather())
    classes, words = _a5_banner(client.get("/property?postcode=M14+5TG").text)
    assert classes == "attention-banner"
    assert "1 thing worth checking on this property" in words and "Among more deprived areas nationally" in words
    assert "could not be checked" not in words and "locked" not in words


# ---- A6. One offer sentence, no orbit, a comparison that fits a phone -----
# The homepage worded the offer four ways: "Free, and no account needed."
# in the hero, a band with "normally £9.99/month" struck through, the
# orbit's tier line and a closing block, and its FAQ promised "headline
# verdicts on every check" where a locked card has none. Area guides and
# hubs said "Free, no account." under "Run the 44 checks". Owner's
# decisions 3 and 7: one sentence from the constants wherever the offer is
# stated, the orbit removed and the scroll-built report kept. The
# comparison's DIY column now quotes the timed by-hand record, and the
# table stacks on a phone instead of hiding this site's column.

ROOT = pathlib.Path(app_main.__file__).resolve().parents[1]
STYLE_CSS = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
INDEX_TEMPLATE = ROOT / "app" / "templates" / "index.html"


def _fresh_home(client):
    from tests.test_ai_search_readiness import _forget_html
    _forget_html()
    return client.get("/").text


def _without_template_comments(text):
    return re.sub(r"\{#.*?#\}", "", text, flags=re.S)


def test_a6_the_offer_sentence_is_counted_from_the_constants_and_every_template_can_use_it():
    expected = (f"{len(app_main.FREE_CHECKS)} checks free with no account. Sign up free, no card, "
                f"and your first home gets all {app_main.CHECK_COUNT}.")
    assert app_main.OFFER_SENTENCE == expected
    assert app_main.templates.env.globals["offer_sentence"] == expected
    # Never typed: no template carries the sentence's words as a literal,
    # so a change to the free list moves every copy at once.
    for path in (ROOT / "app" / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert "checks free with no account" not in text, path.name
        assert "your first home gets all" not in text, path.name


def test_a6_the_homepage_states_the_offer_in_one_sentence_and_the_old_wordings_are_gone(client):
    body = _fresh_home(client)
    sentence = html.escape(app_main.OFFER_SENTENCE, quote=False)

    dek = body.split('<p class="lx-hero-dek">', 1)[1].split("</p>", 1)[0]
    assert dek.split()[:5] == ["Forty-four", "checks", "on", "any", "UK"]
    assert _flat(dek).endswith("What it costs to live there. " + sentence)

    banner = body.split('<section class="promo-banner">', 1)[1].split("</section>", 1)[0]
    assert f'<p class="promo-banner-text">{sentence}</p>' in banner
    assert 'href="/signup"' in banner

    closing = body.split('id="contact"', 1)[1].split("</section>", 1)[0]
    assert f'<p class="lx-lede">{sentence}</p>' in closing

    for gone in ("normally", "£9.99", "promo-banner-price", "headline verdicts on every check",
                 "Free, and no account needed", "come with every Premium check", "free on your first property",
                 "12+ separate sites", ">Hours<", "lx-orbit"):
        assert gone not in body, gone


def test_a6_is_it_free_reads_the_same_in_the_faq_and_its_structured_data(client):
    body = _fresh_home(client)
    visible = {
        " ".join(html.unescape(q).split()): " ".join(html.unescape(a).split())
        for q, a in re.findall(r'<details class="faq-item"[^>]*>\s*<summary>(.*?)</summary>\s*<p>(.*?)</p>', body, re.S)
    }
    data = next(json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
                if '"FAQPage"' in s)
    structured = {q["name"]: q["acceptedAnswer"]["text"] for q in data["mainEntity"]}
    answer = visible["Is it free to use?"]
    assert answer == structured["Is it free to use?"]
    assert answer.startswith(app_main.OFFER_SENTENCE)
    assert "headline verdicts" not in answer


def test_a6_the_orbit_is_gone_and_the_plan_split_and_pricing_link_stay_once(client):
    body = _fresh_home(client)
    free = f"{len(app_main.FREE_CHECKS)} free on every report"
    more = f"{len(app_main.PREMIUM_CHECKS)} more with Premium"
    link = f'<a class="lx-plan-more-btn" href="/premium#all-checks">See pricing for the full list of {app_main.CHECK_COUNT} &rsaquo;</a>'
    assert body.count(free) == 1 and body.count(more) == 1
    assert body.count('href="/premium#all-checks"') == 1 and link in body
    # After the scroll-built report, which stays with its orb.
    assert body.index('id="build"') < body.index(free)
    assert "Watch a report <em class=\"hl-accent\">build itself</em>" in body
    assert body.count("<canvas") == 1 and 'id="lx-build-orb"' in body

    template = INDEX_TEMPLATE.read_text(encoding="utf-8")
    assert "lx-orbit" not in template and "The orbit ring around" not in _without_template_comments(template)
    assert "lx-orbit" not in STYLE_CSS and "promo-banner-price" not in STYLE_CSS
    # The tier labels are the Premium page's too, so their rules stay.
    assert ".lx-tier-label {" in STYLE_CSS and "lx-tier-label" in (ROOT / "app" / "templates" / "premium.html").read_text(encoding="utf-8")
    bump = (ROOT / "scripts" / "bump_check_count.py").read_text(encoding="utf-8")
    assert "lx-orbit-heading" not in bump and 'f"full list of {old}"' not in bump


def test_a6_the_comparison_quotes_the_timed_record_and_links_to_it(client):
    body = _fresh_home(client)
    table = body.split('id="compare-alternatives"', 1)[1].split("</table>", 1)[0]
    rows = re.findall(r"<tr>(.*?)</tr>", table.split("<tbody>", 1)[1], re.S)
    one_page = next(r for r in rows if "Everything on one page" in r)
    assert '<td class="alt-no" data-label="DIY on gov.uk">28 websites</td>' in one_page
    time_row = next(r for r in rows if "Time per property" in r)
    assert ('<td class="alt-no" data-label="DIY on gov.uk"><a href="/methodology#by-hand">'
            "36 minutes, by someone who already knew every site</a></td>") in time_row
    assert "12+" not in table and "Hours" not in table


def test_a6_the_comparison_stacks_on_a_phone_and_the_other_alt_tables_are_left_alone(client):
    body = _fresh_home(client)
    table = body.split('id="compare-alternatives"', 1)[1].split("</table>", 1)[0]
    assert '<table class="alt-table alt-table-stack">' in table
    assert '<th class="alt-portal">Listings portals</th>' in table
    rows = re.findall(r"<tr>(.*?)</tr>", table.split("<tbody>", 1)[1], re.S)
    assert len(rows) == 5
    for row in rows:
        cells = re.findall(r"<td([^>]*)>", row)
        assert len(cells) == 4, row
        assert cells[0] == "" and 'class="alt-portal' in cells[1], row
        assert 'data-label="DIY on gov.uk"' in cells[2], row
        assert 'class="alt-us' in cells[3] and 'data-label="UKPropertyInsight"' in cells[3], row

    # The phone rule: one max-width 600px block, every selector in it on
    # the homepage's own class, the portals column hidden, no minimum
    # width left to scroll. /premium and /alternatives never carry it.
    block = STYLE_CSS.split("@media (max-width: 600px) {\n    .alt-table-stack { min-width: 0; }", 1)
    assert len(block) == 2, "the phone rule for the homepage comparison is missing"
    rules = block[1].split("\n}\n", 1)[0]
    selectors = [s.strip() for chunk in re.findall(r"([^{}]+)\{", rules) for s in chunk.split(",")]
    assert selectors and all(s.startswith(".alt-table-stack") for s in selectors), selectors
    assert ".alt-table-stack .alt-portal { display: none; }" in rules
    assert "content: attr(data-label);" in rules
    for page in ("premium.html", "alternatives.html"):
        assert "alt-table-stack" not in (ROOT / "app" / "templates" / page).read_text(encoding="utf-8"), page
    # No other breakpoint reshapes the shared alt-table.
    for media in re.findall(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", STYLE_CSS):
        if "alt-table" in media:
            assert "alt-table-stack" in media and ".alt-table " not in media.replace(".alt-table-stack", ""), media


def test_a6_every_run_the_checks_box_states_the_offer(client, monkeypatch):
    import time
    from app.services import _cache
    from tests.test_ai_search_readiness import AREA_PAYLOAD, _forget_html
    from tests.test_pages import _seed_admission_school

    sentence = html.escape(app_main.OFFER_SENTENCE, quote=False)

    async def _resolve(outcode):
        return fake_location(postcode=f"{outcode} 2AA", outcode=outcode), True

    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    key = ("area_guide", app_main.AREA_GUIDE_PAYLOAD_VERSION, "AB12")
    _cache._put(key, time.time(), dict(AREA_PAYLOAD))
    _seed_admission_school()
    _forget_html()
    try:
        pages = {
            "/area/AB12": "for that address. ",
            "/areas": "for that address. ",
            "/schools/admissions/manchester": "flood, crime and running costs. ",
            "/running-costs/council-tax/basildon": "the schools nearby. ",
        }
        for path, lead in pages.items():
            r = client.get(path)
            assert r.status_code == 200, path
            form = r.text.split('<form action="/property" method="get"', 1)[1].split("</form>", 1)[0]
            note = _flat(form.split('<p class="section-sub" style="margin: 0.5rem 0 0;">', 1)[1].split("</p>", 1)[0])
            assert lead + sentence in note, (path, note)
            assert "Free, no account." not in r.text, path
    finally:
        _cache._evict(key)
    for name in ("area_guide.html", "areas.html", "schools_admissions_council.html", "council_tax_council.html"):
        text = _without_template_comments((ROOT / "app" / "templates" / name).read_text(encoding="utf-8"))
        assert "Free, no account." not in text and "{{ offer_sentence }}" in text, name


# ---- Batch A fix pass: what the review of A1 to A6 found left over --------
# The neutral banner ran into its next sentence (now pinned in the A5 test
# above). The locked PDF button said Premium to visitors who get the PDF
# free with their free full report. The spent wall and the homepage FAQ
# still promised that every opened home stays open, which a lapsed
# subscriber finds untrue (decision 4), and the homepage FAQ's structured
# data was a drifting second copy. The PDF gate read the postcode as
# typed, before the lookup. /alternatives, sign-up and the council tax
# pages typed counts the free list will move (decision 6). A failed House
# Price Index lookup left "area prices" out of the all-clear in silence.

def _locked_pdf_button(body):
    """(href, label) of the report head's locked PDF button."""
    m = re.search(r'<a class="pdf-download-btn pdf-download-btn-locked" href="([^"]*)">\s*<svg.*?</svg>\s*(.*?)\s*</a>',
                  body, re.S)
    assert m, "the locked PDF button is missing"
    return m.group(1), m.group(2)


FREE_PDF_LABEL = "Free PDF for your first home"


def test_fix_the_locked_pdf_button_says_premium_only_once_the_free_report_is_spent(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    fake_report(location=fake_location(postcode="M25 1AA", outcode="M25"))

    # Signed out: the PDF comes with the free full report a sign-up gets.
    assert _locked_pdf_button(client.get("/property?postcode=M25+1AA").text) == (
        "/signup?next=/property%3Fpostcode%3DM25%201AA", FREE_PDF_LABEL)

    # A new account, its free full report unused.
    assert _signup(client, "fix-pdf-label@customer.test").status_code == 303
    assert _locked_pdf_button(client.get("/property?postcode=M25+1AA").text) == ("#use-free-report", FREE_PDF_LABEL)

    # Spent on another home: this one's PDF is Premium's.
    r = client.post("/property/unlock", data={"postcode": "M26 1AA", "house_number": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")
    body = client.get("/property?postcode=M25+1AA").text
    assert USED_UP in body
    assert _locked_pdf_button(body) == ("/premium", "PDF report &middot; Premium")
    assert FREE_PDF_LABEL not in body


def test_fix_an_unconfirmed_account_is_not_told_the_pdf_is_premium(client, fake_report, monkeypatch):
    _live(monkeypatch)
    fake_report(location=fake_location(postcode="M27 1AA", outcode="M27"))
    assert _signup(client, "fix-pdf-unconfirmed@customer.test").status_code == 303
    body = client.get("/property?postcode=M27+1AA").text
    assert 'id="verify-banner"' in body
    assert _locked_pdf_button(body) == ("#verify-banner", FREE_PDF_LABEL)
    assert "PDF report &middot; Premium" not in body


def test_fix_a_lapsed_subscribers_wall_promises_only_the_free_reports_home(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, "fix-lapsed@customer.test").status_code == 303
    r = client.post("/property/unlock", data={"postcode": "M28 1AA", "house_number": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")

    # Opened on the subscription, then the subscription ended.
    fake_report(location=fake_location(postcode="M29 1AA", outcode="M29"))
    with db.get_session() as session:
        user = auth.find_user_by_email(session, "fix-lapsed@customer.test")
        user.is_premium, user.plan = True, "monthly"
        session.commit()
    assert '<div class="paywall-banner" data-animate>' not in client.get("/property?postcode=M29+1AA").text
    with db.get_session() as session:
        user = auth.find_user_by_email(session, "fix-lapsed@customer.test")
        user.is_premium = False
        session.commit()

    # In a browser the wall has the account's history, and its aside
    # names the one home that does stay open.
    # (A browser with a cold gather gets the wait page, and fake_report
    # never fills the gather cache: marked warm, as the paywall history
    # test in test_email_verification does.)
    from app.services import _cache
    _cache.set(("property_search_gather", "M29 1AA", ""), {"warm": True})
    browser = {"user-agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Safari/537.36"}
    banner = _flat(_banner(client.get("/property?postcode=M29+1AA", headers=browser).text))
    assert f"{USED_UP}." in banner
    assert "The ones you opened" not in banner and "stay unlocked for good" not in banner
    assert banner.count("for good") == 1
    assert '<a href="/property?postcode=M28+1AA">M28 1AA</a>, and it stays open for good.' in banner
    assert "Opening another is &pound;9.99 a month" in banner

    # Without the history (a script, or the owner's own browser), the wall
    # says it itself, still of that one home only.
    banner = _flat(_banner(client.get("/property?postcode=M29+1AA").text))
    assert "The ones you opened" not in banner and "stay unlocked for good" not in banner
    assert banner.count("for good") == 1
    assert "You've used your free report.</strong> The home you opened with it stays open for good. Opening another is" in banner


def _home_faq(body):
    """([(question, answer)] as a reader sees them, [(question, answer)]
    from the homepage's FAQPage structured data)."""
    visible = [
        (" ".join(html.unescape(q).split()), " ".join(html.unescape(a).split()))
        for q, a in re.findall(r'<details class="faq-item"[^>]*>\s*<summary>(.*?)</summary>\s*<p>(.*?)</p>', body, re.S)
    ]
    data = next(json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
                if '"FAQPage"' in s)
    return visible, [(q["name"], q["acceptedAnswer"]["text"]) for q in data["mainEntity"]]


def test_fix_the_homepage_faq_is_one_list_and_keeps_open_only_the_free_reports_home(client):
    body = _fresh_home(client)
    visible, structured = _home_faq(body)
    assert len(visible) == 7
    assert visible == structured

    after = dict(visible)["What happens after the free report?"]
    assert after == ("Nothing is charged, because there is no card on file. The home you opened with your free full "
                     "report stays open for good. Opening another needs a subscription, and postcode search stays "
                     "free either way.")
    assert "stay unlocked for good" not in body and "properties you already opened" not in body

    # One list in the route, not two copies in the template.
    template = _without_template_comments(INDEX_TEMPLATE.read_text(encoding="utf-8"))
    assert '"@type": "Question"' not in template
    assert "{% for q, a in home_faqs %}" in template


def test_fix_the_pdf_gate_reads_the_looked_up_postcode_not_the_typed_one(client, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, "fix-pdf-unspaced@customer.test").status_code == 303
    r = client.post("/property/unlock", data={"postcode": "M31 4AA", "house_number": "7"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")

    seen = _pdf_route_fakes(monkeypatch)

    async def _lookup(postcode):
        # postcodes.io answers a postcode however it is typed, with the
        # canonical form in the result.
        compact = postcode.replace(" ", "").upper()
        return fake_location(postcode=f"{compact[:-3]} {compact[-3:]}", outcode=compact[:-3])

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)

    r = client.get("/property/pdf?postcode=m314aa&house_number=7", follow_redirects=False)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert seen["gathers"] == [("M31 4AA", "7", True, True)]

    # A home it has not unlocked still goes to Premium, however it is typed.
    r = client.get("/property/pdf?postcode=m324aa&house_number=7", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/premium?postcode=")
    assert len(seen["gathers"]) == 1


def test_fix_counts_on_alternatives_sign_up_and_council_tax_come_from_the_constants(client, monkeypatch):
    from tests.test_ai_search_readiness import _forget_html

    # Move both, as batch B will move the free list: every figure follows.
    monkeypatch.setattr(app_main, "FREE_CHECKS", app_main.FREE_CHECKS[:-3])
    monkeypatch.setattr(app_main, "CHECK_COUNT", app_main.CHECK_COUNT + 1)
    free, total = len(app_main.FREE_CHECKS), app_main.CHECK_COUNT
    _forget_html()

    compare = _flat(html.unescape(client.get("/alternatives").text))
    assert f"One full Premium report free on sign-up; {free} of {total} checks free on every address" in compare
    assert f"One report per address: {total} checks from official sources" in compare
    assert f"The free report shows {free} checks on any UK address with no account." in compare
    assert f"UKPropertyInsight shows {free} of its {total} checks free on any address" in compare

    assert f"all {total} checks unlocked, on any property you choose" in _flat(client.get("/signup").text)
    assert f'<button type="submit">Run the {total} checks</button>' in client.get("/running-costs/council-tax/basildon").text

    templates = ROOT / "app" / "templates"
    for name, typed in (("alternatives.html", "26 of 44"), ("alternatives.html", "shows 26 checks"),
                        ("alternatives.html", "44 checks from"), ("signup.html", "all 44 checks"),
                        ("council_tax_council.html", "Run the 44 checks")):
        assert typed not in (templates / name).read_text(encoding="utf-8"), (name, typed)
    main_source = pathlib.Path(app_main.__file__).read_text(encoding="utf-8")
    assert "shows 26 of its 44 checks" not in main_source
    # The bump script no longer looks for the sign-up count it cannot find.
    bump = (ROOT / "scripts" / "bump_check_count.py").read_text(encoding="utf-8")
    assert 'f"all {old} checks unlocked"' not in bump


def test_fix_a_failed_house_price_index_is_named_in_the_all_clear(client, fake_report):
    fake_report(location=fake_location(postcode="M33 1AA", outcode="M33"), gather=_all_read(hpi=None, hpi_error=True))
    classes, words = _a5_banner(client.get("/property?postcode=M33+1AA").text)
    assert "attention-banner-clear" in classes
    assert "area prices" not in _cleared(words)
    assert "Area prices could not be checked just now." in words


def test_fix_the_gather_flags_a_failed_house_price_index(monkeypatch):
    """The real gather's handling, with every source failed: the gather
    step itself is stood in for by making each member raise, so nothing
    reaches the network."""
    import asyncio

    async def _failed(name, coro):
        close = getattr(coro, "close", None)
        if close:
            close()  # never awaited, so never sent
        return RuntimeError(f"{name} down")

    async def _uncached(cache_key, ttl_s, factory):
        return await factory()

    async def _unbounded(coro, seconds):
        return await coro

    monkeypatch.setattr(app_main, "_timed", _failed)
    # So the flood warnings call reaches _failed itself and is closed there.
    monkeypatch.setattr(app_main, "_bounded", lambda coro, seconds: coro)
    monkeypatch.setattr(app_main, "_deduped", _uncached)
    location = fake_location(postcode="M34 9ZZ", outcode="M34")
    try:
        context = asyncio.run(app_main._full_property_gather(location, "", premium_unlocked=False))
    finally:
        app_main._gather_progress.pop(("M34 9ZZ", ""), None)
    assert context["hpi_error"] is True
    assert "hpi" not in context
    # Named beside the other failed sources the banner reads.
    assert context.get("noise_error") and context.get("radon_error") and context.get("deprivation_error")
