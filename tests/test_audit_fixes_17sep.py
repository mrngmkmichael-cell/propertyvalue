"""Fixes from the first-time visitor audit of 17 September 2026
(docs/audits/2026-09-17-first-visitor-audit.md), one headed section per
item, each pinning the behaviour the owner approved that day."""
import asyncio
import datetime
import html
import json
import pathlib
import re

from app import auth, db
from app import main as app_main
from app.services import email as email_service
from app.services import pdf_export
from app.services import stripe_billing
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
    """(href, words) of the score card's locked-checks link."""
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
    # The words are C4's, later the same day; the route is this item's.
    href, words = _upsell(body)
    assert href == "#use-free-report"
    assert words == app_main.locked_found_sentence(1) + ". Open them with your free full report →"


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
    # Both links carry this house to Premium since C3, later the same
    # day; see that section for what the price page then does with it.
    assert '<a class="paywall-banner-cta" href="/premium?home=M1+9AA">See plans</a>' in banner
    assert 'id="use-free-report"' not in body

    href, words = _upsell(body)
    assert href == "/premium?home=M1+9AA"
    assert words == app_main.locked_found_sentence(1) + ". Open them with Premium →"


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
        # To Premium carrying the home, house number too (batch C fix pass).
        assert r.headers["location"].startswith("/premium?home="), url
    assert client.get("/property/pdf?postcode=M15+5AA&house_number=9",
                      follow_redirects=False).headers["location"] == "/premium?home=M15+5AA&hn=9"
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


def test_a4_the_schools_card_marks_estimated_readings_and_the_popup_says_what_likely_means(client, fake_report, monkeypatch):
    from tests.conftest import fake_gather
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    gather = fake_gather(school_landscape=_landscape(MIXED_SCHOOLS), catchment=[])
    fake_report(gather=gather)
    body = client.get("/property?postcode=M14+5TG", headers={"User-Agent": "Googlebot/2.1"}).text

    assert _card_status(body, "Schools Nearby") == "Likely for 2 schools (1 est.), borderline 1 (1 est.)"
    # School Catchment Areas is locked, and from 17 Sep 2026 a locked
    # card carries what the check answers instead of its own reading
    # (item B2 below), so the counts are read on the card an account
    # that has unlocked this home sees.
    assert _b2_line(body, "School Catchment Areas") == _b2_escaped("School Catchment Areas")
    fake_report(gather=gather)
    assert _signup(client, "a4-catchment@customer.test").status_code == 303
    # Its own house number, so no address in this file ends up unlocked
    # by two accounts: tests/test_email_verification.py already unlocks
    # M14 5TG with no house number, and /admin counts that pattern.
    assert client.post("/property/unlock", data={"postcode": "M14 5TG", "house_number": "4"},
                       follow_redirects=False).status_code == 303
    unlocked = client.get("/property?postcode=M14+5TG&house_number=4",
                          headers={"User-Agent": "Googlebot/2.1"}).text
    assert _card_status(unlocked, "School Catchment Areas") == "2 likely (1 est.), 1 borderline (1 est.), 1 unlikely"
    client.cookies.clear()

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
# noise service and the coal mining check had failed. It now names only
# checks open to this reader that ran and came back clear, names a failed
# one as not checked, and counts the locked checks from PREMIUM_CHECKS
# without a word about what they found.

LOCKED_SENTENCE = f"The {len(app_main.PREMIUM_CHECKS)} locked checks are not included."


def _all_read(**overrides):
    """A report on which every check behind the banner ran and came back
    clear. conftest's fake_gather marks most of those services failed, and
    its deprivation decile of 3 is a red flag of its own. The locked
    checks are clear here too, so the score's count of locked checks that
    found something is nil: C4 below is where that count is not nil, and
    what the banner then says (conftest's fake sets it to 1, and the
    banner reads it rather than the locked results themselves)."""
    from tests.conftest import fake_gather
    read = {
        "overview": {**fake_gather()["overview"], "premium_extra_checks": 0},
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

    # Whatever the locked checks found, clear, flagged or failed, no
    # finding of theirs changes a word of the banner. Since C4, later the
    # same day, one number does reach it: how many of them found
    # something, from the score, pinned in that section below. Held at
    # nil here so this test stays about the findings themselves.
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

    # Open to a subscriber, a failed coal mining check is named, not cleared.
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
    # Carrying the home to Premium, as the wall does (batch C fix pass).
    assert _locked_pdf_button(body) == ("/premium?home=M25+1AA", "PDF report &middot; Premium")
    assert FREE_PDF_LABEL not in body
    # And so does every locked card on it.
    redirects = set(re.findall(r'data-lock-redirect="([^"]*)"', body))
    assert redirects == {"/premium?home=M25+1AA"}, redirects


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
    # The price the wall quotes is the plans' own, and from 17 Sep 2026 it
    # is both of them (C2), so this reads them rather than typing one.
    assert f"or {stripe_billing.plan_prices()['monthly']} a month" in banner

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
    # The looked-up postcode and the house go along (batch C fix pass).
    assert r.status_code == 303 and r.headers["location"] == "/premium?home=M32+4AA&hn=7"
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


# ---- B1. Rental, income and affordability are free checks ----------------
# The first-visitor walk met Rental Analysis, Household Income and Costs &
# Affordability behind the wall, three checks that read nothing anyone
# pays for: ONS private rents, ONS small-area income and calculators that
# run in the browser off published tax bands. The owner moved all three to
# the free list on 17 Sep 2026, so 26 free and 18 locked became 29 and 15.
# CHECK_COUNT does not move, and every count a visitor reads is still the
# length of one of the two lists.

B1_RENTAL = {
    "la_name": "Manchester", "period": "2026-06", "price_all": 1050,
    "change_all_pct": 5.2,
    "by_bedroom": [
        {"label": "One bedroom", "price": 795, "change_pct": 6.1},
        {"label": "Two bedrooms", "price": 1025, "change_pct": 4.8},
    ],
}
B1_INCOME = {
    "here": 38500, "la_name": "Manchester", "la_average": 36200,
    "region_name": "North West", "region_average": 35100,
}


def _b1_card(body, title):
    """(opening tag, inner HTML) of the report card with this title."""
    for m in re.finditer(r'<button[^>]*class="dashboard-card[^"]*"[^>]*>', body):
        block = body[m.end():body.index("</button>", m.end())]
        if f'<span class="dashboard-card-title">{title}</span>' in block:
            return m.group(0), block
    raise AssertionError(f"the report has no card titled {title}")


def _b1_locked_cards(body):
    return [m.group(0) for m in re.finditer(r'<button[^>]*class="dashboard-card[^"]*"[^>]*>', body)
            if "dashboard-card-locked" in m.group(0)]


def test_b1_a_signed_out_report_opens_rental_income_and_affordability(client, fake_report):
    from tests.conftest import fake_gather
    fake_report(gather=fake_gather(rental=B1_RENTAL, household_income=B1_INCOME))
    body = client.get("/property?postcode=M14+5TG").text

    for title in ("Costs &amp; Affordability", "Rental Analysis", "Household Income"):
        tag, block = _b1_card(body, title)
        assert "dashboard-card-locked" not in tag, title
        assert "data-lock-redirect" not in tag, title
        assert "dashboard-card-lock-overlay" not in block, title

    # Each reads its own figure to a visitor with no account.
    assert _card_status(body, "Costs &amp; Affordability") == "Stamp duty, mortgage, yield"
    assert _card_status(body, "Rental Analysis") == "£1,050/month typical"
    assert _card_status(body, "Household Income") == "£38,500 p/a"
    assert '<span class="dashboard-card-substat">Manchester, by bedroom count</span>' in body

    # And the pop-up behind each one opens on the same page.
    rent = _flat(body.split('id="modal-rental"', 1)[1].split("</dialog>", 1)[0])
    assert "£1,050" in rent and "Two bedrooms" in rent and "ONS's Price Index of Private Rents" in rent
    income = _flat(body.split('id="modal-household-income"', 1)[1].split("</dialog>", 1)[0])
    assert "£38,500" in income and "£36,200" in income
    costs = _flat(body.split('id="modal-calculators"', 1)[1].split("</dialog>", 1)[0])
    assert "Stamp duty / transaction tax" in costs


def test_b1_the_free_and_locked_lists_are_twenty_nine_and_fifteen(client, fake_report):
    assert len(app_main.FREE_CHECKS) == 29
    assert len(app_main.PREMIUM_CHECKS) == 15
    assert len(app_main.FREE_CHECKS) + len(app_main.PREMIUM_CHECKS) == app_main.CHECK_COUNT

    free = {c[1] for c in app_main.FREE_CHECKS}
    locked = {c[1] for c in app_main.PREMIUM_CHECKS}
    for title in ("Costs & Affordability", "Rental Analysis", "Household Income"):
        assert title in free and title not in locked, title
    # Moved whole: icon, words and source came with them.
    assert ("rental", "Rental Analysis", "Typical rent by bedrooms", "ONS private rents") in app_main.FREE_CHECKS
    assert ("income", "Household Income", "Modelled for the small area", "ONS") in app_main.FREE_CHECKS
    assert ("valuation", "Costs & Affordability", "Stamp duty, mortgage and yield",
            "HMRC rates, Bank of England") in app_main.FREE_CHECKS

    # What a signed-out report locks is the locked list, counted.
    fake_report()
    body = client.get("/property?postcode=M14+5TG").text
    assert len(_b1_locked_cards(body)) == len(app_main.PREMIUM_CHECKS)

    home = client.get("/").text
    assert f"{len(app_main.FREE_CHECKS)} free on every report" in home
    assert f"{len(app_main.PREMIUM_CHECKS)} more with Premium" in home


def test_b1_the_walls_by_hand_line_counts_the_checks_from_the_constant(client, fake_report, monkeypatch):
    fake_report()
    monkeypatch.setattr(app_main, "CHECK_COUNT", app_main.CHECK_COUNT + 3)
    body = client.get("/property?postcode=M14+5TG").text
    assert f"By hand, these {app_main.CHECK_COUNT} checks are 28 websites for one house" in body

    template = (ROOT / "app" / "templates" / "property.html").read_text(encoding="utf-8")
    assert "these 44 checks" not in template
    # The rest of that line is what the 16 September run actually counted,
    # so it stays written out.
    assert "28 websites for one house, and nothing to compare at the end" in template
    assert "We timed it on 16 September 2026" in template


# ---- B2. Every locked card says what its check answers --------------------
# A locked card rendered its own finding and the stylesheet hid the status
# line, so the fifteen locked cards were a title, an icon and a lock: the
# audit's first-time visitor could not tell what any of them was for, and
# the only one that said anything, Mining Risk, was saying "Data
# unavailable" from behind the lock while the coal mining service was down.
# Owner's decision 8: one neutral line on each, what the check answers and
# who publishes it, never what it found. The wording is in main.py
# (LOCKED_CARD_LINES) and the cards read it through _locked.html.

# A gather in which no locked check failed. conftest's fake marks every
# service it is not given as failed, which is a realistic state and the
# one the failure test below leans on, but it is not the state that shows
# what each card says when its check ran. What each one found does not
# matter here: a locked card never reads it.
B2_ANSWERED = dict(
    price_trend=None, clay_risk=None, sewage=None, coal_mining=None, valuation=None,
    orientation=None, air_quality=None, historic_landfill=None, catchment=None,
    amenities=None, wellbeing=None,
)


def _b2_escaped(title):
    """The line as the page must write it: main.py holds the words and the
    template escapes the ampersand in a source like "A&E"."""
    from markupsafe import escape
    return str(escape(app_main.LOCKED_CARD_LINES[title]))


def _b2_escaped_unavailable(title):
    from markupsafe import escape
    return str(escape(app_main.LOCKED_CARD_UNAVAILABLE[title]))


def _b2_line(body, title):
    """The line the locked card with this title shows in place of its
    status, asserting it is locked and that no finding is left in it."""
    tag, block = _b1_card(body, title)
    assert "dashboard-card-locked" in tag, f"{title} is not locked on this report"
    assert '<span class="dashboard-card-status">' not in block, (
        f"the locked {title} card still renders its own finding")
    m = re.search(r'<span class="dashboard-card-status dashboard-card-locked-line">(.*?)</span>', block, re.S)
    assert m, f"the locked {title} card shows no line"
    return _flat(m.group(1))


def test_b2_every_locked_check_has_a_line_and_no_free_check_does():
    lines = app_main.LOCKED_CARD_LINES
    assert set(lines) == {c[1] for c in app_main.PREMIUM_CHECKS}
    assert not set(lines) & {c[1] for c in app_main.FREE_CHECKS}

    for title, line in lines.items():
        answers, _, publisher = line.partition(" · ")
        assert publisher, f"{title} names no publisher after a middle dot"
        assert line.count("·") == 1, f"{title} has more than one middle dot"
        for wrong in ("—", "–", " - ", "!"):
            assert wrong not in line, f"{title} uses {wrong!r}"
        assert answers[0].isupper() and not answers.endswith("."), title
        # The line says what the check answers, never what it found: no
        # figure, and no verdict word a finding would carry.
        assert not re.search(r"\d+(\.\d+)?%|£", line), f"{title} reads a figure"

    # A check whose service did not answer keeps the publisher and offers
    # nothing else.
    assert set(app_main.LOCKED_CARD_UNAVAILABLE) == set(lines)
    for title, line in app_main.LOCKED_CARD_UNAVAILABLE.items():
        assert line.startswith("Could not be checked just now · ")
        assert line.endswith(lines[title].split(" · ", 1)[1])


def test_b2_every_locked_card_in_the_templates_asks_for_its_line():
    """One call per locked check, across every template that draws a
    card: a sixteenth locked card added without a line, or a title typed
    differently from the one in main.py, fails here rather than
    rendering an empty status."""
    templates = pathlib.Path(app_main.__file__).parent / "templates"
    calls = []
    for path in sorted(templates.rglob("*.html")):
        calls += re.findall(r"locked\.locked_status\('([^']+)'", path.read_text(encoding="utf-8"))
    assert sorted(calls) == sorted(app_main.LOCKED_CARD_LINES)


def test_b2_a_signed_out_report_shows_a_line_on_every_locked_card(client, fake_report):
    from tests.conftest import fake_gather
    fake_report(gather=fake_gather(**B2_ANSWERED))
    body = client.get("/property?postcode=M14+5TG&house_number=1").text

    assert len(_b1_locked_cards(body)) == len(app_main.PREMIUM_CHECKS)
    for _icon, title, _value, _source in app_main.PREMIUM_CHECKS:
        shown = _b2_line(body, html.escape(title))
        assert shown == _b2_escaped(title), title
    # The line is one of the card's own status lines, which is where its
    # size comes from, and never on its own.
    assert 'class="dashboard-card-locked-line"' not in body
    # The lock overlay and its screen-reader words are untouched.
    assert "dashboard-card-lock-label" in body


def test_b2_a_postcode_only_report_says_the_extension_check_needs_a_house_number(client, fake_report):
    from tests.conftest import fake_gather
    fake_report(gather=fake_gather(**B2_ANSWERED))
    body = client.get("/property?postcode=M14+5TG").text

    assert _b2_line(body, "Extended or Modified") == app_main.LOCKED_CARD_NEEDS_HOUSE_NUMBER
    assert "Needs a house number · EPC register" == app_main.LOCKED_CARD_NEEDS_HOUSE_NUMBER
    # Nothing else changes: the other fourteen read as they do with a
    # house number.
    assert _b2_line(body, "Aspect") == _b2_escaped("Aspect")
    assert _b2_line(body, "Valuation Estimate") == _b2_escaped("Valuation Estimate")


def test_b2_a_locked_check_whose_service_failed_says_so(client, fake_report):
    """conftest's fake marks every service it is not given as failed, so
    the default report is the one the audit walked into: the coal mining
    check down behind a lock. The publisher is read from
    LOCKED_CARD_LINES rather than typed: the Coal Authority became the
    Mining Remediation Authority, whose own service the check has used
    since 17 Sep 2026."""
    from tests.conftest import fake_gather
    fake_report(gather=fake_gather())
    body = client.get("/property?postcode=M14+5TG&house_number=1").text

    publisher = app_main.LOCKED_CARD_LINES["Mining Risk"].split(" · ")[1]
    assert publisher == "Mining Remediation Authority"
    assert _b2_line(body, "Mining Risk") == "Could not be checked just now · " + publisher
    for title in ("Mining Risk", "Air Quality", "Subsidence Risk", "Historic Contamination"):
        shown = _b2_line(body, html.escape(title))
        assert shown == _b2_escaped_unavailable(title), title
        assert "Data unavailable" not in shown
        # Not offered as a check that ran.
        assert shown != _b2_escaped(title)
    # A check that did answer still reads as its own line on the same page.
    assert _b2_line(body, "Bus Service") == _b2_escaped("Bus Service")


def test_b2_the_lock_and_the_one_sign_up_offer_are_unchanged(client, fake_report, monkeypatch):
    from tests.conftest import fake_gather
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    # The default fake, so the unlocked half of this test renders the
    # cards the way the rest of the suite does.
    gather = fake_gather()
    fake_report(gather=gather)
    body = client.get("/property?postcode=M14+5TG&house_number=1").text
    lock_label = "Sign up: 1 free full report"

    tag, block = _b1_card(body, "Mining Risk")
    assert "dashboard-card-lock-overlay" in block and "data-lock-redirect" in tag
    assert lock_label in block          # the overlay's words for a screen reader
    # Still one offer on the page, and still the sign-up the audit left.
    page = _without_popup(body)
    assert page.count('class="paywall-banner-cta"') == 1
    wall = re.search(r'<a class="paywall-banner-cta" href="([^"]*)"', _banner(body)).group(1)
    assert wall.startswith("/signup?next=")

    # Unlocked, the lines are gone and the cards read their findings again.
    fake_report(gather=gather)
    assert _signup(client, "b2-unlocked@customer.test").status_code == 303
    assert client.post("/property/unlock", data={"postcode": "M14 5TG", "house_number": "1"},
                       follow_redirects=False).status_code == 303
    full = client.get("/property?postcode=M14+5TG&house_number=1").text
    assert 'class="dashboard-card-status dashboard-card-locked-line"' not in full
    assert _b1_locked_cards(full) == []
    assert _card_status(full, "Bus Service") == "No timetable data"


def test_b2_the_line_is_readable_at_the_cards_own_status_size():
    rule = STYLE_CSS.split(
        ".dashboard-card-locked .dashboard-card-status.dashboard-card-locked-line", 1)[1].split("}", 1)[0]
    assert "display: block" in rule and "display: none" not in rule
    # Size, weight and family stay the status line's own: only the colour
    # moves, and it moves to a token so dark mode follows.
    for property_ in ("font-size", "font-family", "font-weight", "letter-spacing", "line-height"):
        assert property_ not in rule, f"the locked line must not set {property_}"
    assert "var(--ink-soft)" in rule
    assert "body.theme-dark .dashboard-card-locked-line" not in STYLE_CSS
    assert STYLE_CSS.count("dashboard-card-locked-line") == 1


# ---- B3. A locked check's answer is not in the page at all ---------------
# The lock was a stylesheet rule. A signed-out report rendered every
# locked finding into its HTML and hid two lines of it with display:
# none: "1.7x WHO guideline at worst", "Improbable by 2030", "16.5 an
# hour", "4 likely, 0 borderline, 4 unlikely", and, in the pop-ups,
# which were not hidden at all, the valuation's "Estimate (median)" with
# its table of sales, every GP practice, every bus stop and every
# brownfield site. The catchment rings and the stations went into the
# map's own script. Anyone who opened the page source read the lot, and
# so did every crawler and scraper. Now nothing a locked check found is
# rendered: the card carries its line (B2), the pop-up carries how the
# check is done, who publishes it and the way in, and the two map
# branches and the two follow-up endpoints are sent nothing.

# Every locked check, answered, with values that appear nowhere else on
# the page. What each one found does not matter; that it is absent does.
B3_FINDINGS = dict(
    valuation={"estimate": 412345, "low": 401111, "high": 423333, "sample_size": 7,
               "years_window": 2, "floor_area_variance_pct": 5},
    valuation_floor_area_known=True,
    price_per_sqm={"sample_size": 4, "median": 5111, "low": 4900, "high": 5300, "years_window": 1,
                   "rows": [{"address": "9 Locked Lane", "date": "2026-02-01", "amount": 404040.0,
                             "floor_area": 79, "per_sqm": 5115, "sold_per_sqm": 5090, "distance_m": 90}],
                   "subject": None, "subject_floor_area": 79, "implied_value": 403769,
                   "subject_vs_median_pct": None},
    price_trend={"area_name": "Lockedshire", "pct_change": 17.3, "start_price": 211111,
                 "current_price": 247777, "projections": [{"months_ahead": 12, "price": 255555}],
                 "series": [{"period": "2021-06", "average_price": 211111},
                            {"period": "2026-06", "average_price": 247777}]},
    extension_signal={"likely_extended": True, "change_pct": 22.5, "earliest_area": 71,
                      "latest_area": 87, "earliest_date": "2011-03-02", "latest_date": "2025-12-16"},
    orientation={"rear_facing": "South-west", "front_facing": "North-east", "nearest_road": "Locked Lane"},
    sewage_error=False,
    sewage_outfalls=[{"name": "Locked Outfall", "water_company": "Locked Water",
                      "receiving_water": "River Locked", "spill_count": 41, "duration_hrs": 312.4,
                      "distance_m": 880, "year": 2025}],
    clay_risk={"class_2030": "Probable", "label_2030": "Probable",
               "class_2050": "Probable", "label_2050": "Probable"},
    air_quality={"year": 2024, "pollutants": [{"name": "no2", "label": "NO2", "value": 34.9,
                                               "who_guideline": 10, "times_guideline": 3.49}]},
    historic_landfill={"status": "nearby", "site_name": "Locked Tip", "distance_m": 420},
    coal_mining={"present": True, "area_name": "Locked Coalfield"},
    catchment=[{"school_name": "Locked Primary", "phase": "Primary",
                "authority": "Lockedshire", "rings": None}],
    catchment_distance_schools=[{"name": "Locked Primary", "latitude": 53.45, "longitude": -2.22,
                                 "phase_group": "Primary", "ofsted_rating": None,
                                 "ofsted_rating_label": None, "radius_miles": 0.87, "is_real": True,
                                 "academic_year": "2025/26", "source_authority": "Lockedshire",
                                 "property_distance_miles": 0.31, "within_catchment": True,
                                 "verdict": {"level": "likely", "label": "Likely"}}],
    catchment_distance_count=1, catchment_distance_any_real=True,
    stations={"rail": {"name": "Locked Central", "distance_m": 410,
                       "city_journeys": [{"minutes": 13, "city": "Manchester", "departs": "08:00",
                                          "arrives": "08:13", "operator": "Locked Rail"}]}},
    stations_list={"rail": [{"name": "Locked Central", "distance_m": 410, "lat": 53.46, "lon": -2.23}],
                   "tube": [], "tram": [], "bus": []},
    nearest_transport={"name": "Locked Central", "distance_m": 410, "walking_distance_m": 480},
    amenities={k: [] for k in ("restaurant", "supermarket", "pharmacy", "pub", "hospital", "parking",
                               "ev_charging", "gp", "dentist", "green_space", "wind_turbine",
                               "solar_farm")},
    wellbeing={"good_health_pct": 63.7, "health_breakdown": [{"label": "Very good health", "pct": 63.7}],
               "marital_breakdown": [], "nssec_breakdown": []},
    brownfield={"covered": True, "count": 3, "hectares": 1.4, "dwellings": 144, "dwellings_stated": 2,
                "permissioned": 1, "radius_m": 800, "country": "England",
                "council": {"name": "Lockedshire", "published": True, "register_count": 12},
                "newest_entry": "2025-01-01",
                "sites": [{"distance_m": 300, "address": "Locked Yard", "hectares": 1.4,
                           "max_dwellings": 144, "min_dwellings": 100, "permission": "Yes",
                           "permission_type": "Full", "ownership": "Private",
                           "entry_date": "2025-01-01", "plan_url": ""}]},
    bus_service={"count": 2, "radius_m": 500, "ref_weekday": "2026-06-02", "ref_sunday": "2026-06-07",
                 "feed_date": "2026-06-01", "routes": ["142", "197"],
                 "best": {"name": "Locked Road Stop A", "atco_code": "X1", "distance_m": 120,
                          "weekday_day": 33, "weekday_day_per_hour": 16.5, "weekday_eve_per_hour": 6.0,
                          "sunday_day_per_hour": 4.5, "weekday_first": "05:12", "weekday_last": "23:44"},
                 "stops": [{"name": "Locked Road Stop A", "atco_code": "X1", "distance_m": 120,
                            "weekday_day_per_hour": 16.5, "weekday_eve_per_hour": 6.0,
                            "sunday_day_per_hour": 4.5, "weekday_first": "05:12",
                            "weekday_last": "23:44", "routes": ["142"]}]},
    health={"count": 2, "radius_m": 3000, "patients_date": "2026-06-01", "workforce_date": "2026-05-01",
            "median_patients_per_qualified_gp": 2294, "ae_period": "June 2026", "icb_name": "Locked ICB",
            "national_type1_within_4h_pct": 58.1,
            "nearest": {"patients": 12345, "patients_per_qualified_gp": 3777, "vs_median": 1.65},
            "practices": [{"name": "Locked Medical Centre", "distance_m": 300, "patients": 12345,
                           "qualified_gp_fte": 3.2, "gp_fte": 4.0, "patients_per_qualified_gp": 3777,
                           "vs_median": 1.65, "estimated": False}],
            "trusts": [{"name": "Locked NHS Trust", "type1_attendances": 9876,
                        "type1_within_4h_pct": 61.4, "all_within_4h_pct": 71.2}]},
)

# One string per locked check that the answer, and only the answer, puts
# on the page. Written as the page writes them.
B3_ANSWERS = (
    "412,345", "401,111", "Estimate (median)", "5,111",          # Valuation Estimate
    "17.3", "247,777", "255,555",                                # Price Trend & Forecast
    "2011-03-02", "87 m", "Locked Lane",                         # Extended or Modified, Aspect
    "South-west", "Locked Outfall", "312.4",                     # Aspect, Sewage Discharge
    "Probable by 2030", "34.9", "3.49",                          # Subsidence Risk, Air Quality
    "Locked Tip", "Locked Coalfield",                            # Contamination, Mining Risk
    "Locked Primary", "Lockedshire",                             # School Catchment Areas
    "Locked Central", "Locked Rail",                             # Getting Around
    "63.7", "Locked Yard", "144",                                # Wellbeing, Development Nearby
    "Locked Road Stop A", "16.5",                                # Bus Service
    "Locked Medical Centre", "3,777", "2,294", "Locked NHS Trust", "61.4",   # Health Services
)

# The pop-up each locked card opens. Held against LOCKED_CARD_LINES
# below, so a sixteenth locked check cannot be added without saying
# which pop-up has to stay shut.
B3_MODALS = {
    "Valuation Estimate": "modal-valuation",
    "Price Trend & Forecast": "modal-price-trend",
    "Extended or Modified": "modal-extension",
    "Aspect": "modal-orientation",
    "Sewage Discharge": "modal-sewage",
    "Subsidence Risk": "modal-clay-risk",
    "Air Quality": "modal-air-quality",
    "Historic Contamination": "modal-historic-landfill",
    "Mining Risk": "modal-coal-mining",
    "School Catchment Areas": "modal-catchment",
    "Getting Around": "modal-getting-around",
    "Health, Relationships & Social Grade": "modal-wellbeing",
    "Development Nearby": "modal-brownfield",
    "Bus Service": "modal-bus",
    "Health Services": "modal-health",
}

B3_WAY_IN = f"One of the {len(app_main.PREMIUM_CHECKS)} checks that open with a full report."


def _b3_gather():
    """The same findings every time, with the trend chart the real
    gather computes from the trend it is given."""
    from tests.conftest import fake_gather
    return fake_gather(price_trend_chart=app_main._price_trend_chart(B3_FINDINGS["price_trend"]),
                       **B3_FINDINGS)


def _b3_report(client, fake_report, gather=None, house_number="1"):
    fake_report(gather=gather or _b3_gather())
    r = client.get(f"/property?postcode=M14+5TG&house_number={house_number}")
    assert r.status_code == 200
    return r.text


def _b3_modal(body, modal_id):
    assert f'id="{modal_id}"' in body, f"the report has no {modal_id}"
    return body.split(f'id="{modal_id}"', 1)[1].split("</dialog>", 1)[0]


def _b3_subscriber(client, email):
    assert _signup(client, email).status_code == 303
    with db.get_session() as session:
        user = auth.find_user_by_email(session, email)
        user.is_premium, user.plan = True, "monthly"
        session.commit()


def test_b3_a_signed_out_report_holds_no_locked_answer_anywhere(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    body = _b3_report(client, fake_report)

    # Every locked check answered, and not one of those answers is in
    # the page: not in a card, not in a pop-up, not in a script.
    for answer in B3_ANSWERS:
        assert answer not in body, f"a signed-out report still carries {answer!r}"
    # The tables those answers sat in are gone with them.
    for heading in ("Estimate (median)", "Catchment radius", "Patients per GP",
                    "Weekday daytime, an hour", "Listed since", "Rear/garden-facing"):
        assert heading not in body, heading

    # The report itself is whole: fifteen locked cards, each with its
    # line, and the free checks still read their own figures.
    assert len(_b1_locked_cards(body)) == len(app_main.PREMIUM_CHECKS)
    assert _b2_line(body, "Air Quality") == _b2_escaped("Air Quality")
    assert "£250,000" in body                       # the sale the free card names
    assert _card_status(body, "Household Income") == "No data available"


def test_b3_every_locked_popup_holds_its_method_its_source_and_the_way_in(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert sorted(B3_MODALS) == sorted(app_main.LOCKED_CARD_LINES)
    body = _b3_report(client, fake_report)

    for title, modal_id in B3_MODALS.items():
        modal = _b3_modal(body, modal_id)
        # The way in, worded and pointed the way the card is.
        assert B3_WAY_IN in _flat(modal), f"{modal_id} does not offer the way in"
        tag, _block = _b1_card(body, html.escape(title))
        redirect = re.search(r'data-lock-redirect="([^"]*)"', tag).group(1)
        assert f'<a href="{redirect}">' in modal, f"{modal_id} does not point where its card does"
        # Nothing a figure could hide in.
        for shape in ("<table", "<svg", "<circle", "catchment-badge", "clay-badge"):
            assert shape not in modal, f"{modal_id} still renders {shape}"
        # Still says who publishes the answer it is not giving.
        assert len(_flat(modal)) > len(B3_WAY_IN) + 200, f"{modal_id} says nothing about the check"


def test_b3_neither_map_branch_is_sent_a_locked_checks_data(client, fake_report, monkeypatch):
    """Production renders the Google branch and dev the Leaflet one, so
    the catchment shapes, the admission rings and the stations have to
    go from both."""
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    for key in ("", "test-maps-key"):
        monkeypatch.setenv("GOOGLE_MAPS_API_KEY", key)
        body = _b3_report(client, fake_report)
        assert ("maps.googleapis.com" in body) == bool(key)
        scripts = "".join(re.findall(r"<script[^>]*>(.*?)</script>", body, re.S))
        for value in ("Locked Primary", "Locked Central", "0.87", "Lockedshire", "2025/26"):
            assert value not in scripts, f"the map script carries {value!r} with key={key!r}"
        assert "addStationsToMap({})" in scripts.replace(" ", "")


def test_b3_a_subscriber_and_an_unlocked_home_read_every_figure(client, fake_report, monkeypatch):
    """The other half of the gate: nothing here is taken from a reader
    who has paid, or who has spent their free report on this home."""
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    _b3_subscriber(client, "b3-subscriber@customer.test")
    full = _b3_report(client, fake_report)
    for answer in B3_ANSWERS:
        assert answer in full, f"a subscriber has lost {answer!r}"
    assert _b1_locked_cards(full) == []
    assert B3_WAY_IN not in full
    # The map gets its layers back, in whichever branch is rendered.
    assert "Locked Primary" in full and "Locked Central" in full

    # A free account that has spent its one report on this address reads
    # the same page. Its own house number, so no address in this file
    # ends up unlocked by two accounts: /admin counts that pattern and a
    # test of its own reads the count.
    client.cookies.clear()
    assert _signup(client, "b3-unlocked@customer.test").status_code == 303
    fake_report(gather=_b3_gather())
    assert client.post("/property/unlock", data={"postcode": "M14 5TG", "house_number": "9"},
                       follow_redirects=False).status_code == 303
    mine = _b3_report(client, fake_report, house_number="9")
    for answer in B3_ANSWERS:
        assert answer in mine, f"an unlocked home has lost {answer!r}"


def test_b3_the_two_follow_up_endpoints_answer_by_who_is_asking(client, fake_report, monkeypatch):
    """The valuation and the amenities fragments are fetched after the
    page renders and swapped into it, so the same decision has to be
    made again there: both used to send the finding to a page that had
    just rendered it locked."""
    from app.services import amenities as amenities_service
    monkeypatch.setattr(email_service, "can_verify", lambda: False)

    async def _comparables(_lat, _lon):
        return []

    async def _gather(location, house_number, premium_unlocked=False, wait_for_slow=False):
        return {"location": location, **_b3_gather()}

    def _apply(context, *_args, **_kw):
        context["valuation"] = B3_FINDINGS["valuation"]
        context["price_per_sqm"] = B3_FINDINGS["price_per_sqm"]
        context["valuation_floor_area_known"] = True

    async def _amenities(_lat, _lon, lite=False):
        return {"categories": B3_FINDINGS["amenities"], "stations": B3_FINDINGS["stations"],
                "stations_list": B3_FINDINGS["stations_list"]}

    fake_report(gather=_b3_gather())
    monkeypatch.setattr(app_main, "_comparables_fetch", _comparables)
    monkeypatch.setattr(app_main, "_full_property_gather", _gather)
    monkeypatch.setattr(app_main, "_apply_valuation", _apply)
    monkeypatch.setattr(amenities_service, "nearby_amenities_and_station", _amenities)

    out = client.get("/api/property/valuation?postcode=M14%205TG").json()
    for answer in ("412,345", "401,111", "423,333", "Estimate (median)", "5,111", "Locked Lane"):
        assert answer not in out["card"] + out["body"], f"the valuation endpoint sent {answer!r}"
    assert B3_WAY_IN in _flat(out["body"])

    nearby = client.get("/api/property/amenities?postcode=M14%205TG").json()
    assert "Locked Central" not in nearby["transport_card"] + nearby["transport_body"]
    assert nearby["stations_list"] == {}

    # Signed in as a subscriber, both reply in full.
    _b3_subscriber(client, "b3-endpoints@customer.test")
    out = client.get("/api/property/valuation?postcode=M14%205TG").json()
    assert "£412,345" in out["card"] and "Estimate (median)" in out["body"] and "£5,111" in out["body"]
    nearby = client.get("/api/property/amenities?postcode=M14%205TG").json()
    assert "Locked Central" in nearby["transport_body"]
    assert nearby["stations_list"]["rail"][0]["name"] == "Locked Central"


def test_b3_the_stylesheet_no_longer_hides_a_locked_cards_value():
    """The CSS that hid the finding is gone, because there is no longer
    a finding to hide. A rule like it coming back would mean the value
    is being written into the page again."""
    assert ".dashboard-card-locked .dashboard-card-substat" not in STYLE_CSS
    assert ".premium-preview-lede ~ .dashboard-grid .dashboard-card-locked" not in STYLE_CSS
    locked_rules = [block for block in STYLE_CSS.split("}")
                    if ".dashboard-card-locked .dashboard-card-status" in block]
    assert len(locked_rules) == 1 and "display: none" not in locked_rules[0]


# ---- Batch B fix pass: what the review of B1 to B3 found left over -------
# B3 took every locked finding out of the cards, the pop-ups, the two map
# branches and the two follow-up endpoints, and left one place standing:
# the "Questions to ask" teaser, which named five of them in plain body
# text, one of them a figure, on the same screen where their cards say
# only what the check answers. A question triggered by a locked check
# states that check's finding in its own trigger, so the teaser now draws
# on buyer_questions_teaser, the list with those questions dropped.

# The triggers of the five questions a locked check can raise, as the
# page writes them for the B3 findings.
B3_LOCKED_TRIGGERS = (
    "Coal Mining Reporting Area",
    "Historic landfill on or near the site",
    "Frequent sewage discharges nearby",
    "Rising clay subsidence risk",
    "Floor area grew about +22% between energy certificates",
)


def _b3_questions(body):
    """The "Questions to ask" section, whichever branch rendered it."""
    assert 'id="buyer-questions"' in body, "the report has no questions section"
    return _flat(body.split('id="buyer-questions"', 1)[1].split("</section>", 1)[0])


def _b3_flood_gather():
    """The B3 findings plus one free check that raises questions of its
    own, so the teaser has something left to show."""
    gather = _b3_gather()
    gather["flood_zone"] = {"zone": 3, "label": "Zone 3 (high probability)", "source": None}
    return gather


def test_b3_the_questions_teaser_names_no_locked_finding(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    teaser = _b3_questions(_b3_report(client, fake_report, gather=_b3_flood_gather()))

    # The teaser still does its job, from the flood question a free check
    # raised: a real question in full, and where the rest came from.
    assert "questions</strong> were generated for this property." in teaser
    assert "Has the property ever flooded" in teaser
    assert "The others come from: Flood: Zone 3 (high probability)." in teaser
    # And it names none of the five findings behind the lock.
    for trigger in B3_LOCKED_TRIGGERS:
        assert trigger not in teaser, f"the teaser still names {trigger!r}"

    # Nowhere else on a signed-out page either. "Coal Mining Reporting
    # Area" is left out of this sweep on purpose: the Mining Risk pop-up
    # names it as what the check answers, which is the point of B2, not a
    # reading of this address.
    body = _flat(_b3_report(client, fake_report, gather=_b3_flood_gather()))
    for trigger in B3_LOCKED_TRIGGERS[1:]:
        assert trigger not in body, f"a signed-out report still carries {trigger!r}"


def test_b3_the_teaser_counts_every_question_and_a_subscriber_reads_them_all(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    teaser = _b3_questions(_b3_report(client, fake_report, gather=_b3_flood_gather()))
    counted = int(re.search(r"<strong>(\d+) questions</strong>", teaser).group(1))

    _b3_subscriber(client, "b-fix-questions@customer.test")
    full = _b3_questions(_b3_report(client, fake_report, gather=_b3_flood_gather()))
    # How many questions were generated is not a finding, so the locked
    # count is still every question, the five dropped ones included.
    assert counted == len(re.findall(r'class="bq-item"', full))
    assert counted > len(B3_LOCKED_TRIGGERS)
    for trigger in B3_LOCKED_TRIGGERS:
        assert trigger in full, f"a subscriber has lost {trigger!r}"


def test_b3_only_a_locked_checks_question_is_dropped_from_the_teaser():
    from app.services import overview_score, solicitor_questions
    # The tags are the keys the score already uses for the same idea, so
    # a check moving between the tiers moves in one place.
    assert solicitor_questions.LOCKED_CHECKS <= overview_score._PREMIUM_ONLY_CONCERNS

    questions = solicitor_questions.build(dict(B3_FINDINGS))
    assert {q["check"] for q in questions if q["check"]} == set(solicitor_questions.LOCKED_CHECKS)
    kept = solicitor_questions.without_locked(questions)
    assert len(kept) == len(questions) - len(solicitor_questions.LOCKED_CHECKS)
    # What is left says nothing about this address at all.
    assert {q["trigger"] for q in kept} == {"Every purchase"}


def test_b3_a_failed_follow_up_fetch_leaves_a_locked_cards_line_alone():
    """Both follow-up fetches used to write "Data unavailable" into the
    card's status when they failed. That span was hidden inside a locked
    card until 17 Sep 2026; it is the neutral line now, so a failed fetch
    would have put a reading back on a card that shows none."""
    template = (ROOT / "app" / "templates" / "property.html").read_text(encoding="utf-8")
    assert template.count("textContent = 'Data unavailable'") == 2
    assert "if (status && !card.classList.contains('dashboard-card-locked')) {" in template
    assert "if (card && card.classList.contains('dashboard-card-locked')) return;" in template


# One publisher each of the four locked checks whose method paragraph is
# written twice, once for the locked reader and once for the answered
# branch. A rename in one copy and not the other now fails here rather
# than leaving the locked reader with the old source.
B3_SHARED_PUBLISHERS = {
    "modal-price-trend": "HM Land Registry's monthly House Price Index",
    "modal-health": "NHS England Digital",
    "modal-bus": "Department for Transport's Bus Open Data Service",
    "modal-brownfield": "the government's planning data platform",
}


def test_b3_the_duplicated_method_paragraphs_name_the_same_publisher(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    locked = _b3_report(client, fake_report)
    _b3_subscriber(client, "b-fix-publishers@customer.test")
    answered = _b3_report(client, fake_report)

    for modal_id, publisher in B3_SHARED_PUBLISHERS.items():
        assert publisher in _flat(_b3_modal(locked, modal_id)), f"{modal_id} locked lost {publisher!r}"
        assert publisher in _flat(_b3_modal(answered, modal_id)), f"{modal_id} answered lost {publisher!r}"


def test_b3_the_catchment_popup_no_longer_offers_an_upgrade_it_cannot_show():
    """A branch on catchment_distance_count ended "Upgrade to Premium to
    see them." Only an unlocked reader reaches that chain now, and for an
    unlocked reader every counted school is in the list above it, so the
    branch could not render and its wording would have been wrong."""
    template = _without_template_comments(
        (ROOT / "app" / "templates" / "property.html").read_text(encoding="utf-8"))
    assert "Upgrade to Premium to see them" not in template


# ---- C1. After the free unlock: kept, and what would make us email -------
# POST /property/unlock sends the reader back to the report with
# &unlocked=1, where it said "Unlocked. Every card on this property is
# yours, for good." and nothing more. The "Kept in My properties" line
# and the first-home extension offer were both gated on auto_saved,
# which is true only on the visit that created the saved row, so on that
# reload both vanished: the one moment the report opened in full said
# nothing about the home being kept, and nothing about what, if
# anything, would bring the reader back.

SAVED_NOTE = 'class="section-sub auto-saved-note"'


def _c1_open(client, fake_report, postcode, outcode, house_number):
    fake_report(location=fake_location(postcode=postcode, outcode=outcode))
    r = client.get("/property", params={"postcode": postcode, "house_number": house_number})
    assert r.status_code == 200
    return r.text


def _c1_unlock(client, fake_report, postcode, outcode, house_number):
    """Claim the free report the way a reader does: open it, POST the
    yes, then follow the redirect the POST hands back."""
    _c1_open(client, fake_report, postcode, outcode, house_number)
    r = client.post("/property/unlock",
                    data={"postcode": postcode, "house_number": house_number},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")
    unlocked = client.get(r.headers["location"])
    assert unlocked.status_code == 200
    return unlocked.text


def _c1_notice(body):
    """The unlock notice, from its id to the end of its block."""
    assert 'id="use-free-report"' in body, "the report has no unlock notice"
    return _flat(body.split('id="use-free-report"', 1)[1].split("</div>", 1)[0])


def test_c1_the_unlocked_report_says_it_is_kept_and_names_every_trigger(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    email = "c1-unlocked@customer.test"
    assert _signup(client, email).status_code == 303
    body = _c1_unlock(client, fake_report, "M16 4AA", "M16", "12")

    # One notice, not several: the unlock, the saved home and the
    # triggers are all in the block the reader is already looking at.
    assert _flat(body).count(SAVED_NOTE) == 1
    notice = _c1_notice(body)
    assert "Unlocked. Every card on this property is yours, for good." in notice
    assert 'Kept in <a href="/watchlist">My properties</a>' in notice
    for trigger in app_main.alert_triggers("12"):
        assert trigger in notice, f"the unlocked report does not name {trigger!r}"
    # House 12 is saved with its number, so the job counts only its own
    # sales: the notice must not promise a neighbour's.
    assert "a sale of this home is recorded" in notice
    assert "sale is recorded at this postcode" not in notice
    assert "Never on a schedule." in notice
    # The address is named because the page already holds it, the way
    # the confirmation banner does.
    assert f"We email {email}" in notice
    # And the way out of the list is still beside it.
    assert 'action="/watchlist/remove"' in notice and ">Remove it</button>" in notice


def test_c1_the_first_home_keeps_its_extension_offer_through_the_unlock(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, "c1-extension@customer.test").status_code == 303
    before = _c1_open(client, fake_report, "M16 4BB", "M16", "14")
    assert "Add it to Chrome" in before
    # It used to disappear here, on the account's first home, at the
    # moment the reader said yes.
    after = _c1_unlock(client, fake_report, "M16 4BB", "M16", "14")
    assert "Add it to Chrome" in after
    assert 'class="compare-offer extension-offer"' in after


def test_c1_a_second_saved_home_gets_the_comparison_and_not_the_extension(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, "c1-second@customer.test").status_code == 303
    _c1_unlock(client, fake_report, "M17 5AA", "M17", "16")
    second = _c1_open(client, fake_report, "M17 5BB", "M17", "18")

    assert 'class="compare-offer" data-animate' in second
    assert 'class="compare-offer extension-offer"' not in second
    assert "Add it to Chrome" not in second
    # The second home is saved too, so it says so and names the same
    # triggers, once, even though its cards are locked.
    assert _flat(second).count(SAVED_NOTE) == 1
    assert "Never on a schedule." in second
    for trigger in app_main.alert_triggers("18"):
        assert trigger in second, f"the second home does not name {trigger!r}"


def test_c1_the_saved_line_is_there_on_every_later_visit_too(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, "c1-return@customer.test").status_code == 303
    _c1_unlock(client, fake_report, "M17 5CC", "M17", "20")
    # A plain visit, no &unlocked=1: the row was saved on an earlier
    # visit, so auto_saved is false and this used to say nothing.
    later = _flat(_c1_open(client, fake_report, "M17 5CC", "M17", "20"))
    assert later.count(SAVED_NOTE) == 1
    assert "Kept in" in later and "Never on a schedule." in later
    assert app_main.alert_triggers("20")[0] in later


def test_c1_the_anonymous_save_line_names_the_same_triggers(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    body = _flat(_c1_open(client, fake_report, "M17 5DD", "M17", "22"))
    assert "Save it free</a> and be told when " + app_main.alert_triggers_short("22") in body
    assert "Never on a schedule." in body
    # "when anything on it changes" promised more than the alert job
    # sends, and told nobody what to expect.
    assert "be told when anything on it changes" not in body


def _c1_sales(*addresses):
    """Price Paid records at one postcode, addressed the way
    land_registry builds them (SAON, PAON, street)."""
    return [{"address": address, "amount": 250000} for address in addresses]


def _c1_sale_summary(records, house_number):
    """tx_count and avg_price the way both real summaries build them:
    _comparison_summary for the alert job and _summary_from_report (over
    the gather's own filtered list) for the page, each through
    _filter_by_address. A bare tx_count here once let a house-numbered
    home be promised every sale at its postcode."""
    mine = app_main._filter_by_address(records, house_number)
    return {"tx_count": len(mine), "avg_price": app_main._average_amount(mine)}


C1_BASE = {"epc_date": "2024-01-01", "flood_zone": "Zone 2 (medium probability)",
           "price_growth_pct": 3.0}
C1_STREET = _c1_sales("9 ACACIA AVENUE", "14 ACACIA AVENUE", "22 ACACIA AVENUE")


def _c1_moved(house_number, change_for):
    """What the job compares once each named trigger has happened,
    keyed by the words the page uses for this home."""
    before = {**C1_BASE, **_c1_sale_summary(C1_STREET, house_number)}
    triggers = app_main.alert_triggers(house_number)
    sale_at = C1_STREET + _c1_sales(f"{change_for} ACACIA AVENUE")
    moved = {
        triggers[0]: _c1_sale_summary(sale_at, house_number),
        triggers[1]: {"epc_date": "2026-01-01"},
        triggers[2]: {"flood_zone": "Zone 3 (high probability)"},
        triggers[3]: {"price_growth_pct": -2.0},
    }
    return before, moved


def test_c1_each_named_trigger_is_one_the_alert_job_really_sends_on():
    """Four lines beside a saved home, four branches of
    _snapshot_changes. If one stops firing, the promise on the page is
    wrong, which is the failure this catches."""
    # A house-numbered home: its own sale fires, and it is the one the
    # page names.
    before, moved = _c1_moved("9", change_for="9")
    assert list(moved) == list(app_main.alert_triggers("9"))
    assert list(moved)[0] == "a sale of this home is recorded"
    for trigger, change in moved.items():
        assert app_main._snapshot_changes(before, {**before, **change}), \
            f"nothing in the alert job fires for {trigger!r}"

    # A postcode-only home: any sale at the postcode fires, and the page
    # says so.
    before, moved = _c1_moved("", change_for="31")
    assert list(moved)[0] == "a new sale is recorded at this postcode"
    for trigger, change in moved.items():
        assert app_main._snapshot_changes(before, {**before, **change}), \
            f"nothing in the alert job fires for {trigger!r}"

    # The short lines are the same four, so the promises cannot drift.
    for hn, sale in (("9", "a sale of this home is recorded"), ("", "a sale is recorded at this postcode")):
        short = app_main.alert_triggers_short(hn)
        assert short.startswith(sale + ", ")
        for word in ("energy certificate", "flood zone", "area prices"):
            assert word in short


def test_c1_the_saved_line_is_written_once_and_read_from_the_constants():
    """One partial, included where it is needed. The line was written
    into property.html by hand before, and the triggers are never
    typed into a template at all: not the report's, and not the list
    line on My properties either."""
    assert (ROOT / "app" / "templates" / "_saved_alerts.html").exists()
    template = _without_template_comments(
        (ROOT / "app" / "templates" / "property.html").read_text(encoding="utf-8"))
    assert template.count('{% include "_saved_alerts.html" %}') == 2
    assert 'Kept in <a href="/watchlist">My properties</a>' not in template, \
        "the saved line is written out in property.html again"
    typed = {*app_main.alert_triggers("1"), *app_main.alert_triggers(""),
             app_main.ALERT_OTHER_TRIGGERS_SHORT, app_main.ALERT_TRIGGERS_LIST}
    for path in (ROOT / "app" / "templates").rglob("*.html"):
        text = _without_template_comments(path.read_text(encoding="utf-8"))
        for trigger in typed:
            assert trigger not in text, f"{path.name} types {trigger!r} out by hand"
    watchlist_template = _without_template_comments(
        (ROOT / "app" / "templates" / "watchlist.html").read_text(encoding="utf-8"))
    assert "{{ alert_triggers_list }}" in watchlist_template
    assert "if anything on this list changes" not in watchlist_template
    assert "No need to keep checking back" not in watchlist_template


# ---- C2. Both prices on the wall, and the questions under them ------------
# The signed-in wall on a locked home said "Opening another is £9.99 a
# month" and named no other plan, so the reader most likely to be standing
# there, someone checking one house, was offered an open-ended monthly
# bill and nothing else. The three-month plan that covers a house hunt was
# invisible until /premium, where its card was badged "SAVE 17% VS
# MONTHLY" (a figure typed into stripe_billing, not worked out), its
# second line repeated the price set in type directly above it, and
# "Before you pay", which answers "am I stuck with a monthly bill?", sat
# seventeen phone screens down, below every check card on the page.

PROPERTY_TEMPLATE = ROOT / "app" / "templates" / "property.html"
TERMS_TEMPLATE = ROOT / "app" / "templates" / "terms.html"


def _pricing_cards(body):
    """{price heading: (badge, the line under the price)} per card."""
    grid = body.split('<div class="pricing-grid">', 1)[1].split('<p class="pricing-reassure">', 1)[0]
    plain = lambda s: _flat(html.unescape(s))
    cards = {}
    for chunk in grid.split('<div class="pricing-card')[1:]:
        price = re.search(r'<h2 class="pricing-card-price">(.*?)</h2>', chunk, re.S)
        badge = re.search(r'<span class="pricing-card-badge">(.*?)</span>', chunk, re.S)
        line = re.search(r'<p class="section-sub">(.*?)</p>', chunk, re.S)
        assert price and line, chunk[:200]
        cards[plain(price.group(1))] = (plain(badge.group(1)) if badge else "", plain(line.group(1)))
    return cards


def test_c2_plan_prices_come_from_the_plan_labels_premium_shows():
    prices = stripe_billing.plan_prices()
    assert set(prices) == set(stripe_billing.PLANS)
    for key, price in prices.items():
        # The price out of the PLANS label /premium's cards show. The
        # checkout charges the Price ID in the environment, not this
        # label, so a Price changed in the Stripe dashboard does not move
        # the copy: PLANS has to be edited with it.
        assert stripe_billing.PLANS[key][1].startswith(price), key
        assert "/" not in price
    assert app_main.templates.env.globals["plan_prices"] == prices


def test_c2_the_spent_accounts_wall_names_both_prices_three_months_first(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    # Addresses of its own: two accounts unlocking one address on one day
    # is the pattern the /admin test asserts is absent.
    fake_report(location=fake_location(postcode="M32 6AA", outcode="M32"))
    assert _signup(client, "c2-spent@customer.test").status_code == 303
    client.get("/property?postcode=M32+6AA")
    r = client.post("/property/unlock", data={"postcode": "M32 6AA", "house_number": "14"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")

    fake_report(location=fake_location(postcode="M32 7AA", outcode="M32"))
    banner = _flat(_banner(client.get("/property?postcode=M32+7AA").text))
    prices = stripe_billing.plan_prices()
    assert f"{USED_UP}." in banner
    assert f"Opening another is {prices['quarterly']} for three months" in banner
    assert f"or {prices['monthly']} a month" in banner
    # The three-month plan is the plain answer, so it is read first.
    assert banner.index(prices["quarterly"]) < banner.index(prices["monthly"])
    # What happens next, in the terms page's own words, and still no
    # promise that a home opened on a subscription stays open.
    assert "Each renews until you cancel" in banner
    assert "renews automatically at the end of each period until you cancel" in \
        TERMS_TEMPLATE.read_text(encoding="utf-8")
    assert "stay unlocked" not in banner


def test_c2_the_returning_wall_names_both_prices_too(client, fake_report, monkeypatch):
    """The second-and-later wording is a branch of its own, and it quoted
    the monthly price on its own as well."""
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    fake_report(location=fake_location(postcode="M33 2AA", outcode="M33"))
    assert _signup(client, "c2-returner@customer.test").status_code == 303
    client.get("/property?postcode=M33+2AA")
    r = client.post("/property/unlock", data={"postcode": "M33 2AA", "house_number": "8"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")

    fake_report(location=fake_location(postcode="M33 3AA", outcode="M33"))
    # A real browser, and a gather marked warm, is what records a paywall
    # event and still renders the report (the same trick as above).
    from app.services import _cache
    _cache.set(("property_search_gather", "M33 3AA", ""), {"warm": True})
    browser = {"user-agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Safari/537.36"}
    client.get("/property?postcode=M33+3AA", headers=browser)
    banner = _flat(_banner(client.get("/property?postcode=M33+3AA", headers=browser).text))

    prices = stripe_billing.plan_prices()
    assert "This is the 2nd time you have reached this wall." in banner
    assert (f"Premium is {prices['quarterly']} for three months, made for one house hunt, "
            f"or {prices['monthly']} a month") in banner
    assert "Each renews until you cancel" in banner


def test_c2_the_report_types_no_price_the_plans_already_hold():
    template = _without_template_comments(PROPERTY_TEMPLATE.read_text(encoding="utf-8"))
    for price in stripe_billing.plan_prices().values():
        assert price not in template, f"the report types {price} out by hand"
    for typed in ("9.99", "24.99"):
        assert typed not in template, f"the report types {typed} out by hand"
    assert "{{ plan_prices.quarterly }}" in template and "{{ plan_prices.monthly }}" in template


def test_c2_before_you_pay_sits_under_the_prices_not_below_every_check(client, monkeypatch):
    _billing(monkeypatch)
    body = _fresh_premium(client)
    cards = body.index('<div class="pricing-grid">')
    questions = body.index("Before you pay")
    checks = body.index('id="all-checks"')
    assert cards < questions < checks, "the questions are still below the check cards"
    # One list, drawn once, with its structured data still beside it.
    assert body.count("Before you pay") == 1
    assert body.count('"FAQPage"') == 1
    visible, structured = _faq(body)
    assert visible == structured and len(visible) == 5

    # The one answer that states a price states the plans' price.
    prices = stripe_billing.plan_prices()
    one_house = dict(visible)["I only have one house to check. Am I stuck with a monthly bill?"]
    assert one_house.startswith(f"No. The three-month plan is {prices['quarterly']} for three months")

    # With billing off there are no prices to sit under, and the page
    # keeps its questions rather than losing them with the cards.
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    off = _fresh_premium(client)
    assert off.count("Before you pay") == 1 and off.count('"FAQPage"') == 1


def test_c2_the_three_month_card_is_badged_for_a_house_hunt_and_never_repeats_its_price(client, monkeypatch):
    _billing(monkeypatch)
    body = _fresh_premium(client)
    assert "vs monthly" not in body
    labels = {plan["key"]: plan["label"] for plan in stripe_billing.plan_choices()}
    cards = _pricing_cards(body)

    assert cards[labels["quarterly"]] == (
        "Made for one house hunt", "Billed from today, then renews every three months until you cancel.")
    assert cards[labels["monthly"]] == (
        "", "Billed from today, then renews every month until you cancel.")
    # The price is set in type directly above each line, so the line says
    # what happens next instead of saying it again.
    for heading, (_, line) in cards.items():
        assert "\u00a3" not in line, line

    # With the pass on sale the pass is the one made for a house hunt, and
    # the three-month card drops the badge rather than claiming it too.
    _billing(monkeypatch, pass_on=True)
    with_pass = _pricing_cards(_fresh_premium(client))
    assert with_pass[labels["quarterly"]][0] == ""
    assert "house hunt" in with_pass[stripe_billing.PASS_LABEL][0]


# ---- C3. The house travels through Premium and checkout ------------------
# The wall's "See plans" and the score's "+N with Premium" linked to a bare
# /premium, which opened on a hero about houses in general; checkout named
# no home in its success_url, and /premium/success said "Back to search".
# A returning buyer, the only kind that has ever paid, had to find the
# house again at every step.

C3_HOME = "M35 1AA"  # the locked home a buyer is weighing up


def _home_block(body):
    """The block above the hero naming the home, or "" if there is none."""
    if '<div class="premium-home">' not in body:
        return ""
    return body.split('<div class="premium-home">', 1)[1].split("<h1", 1)[0]


def _wall_cta(body):
    """The href of the wall's "See plans", as a browser would read it."""
    m = re.search(r'<a class="paywall-banner-cta" href="([^"]*)"', _banner(body))
    assert m, "the wall's call to action is missing"
    return html.unescape(m.group(1))


def _spend_the_free_report(client, email, postcode, house_number):
    """A signed-in account with its free full report spent on a home of
    its own: the one state that is offered Premium at all."""
    assert _signup(client, email).status_code == 303
    r = client.post("/property/unlock", data={"postcode": postcode, "house_number": house_number},
                    follow_redirects=False)
    assert r.status_code == 303


def test_c3_the_wall_and_the_score_carry_the_home_to_premium(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    _spend_the_free_report(client, "c3-wall@customer.test", "M35 3AA", "9")

    fake_report(location=fake_location(postcode=C3_HOME, outcode="M35"))
    body = client.get("/property?postcode=M35+1AA&house_number=7").text
    assert USED_UP in body
    assert _wall_cta(body) == "/premium?home=M35+1AA&hn=7"
    # The score's upsell goes where the wall goes, as it has since A1.
    assert html.unescape(_upsell(body)[0]) == _wall_cta(body)

    # A report without a house number carries the postcode alone.
    fake_report(location=fake_location(postcode=C3_HOME, outcode="M35"))
    assert _wall_cta(client.get("/property?postcode=M35+1AA").text) == "/premium?home=M35+1AA"


def test_c3_premium_opens_on_the_home_for_a_signed_in_free_account(client, monkeypatch):
    _billing(monkeypatch)
    with_home = "/premium?home=M35+1AA&hn=7"

    # Signed out there is a free full report to spend first, so this page
    # is not asking that visitor to pay for one house.
    assert _home_block(client.get(with_home).text) == ""

    _spend_the_free_report(client, "c3-premium@customer.test", "M35 4AA", "10")
    block = _home_block(client.get(with_home).text)
    assert "Open 7 M35 1AA in full" in block

    # Both plans, the three-month one first, each posting the home along.
    forms = re.findall(r'<form action="/premium/checkout".*?</form>', block, re.S)
    assert len(forms) == 2
    assert [re.search(r'name="plan" value="([^"]*)"', f).group(1) for f in forms] == ["quarterly", "monthly"]
    prices = stripe_billing.plan_prices()
    labels = [re.search(r'<button type="submit">(.*?)</button>', f).group(1) for f in forms]
    assert labels == [prices["quarterly"] + " for three months", prices["monthly"] + " a month"]
    for form in forms:
        assert '<input type="hidden" name="home" value="M35 1AA">' in form
        assert '<input type="hidden" name="hn" value="7">' in form

    # And it says where the free report went, so nobody is sold what they
    # already have. The same row the wall's own aside reads.
    assert ('Your free full report is on '
            '<a href="/property?postcode=M35+4AA&amp;house_number=10">10 M35 4AA</a> '
            'and stays open.') in " ".join(block.split())

    # A subscriber has nothing to buy, on this home or any other.
    with db.get_session() as session:
        auth.find_user_by_email(session, "c3-premium@customer.test").is_premium = True
        session.commit()
    assert _home_block(client.get(with_home).text) == ""


def test_c3_premium_ignores_a_home_that_is_not_a_postcode(client, monkeypatch):
    _billing(monkeypatch)
    _spend_the_free_report(client, "c3-junk@customer.test", "M35 5AA", "11")

    for junk in ("javascript:alert(1)", "<script>alert(1)</script>", "M35", "not a postcode"):
        body = client.get("/premium", params={"home": junk, "hn": "7"}).text
        assert _home_block(body) == "", junk
        assert junk not in body and html.escape(junk) not in body, junk

    # A real postcode with a house number that is not one keeps the
    # postcode and drops the rest, rather than putting it on the page.
    block = _home_block(client.get("/premium", params={"home": "m351aa", "hn": "<b>x</b>"}).text)
    assert "Open M35 1AA in full" in block
    assert '<input type="hidden" name="hn" value="">' in block
    assert "<b>x</b>" not in block and "&lt;b&gt;" not in block


def test_c3_checkout_sends_stripe_a_success_url_carrying_the_home(client, monkeypatch):
    _billing(monkeypatch)
    _spend_the_free_report(client, "c3-checkout@customer.test", "M35 6AA", "12")
    seen = {}

    async def _fake_session(**kwargs):
        seen.update(kwargs)
        return "https://checkout.stripe.test/c/pay/abc"

    monkeypatch.setattr(stripe_billing, "create_checkout_session", _fake_session)

    r = client.post("/premium/checkout", data={"plan": "quarterly", "home": "m351aa", "hn": "7"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "https://checkout.stripe.test/c/pay/abc"
    assert seen["plan"] == "quarterly"
    assert seen["success_url"].endswith("/premium/success?home=M35+1AA&hn=7")
    assert seen["cancel_url"].endswith("/premium/cancel")

    # No home, or one that is not a postcode: the page checkout comes back
    # to is the one it was before today.
    for data in ({"plan": "monthly"}, {"plan": "monthly", "home": "nonsense", "hn": "7"}):
        seen.clear()
        assert client.post("/premium/checkout", data=data, follow_redirects=False).status_code == 303
        assert seen["success_url"].endswith("/premium/success")


def test_c3_stripe_is_given_that_success_url_unchanged(monkeypatch):
    """The last link in the chain: what create_checkout_session actually
    posts to Stripe. Nothing here reaches the network."""
    _billing(monkeypatch)
    sent = {}

    class _Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"url": "https://checkout.stripe.test/c/pay/abc"}

    class _FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url, data=None, auth=None):
            sent.update({"url": url, "data": data})
            return _Response()

    monkeypatch.setattr(stripe_billing.httpx, "AsyncClient", _FakeClient)
    success_url = "https://ukpropertyinsight.co.uk/premium/success?home=M35+1AA&hn=7"
    url = asyncio.run(stripe_billing.create_checkout_session(
        plan="quarterly", user_id=1, user_email="c3-stripe@customer.test",
        success_url=success_url, cancel_url="https://ukpropertyinsight.co.uk/premium/cancel",
    ))
    assert url == "https://checkout.stripe.test/c/pay/abc"
    assert sent["url"].endswith("/checkout/sessions")
    assert sent["data"]["success_url"] == success_url


def test_c3_the_success_page_opens_the_report_it_was_bought_for(client):
    body = client.get("/premium/success", params={"home": "m351aa", "hn": "7"}).text
    assert "Premium is on. Every check on 7 M35 1AA is open." in " ".join(body.split())
    assert '<a href="/property?postcode=M35+1AA&amp;house_number=7">Open the full report on 7 M35 1AA</a>' in body
    assert '<a href="/watchlist">My properties</a>' in body
    assert "Back to search" not in body

    # No home, or one that is not a postcode: today's page, unchanged.
    for params in ({}, {"home": "javascript:alert(1)"}):
        plain = client.get("/premium/success", params=params).text
        assert "Back to search" in plain and "is open." not in plain
        assert "javascript:alert(1)" not in plain


# ---- C4. How many of the locked checks found something, never which ------
# Beside the score the report said "+N more checks factored in with
# Premium", which never says those checks found anything, and the banner
# under it could show a green tick and "No major red flags found" while a
# locked check had a finding waiting behind the lock. Owner's decision 6:
# say how many of the locked checks found something worth checking on this
# home, and never which. The count is the score's own premium_extra_checks,
# the words are locked_found_sentence in main.py so the score and the
# banner cannot say different things on one screen, and the check that
# raised it stays where B2 and B3 above put it.

# A finding behind a lock, with a name of its own that must not reach the
# page while that lock holds.
C4_FLAGGED_LANDFILL = {"status": "on_site", "site_name": "Former Brickworks Tip", "distance_m": 0}

# The locked checks, in the words the report would use if they were open.
# None of them may appear beside the count.
C4_LOCKED_WORDS = ("landfill", "brickworks", "contamination", "air quality",
                   "mining", "coal", "subsidence", "sewage", "extension")


def _c4_gather(**overrides):
    """A5's report, where every check open to the reader came back clear,
    with a locked check flagged. The score is computed from that gather
    rather than written into the fake, so the count on the page is the one
    the real service produces from a real finding."""
    from app.services import overview_score
    gather = _all_read(**{"historic_landfill": C4_FLAGGED_LANDFILL, **overrides})
    gather["overview"] = overview_score.compute(gather, premium_unlocked=False)
    return gather


def test_c4_a_flagged_locked_check_is_counted_beside_the_score_and_never_named(client, fake_report):
    gather = _c4_gather()
    assert gather["overview"]["premium_extra_checks"] == 1
    assert gather["overview"]["concerns"] == []  # and the verdict still says nothing of it
    fake_report(location=fake_location(postcode="M32 1AA", outcode="M32"), gather=gather)
    body = client.get("/property?postcode=M32+1AA").text

    # The score's line, word for word. Only the total is read from the
    # constant: the sentence itself is the one the owner approved.
    href, words = _upsell(body)
    assert words == (f"1 of the {len(app_main.PREMIUM_CHECKS)} locked checks found something "
                     "worth checking on this home. Sign up free to open them →")
    assert href == "/signup?next=/property%3Fpostcode%3DM32%201AA"

    # The banner is not an all-clear for the home: no green, no tick, and
    # what it vouches for is only the checks this reader can open.
    classes, banner = _a5_banner(body)
    assert "attention-banner-clear" not in classes and "attention-banner-unread" in classes
    assert "✓" not in banner and "No major red flags found" not in banner
    assert banner.startswith("1 No major red flags in the checks open to you across ")
    assert banner.endswith(app_main.locked_found_sentence(1) + ".")

    # Neither place names or hints at the check, and the finding itself is
    # not on the page at all (B3 above).
    for word in C4_LOCKED_WORDS:
        assert word not in banner.lower(), word
        assert word not in words.lower(), word
    assert "Former Brickworks Tip" not in body and "On a former landfill" not in body


def test_c4_with_nothing_flagged_behind_the_lock_neither_the_count_nor_the_mention_appears(client, fake_report):
    gather = _c4_gather(historic_landfill={"status": "clear"})
    assert gather["overview"]["premium_extra_checks"] == 0
    fake_report(location=fake_location(postcode="M32 1AA", outcode="M32"), gather=gather)
    body = client.get("/property?postcode=M32+1AA").text

    assert 'class="overview-score-upsell"' not in body
    assert "found something worth checking on this home" not in body
    # A5's all-clear, to the word, for a report with nothing behind the lock.
    classes, banner = _a5_banner(body)
    assert "attention-banner-clear" in classes
    assert banner.startswith("✓ No major red flags found across ")
    assert banner.endswith(LOCKED_SENTENCE)


def test_c4_one_count_in_both_places_routed_by_state_and_gone_once_the_home_opens(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    # Two locked checks with a finding, so the count is read as a count.
    gather = _c4_gather(coal_mining={"present": True})
    assert gather["overview"]["premium_extra_checks"] == 2
    sentence = app_main.locked_found_sentence(2)
    fake_report(location=fake_location(postcode="M32 2AA", outcode="M32"), gather=gather)

    # Signed out: sign up, carrying the house.
    body = client.get("/property?postcode=M32+2AA").text
    href, words = _upsell(body)
    assert href == "/signup?next=/property%3Fpostcode%3DM32%202AA"
    assert words == sentence + ". Sign up free to open them →"
    assert _a5_banner(body)[1].endswith(sentence + ".")

    # A free account with its free full report unused: the offer at the top.
    assert _signup(client, "c4-states@customer.test").status_code == 303
    body = client.get("/property?postcode=M32+2AA").text
    href, words = _upsell(body)
    assert href == "#use-free-report"
    assert words == sentence + ". Open them with your free full report →"
    assert _a5_banner(body)[1].endswith(sentence + ".")

    # The same account after spending that report on a home of its own:
    # Premium, opening on the house it was read from (C3 above).
    fake_report(location=fake_location(postcode="M32 9AA", outcode="M32"), gather=gather)
    r = client.post("/property/unlock", data={"postcode": "M32 9AA", "house_number": "4"},
                    follow_redirects=False)
    assert r.status_code == 303
    fake_report(location=fake_location(postcode="M32 2AA", outcode="M32"), gather=gather)
    body = client.get("/property?postcode=M32+2AA").text
    assert USED_UP in body
    href, words = _upsell(body)
    assert href == "/premium?home=M32+2AA"
    assert words == sentence + ". Open them with Premium →"
    assert _a5_banner(body)[1].endswith(sentence + ".")

    # On the home this account did open there is nothing to count, and the
    # findings are named, because they are in front of the reader now.
    fake_report(location=fake_location(postcode="M32 9AA", outcode="M32"), gather=gather)
    body = client.get("/property?postcode=M32+9AA&house_number=4").text
    assert 'class="overview-score-upsell"' not in body
    assert "found something worth checking on this home" not in body
    banner = _a5_banner(body)[1]
    assert "locked" not in banner
    assert "Historic landfill on/near site" in banner and "In a Coal Mining Reporting Area" in banner


# ---- C5. My properties says which homes are open in full -----------------
# Change alerts lead to My properties, which listed every saved home with
# its notes and changes and never said whether it could be read in full.
# Each card now says "Open in full" or how many checks are locked, counted
# from PREMIUM_CHECKS. Once two or more are closed, one line above the
# compare bar gives both prices from plan_prices and carries the first
# closed home to /premium (C2 and C3 above). With the free full report
# unspent the line offers that report instead, and a subscriber, whose
# every home is open, reads neither a locked label nor an offer.

WATCHLIST_TEMPLATE = ROOT / "app" / "templates" / "watchlist.html"
C5_LOCKED_LABEL = f'<p class="myprops-access">{len(app_main.PREMIUM_CHECKS)} checks locked</p>'
C5_OPEN_LABEL = '<p class="myprops-access myprops-access-open">Open in full</p>'


def _c5_quiet(monkeypatch):
    """My properties diffs every saved home against a fresh summary. None
    of that is under test here, and none of it may reach the network."""
    async def _summary(postcode, house_number):
        return {"postcode": postcode}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    monkeypatch.setattr(email_service, "can_verify", lambda: False)


def _c5_account(client, email, saved, opened=None):
    """A signed-in account that spent its free full report on `opened`, if
    given, and saved each (postcode, house number) in `saved`."""
    from app import watchlist
    assert _signup(client, email).status_code == 303
    if opened:
        r = client.post("/property/unlock", data={"postcode": opened[0], "house_number": opened[1]},
                        follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("&unlocked=1")
    with db.get_session() as session:
        uid = auth.find_user_by_email(session, email).id
    for postcode, house_number in saved:
        watchlist.save_item(uid, postcode, house_number, "")
    return uid


def _c5_cards(body):
    """[(address, access line)] for each saved home, in page order."""
    cards = re.split(r'<div class="myprops-card(?: myprops-card-changed)?">', body)[1:]
    out = []
    for card in cards:
        address = _flat(re.search(r'class="myprops-address"[^>]*>(.*?)</a>', card, re.S).group(1))
        access = re.search(r'<p class="myprops-access[^"]*">(.*?)</p>', card, re.S)
        out.append((address, access.group(1) if access else None))
    return out


def _c5_offer(body):
    """The offer line's words and its link, or ("", None) when there is none."""
    found = re.findall(r'<p class="myprops-offer">(.*?)</p>', body, re.S)
    assert len(found) <= 1, "My properties states the offer more than once"
    if not found:
        return "", None
    link = re.search(r'<a href="([^"]*)">', found[0])
    return _flat(re.sub(r"<[^>]+>", "", found[0])), html.unescape(link.group(1)) if link else None


def test_c5_one_home_open_two_locked_by_the_constant_and_one_offer(client, monkeypatch):
    _c5_quiet(monkeypatch)
    uid = _c5_account(client, "c5-mixed@customer.test", opened=("M36 1AA", "3"),
                      saved=[("M36 1AA", "3"), ("M36 2AA", "5"), ("M36 3AA", "")])
    locked = f"{len(app_main.PREMIUM_CHECKS)} checks locked"

    body = client.get("/watchlist").text
    cards = _c5_cards(body)
    assert sorted(cards) == sorted([("3, M36 1AA", "Open in full"),
                                    ("5, M36 2AA", locked), ("M36 3AA", locked)])
    assert body.count(C5_OPEN_LABEL) == 1 and body.count(C5_LOCKED_LABEL) == 2

    # One line, above the compare bar, naming how many homes are closed and
    # both prices, three months first, as the wall does.
    words, link = _c5_offer(body)
    prices = stripe_billing.plan_prices()
    assert words == (f"2 of your saved homes have {locked}. Premium opens every check on all of "
                     f"them: {prices['quarterly']} for three months, made for one house hunt, or "
                     f"{prices['monthly']} a month. Each renews until you cancel. See plans")
    assert body.index('<p class="myprops-offer">') < body.index('class="myprops-compare-bar"')

    # The link carries the first closed home on the page, in C3's words.
    first_closed = next(address for address, access in cards if access == locked)
    assert link == {"5, M36 2AA": "/premium?home=M36+2AA&hn=5",
                    "M36 3AA": "/premium?home=M36+3AA"}[first_closed]

    # One closed home left: its label stays, the offer goes.
    from app import watchlist
    closed_ids = [i["id"] for i in watchlist.list_items(uid) if i["postcode"] != "M36 1AA"]
    assert client.post("/watchlist/remove", data={"item_id": closed_ids[0]},
                       follow_redirects=False).status_code == 303
    body = client.get("/watchlist").text
    assert body.count(C5_OPEN_LABEL) == 1 and body.count(C5_LOCKED_LABEL) == 1
    assert _c5_offer(body) == ("", None)


def test_c5_a_subscriber_reads_no_locked_label_and_no_offer(client, monkeypatch):
    _c5_quiet(monkeypatch)
    _c5_account(client, "c5-subscriber@customer.test",
                saved=[("M36 5AA", "1"), ("M36 5AA", "2"), ("M36 6AA", "")])
    with db.get_session() as session:
        auth.find_user_by_email(session, "c5-subscriber@customer.test").is_premium = True
        session.commit()

    body = client.get("/watchlist").text
    assert [access for _, access in _c5_cards(body)] == ["Open in full"] * 3
    assert "checks locked" not in body
    assert 'class="myprops-offer"' not in body and "/premium?home=" not in body


def test_c5_with_the_free_report_unspent_the_offer_is_that_report(client, monkeypatch):
    """A1's rule on the report holds here too: with an unlock left, the one
    offer is the free full report, and no price is put beside it."""
    _c5_quiet(monkeypatch)
    _c5_account(client, "c5-unspent@customer.test", saved=[("M36 7AA", "4"), ("M36 8AA", "6")])

    body = client.get("/watchlist").text
    assert body.count(C5_LOCKED_LABEL) == 2 and C5_OPEN_LABEL not in body
    words, link = _c5_offer(body)
    assert words == (f"2 of your saved homes have {len(app_main.PREMIUM_CHECKS)} checks locked. "
                     "Your free full report opens every check on one of them, with no card: "
                     "use it from that home's report.")
    assert link is None
    for price in stripe_billing.plan_prices().values():
        assert price not in words


def test_c5_a_postcode_saved_as_typed_is_open_when_its_report_is(client, monkeypatch):
    """My properties keeps a postcode as it was typed, while the report
    looks it up and records the unlock against postcodes.io's spacing."""
    _c5_quiet(monkeypatch)
    _c5_account(client, "c5-typed@customer.test", opened=("M36 9AA", "8"),
                saved=[("m369aa", "8"), ("M36 9AB", "8")])
    cards = dict(_c5_cards(client.get("/watchlist").text))
    assert cards == {"8, m369aa": "Open in full",
                     "8, M36 9AB": f"{len(app_main.PREMIUM_CHECKS)} checks locked"}


def test_c5_my_properties_costs_no_statement_per_home(client, monkeypatch):
    """Neon round trips are the unit of page cost: the labels are read from
    the unlock rows the page already fetches, never one lookup per card,
    and the snapshots are written once for the list and only where they
    moved (batch C fix pass). Every statement is counted, not only the
    unlock ones: a count of those alone passed while the page wrote one
    snapshot per home."""
    from sqlalchemy import event
    from app import watchlist
    _c5_quiet(monkeypatch)
    uid = _c5_account(client, "c5-rounds@customer.test", opened=("M35 8AA", "2"),
                      saved=[("M35 8AA", "2"), ("M35 8AB", "2")])

    def _no_per_home_lookup(*_a, **_k):
        raise AssertionError("My properties asked about one home at a time")

    monkeypatch.setattr(auth, "has_unlocked", _no_per_home_lookup)
    engine = db._get_engine()

    def _statements():
        seen = []

        def _count(_conn, _cursor, statement, *_rest):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", _count)
        try:
            body = client.get("/watchlist").text
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        return body, seen

    # The first visit writes both homes' first snapshots, in one statement.
    body, two_homes = _statements()
    assert body.count(C5_OPEN_LABEL) == 1 and body.count(C5_LOCKED_LABEL) == 1
    assert sum(1 for s in two_homes if s.lstrip().upper().startswith("UPDATE")) == 1
    # Four more homes: the four new snapshots are one statement, and the
    # two that did not move are not written again.
    for n in range(3, 7):
        watchlist.save_item(uid, "M35 8AC", str(n), "")
    body, six_homes = _statements()
    assert body.count(C5_LOCKED_LABEL) == 5
    assert len(six_homes) == len(two_homes), (two_homes, six_homes)
    assert sum(1 for s in six_homes if "premium_unlocks" in s) == sum(1 for s in two_homes if "premium_unlocks" in s)
    # Nothing moved since: nothing is written at all.
    _body, again = _statements()
    assert not any(s.lstrip().upper().startswith("UPDATE") for s in again)
    assert len(again) == len(two_homes) - 1
    # And what was written is each home's own snapshot, under this account.
    for item in watchlist.list_items(uid):
        assert json.loads(item["last_snapshot"]) == {"postcode": item["postcode"]}


def test_c5_the_count_and_the_prices_are_never_typed_into_the_page():
    source = _without_template_comments(WATCHLIST_TEMPLATE.read_text(encoding="utf-8"))
    assert "{{ locked_check_count }} checks locked" in source
    assert "plan_prices.quarterly" in source and "plan_prices.monthly" in source
    assert "£" not in source and not re.search(r"\d+ checks", source)


# ---- C6. Change alerts lead with what matters and leave crime out ---------
# One comparison feeds the report's "Since you last looked", the My
# properties chips and the change alert emails. It flagged recorded crime
# whenever the latest month differed from the last snapshot by 5 or more,
# without naming the months, which for a busy postcode is most months, so
# an email could carry nothing else. Crime now stays on the page, last,
# and names the two months it compares once the snapshots hold them; an
# email never carries it, never goes out for it alone, and lists what it
# does carry in the order a buyer reads it: new sales, a new energy
# certificate, a flood zone change, then the price trend and the average.
# The job still runs when it did: nothing here sends on a schedule.

C6_BEFORE = {
    "tx_count": 3, "avg_price": 250000, "epc_date": "2024-01-01",
    "flood_zone": "Zone 1 (low probability)", "price_growth_pct": 2.0,
    "crime_total": 40, "crime_month": "2026-06",
}
C6_AFTER_EVERYTHING = {
    "tx_count": 4, "avg_price": 262500, "epc_date": "2026-08-01",
    "flood_zone": "Zone 2 (medium probability)", "price_growth_pct": -1.5,
    "crime_total": 60, "crime_month": "2026-07",
}
C6_ORDER = [
    "1 new sold price recorded here since you last looked",
    "A new energy certificate was lodged, often a sign the property is being prepared for sale",
    "Flood zone changed from Zone 1 (low probability) to Zone 2 (medium probability)",
    "Area house-price trend flipped: growth turned negative (-1.5% YoY)",
    "Average sold price changed from £250,000 to £262,500",
]
C6_CRIME_LINE = "Recorded crime nearby up by 20 in July 2026 compared with June 2026"


def _c6_run(client, monkeypatch, email, postcode, house_number, before, after):
    """Save one home holding `before` as its snapshot, run the alert job
    with `after` as that home's fresh summary, and return what was sent
    to this account and the snapshot the job left behind. Every other
    saved home in the shared database comes back with nothing to compare,
    so the run's figures are this home's alone."""
    from app import watchlist
    monkeypatch.setenv("ALERTS_CRON_SECRET", "c6-secret")
    monkeypatch.setattr(email_service, "is_configured", lambda: True)
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    sent = []

    async def _send(to, subject, body):
        sent.append({"to": to, "subject": subject, "html": body})
        return True

    monkeypatch.setattr(email_service, "send_email", _send)

    async def _summary(pc, hn):
        if (pc, hn) == (postcode, house_number):
            return {"postcode": pc, "house_number": hn, **after}
        return {"postcode": pc}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary)

    assert _signup(client, email).status_code == 303
    with db.get_session() as session:
        uid = auth.find_user_by_email(session, email).id
    watchlist.save_item(uid, postcode, house_number, "")
    item = next(i for i in watchlist.list_items(uid) if i["postcode"] == postcode)
    watchlist.update_snapshot(uid, item["id"], json.dumps(
        {"postcode": postcode, "house_number": house_number, **before}))

    r = client.post("/internal/run-watchlist-alerts", headers={"x-alerts-secret": "c6-secret"})
    assert r.status_code == 200
    mine = [m for m in sent if m["to"] == email]
    left = next(i for i in watchlist.list_items(uid) if i["postcode"] == postcode)
    return mine, sent, json.loads(left["last_snapshot"])


def _c6_listed(email_html):
    return [html.unescape(li) for li in re.findall(r"<li[^>]*>(.*?)</li>", email_html, re.S)]


def test_c6_the_page_reads_changes_in_order_with_crime_last_and_its_months_named():
    changes = app_main._snapshot_changes(C6_BEFORE, C6_AFTER_EVERYTHING)
    assert changes == C6_ORDER + [C6_CRIME_LINE]
    # The return visit opens on the first line, which is now the sale.
    assert app_main._group_for_changes(changes) == "cat-value-market"


def test_c6_the_crime_line_keeps_its_old_words_when_the_snapshot_has_no_month():
    """Snapshots written before 17 Sep 2026 carry no month, and a line
    must not name months nobody recorded."""
    old = {"crime_total": 40}
    assert app_main._snapshot_changes(old, {"crime_total": 52, "crime_month": "2026-07"}) == [
        "Recorded crime nearby up by 12 since last checked"]
    # The same month republished with a different count says which month.
    assert app_main._snapshot_changes({"crime_total": 40, "crime_month": "2026-07"},
                                      {"crime_total": 33, "crime_month": "2026-07"}) == [
        "Recorded crime nearby for July 2026 down by 7 since last checked"]
    # And under 5 is still not a change.
    assert app_main._snapshot_changes(C6_BEFORE, {**C6_BEFORE, "crime_total": 44,
                                                  "crime_month": "2026-07"}) == []


def test_c6_the_email_list_is_the_page_list_without_crime():
    assert app_main._alert_changes(C6_BEFORE, C6_AFTER_EVERYTHING) == C6_ORDER
    only_crime = {**C6_BEFORE, "crime_total": 90, "crime_month": "2026-07"}
    assert app_main._snapshot_changes(C6_BEFORE, only_crime)
    assert app_main._alert_changes(C6_BEFORE, only_crime) == []
    assert "crime" not in app_main.ALERT_CHANGE_KINDS


def test_c6_every_trigger_the_page_names_still_sends_an_email():
    """C1 names four triggers beside a saved home; the job now sends on
    _alert_changes, so each must still fire there, not only on the page.
    The sale is built through _filter_by_address, as both summaries build
    it (fix pass, 17 Sep 2026), for a home with a house number and one
    without."""
    for house_number, seller in (("9", "9"), ("", "31")):
        before, moved = _c1_moved(house_number, change_for=seller)
        assert list(moved) == list(app_main.alert_triggers(house_number))
        for trigger, change in moved.items():
            assert app_main._alert_changes(before, {**before, **change}), \
                f"the alert job no longer emails on {trigger!r}"


def test_c6_a_run_where_only_crime_moved_sends_no_email(client, monkeypatch):
    # The area trend moves too, by less than a change of direction, so the
    # snapshot shows the job really did check this home and write it.
    only_crime = {**C6_BEFORE, "crime_total": 60, "crime_month": "2026-07", "price_growth_pct": 2.4}
    mine, sent, left = _c6_run(client, monkeypatch, "c6-crime-only@customer.test",
                               "M38 1AA", "7", C6_BEFORE, only_crime)
    assert mine == [], "an email went out with crime as its only change"
    assert sent == []
    run = app_main._alert_runs()[0]
    assert run["homes_changed"] == 0 and run["users_with_changes"] == 0 and run["emails_sent"] == 0

    # The job did not spend the crime change: the snapshot keeps crime as
    # the reader last saw it, and moves everything else on.
    assert left["price_growth_pct"] == 2.4
    assert left["crime_total"] == 40 and left["crime_month"] == "2026-06"

    # So My properties still shows it, naming both months.
    body = client.get("/watchlist").text
    assert C6_CRIME_LINE in html.unescape(body)
    assert "myprops-card myprops-card-changed" in body
    # And once the reader has seen it, it is not shown again.
    assert C6_CRIME_LINE not in html.unescape(client.get("/watchlist").text)


def test_c6_an_email_with_several_changes_lists_them_in_the_new_order(client, monkeypatch):
    mine, _sent, left = _c6_run(client, monkeypatch, "c6-ordered@customer.test",
                                "M38 2AA", "9", C6_BEFORE, C6_AFTER_EVERYTHING)
    assert len(mine) == 1
    assert _c6_listed(mine[0]["html"]) == C6_ORDER
    assert "crime" not in mine[0]["html"].lower()
    assert mine[0]["subject"] == "Changes on 1 property you follow"
    run = app_main._alert_runs()[0]
    assert run["homes_changed"] == 1 and run["emails_sent"] == 1
    # Everything the email carried is consumed; crime waits for the page.
    assert left["tx_count"] == 4 and left["flood_zone"].startswith("Zone 2")
    assert left["crime_total"] == 40 and left["crime_month"] == "2026-06"


def test_c6_a_single_change_email_leads_its_subject_with_that_change(client, monkeypatch):
    """A sale beside a crime move is a one-change email: the subject names
    the sale, and the crime is on the page, not in the inbox."""
    sale_and_crime = {**C6_BEFORE, "tx_count": 5, "crime_total": 70, "crime_month": "2026-07"}
    mine, _sent, _left = _c6_run(client, monkeypatch, "c6-sale@customer.test",
                                 "M38 3AA", "11", C6_BEFORE, sale_and_crime)
    assert len(mine) == 1
    assert mine[0]["subject"] == "M38 3AA, 11: 2 new sold prices recorded here since you last looked"
    assert _c6_listed(mine[0]["html"]) == ["2 new sold prices recorded here since you last looked"]
    assert "never on a schedule" in mine[0]["html"]


def test_c6_both_snapshots_record_the_crime_month(monkeypatch):
    """The report writes one snapshot and My properties and the alert job
    write the other; both must carry the month for the line to name it."""
    from app.services import area_stats, crime, flood_zones, hpi, schools_db
    from tests.conftest import fake_gather
    summary = app_main._summary_from_report(fake_gather(), "M14 5TG", "")
    assert summary["crime_total"] == 120 and summary["crime_month"] == "2026-06"

    async def _location(_pc):
        return fake_location(postcode="M38 4AA", outcode="M38")

    async def _down(*_a, **_k):
        raise RuntimeError("not under test")

    def _down_sync(*_a, **_k):
        raise RuntimeError("not under test")

    async def _crime(_lat, _lon):
        return {"total": 57, "month": "2026-07", "by_category": []}

    monkeypatch.setattr(app_main, "lookup_postcode", _location)
    monkeypatch.setattr(app_main, "sold_prices_for_postcode", _down)
    monkeypatch.setattr(app_main, "_epc_flow", _down)
    monkeypatch.setattr(flood_zones, "zone_for", _down)
    monkeypatch.setattr(hpi, "area_comparison", _down)
    monkeypatch.setattr(area_stats, "deprivation_for_lsoa", _down_sync)
    monkeypatch.setattr(schools_db, "school_landscape", _down_sync)
    monkeypatch.setattr(crime, "summary_near", _crime)
    light = asyncio.run(app_main._comparison_summary("M38 4AA", "c6"))
    assert light["crime_total"] == 57 and light["crime_month"] == "2026-07"


# ---- Batch C fix pass: what the review of C1 to C6 found left over -------
# The saved line promised an email when "a new sale is recorded at this
# postcode", but a home saved with a house number counts only its own
# sales (_filter_by_address), so a neighbour's sale never sent anything.
# It told an account waiting on confirmation "We email <address>" while
# the job skips that address. My properties still promised an email "if
# anything on this list changes", above a crime move the job never
# emails. The rest is below, one test per finding.

def test_c1_a_neighbours_sale_is_not_promised_on_a_house_numbered_home():
    """The mismatch this pins: house 9 saved, the neighbour at 14 sells.
    The job sends nothing, so the page must not say it would."""
    before = _c1_sale_summary(C1_STREET, "9")
    after = _c1_sale_summary(C1_STREET + _c1_sales("14 ACACIA AVENUE"), "9")
    assert app_main._alert_changes(before, after) == []
    assert all("postcode" not in trigger for trigger in app_main.alert_triggers("9"))
    assert "postcode" not in app_main.alert_triggers_short("9")
    # The same sale at a postcode-only home is one it is told about.
    before = _c1_sale_summary(C1_STREET, "")
    after = _c1_sale_summary(C1_STREET + _c1_sales("14 ACACIA AVENUE"), "")
    assert app_main._alert_changes(before, after)
    # Whitespace is no house number: _filter_by_address keeps every sale.
    assert app_main.alert_triggers("  ")[0] == "a new sale is recorded at this postcode"


def test_c1_a_house_numbered_report_and_a_postcode_report_word_the_sale_apart(client, fake_report, monkeypatch):
    """Rendered, signed in and out, for a home with a house number and a
    home without one."""
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    anon_house = _flat(_c1_open(client, fake_report, "M18 6AA", "M18", "24"))
    assert "be told when a sale of this home is recorded, a new energy" in anon_house
    assert "sale is recorded at this postcode" not in anon_house
    anon_postcode = _flat(_c1_open(client, fake_report, "M18 6AB", "M18", ""))
    assert "be told when a sale is recorded at this postcode, a new energy" in anon_postcode

    email = "c1-wording@customer.test"
    assert _signup(client, email).status_code == 303
    house = _flat(_c1_open(client, fake_report, "M18 6AC", "M18", "26"))
    saved = house.split(SAVED_NOTE, 1)[1].split("</p>", 1)[0]
    assert f"We email {email} when a sale of this home is recorded; a new energy" in saved
    assert "sale is recorded at this postcode" not in saved
    postcode = _flat(_c1_open(client, fake_report, "M18 6AD", "M18", ""))
    saved = postcode.split(SAVED_NOTE, 1)[1].split("</p>", 1)[0]
    assert f"We email {email} when a new sale is recorded at this postcode; a new energy" in saved


def test_c1_an_unconfirmed_account_is_not_told_we_email_it(client, fake_report, monkeypatch):
    """With confirmation switched on, the alert job skips an address
    nobody has confirmed (_email_can_receive), so the saved line and the
    end-of-report line say the emails start once it is confirmed."""
    _live(monkeypatch)
    email = "c1-unconfirmed@customer.test"
    assert _signup(client, email).status_code == 303
    assert app_main._email_can_receive(email) is False
    body = _flat(_c1_open(client, fake_report, "M18 6AE", "M18", "28"))
    saved = body.split(SAVED_NOTE, 1)[1].split("</p>", 1)[0]
    assert f"Once you confirm {email}, we email you when a sale of this home is recorded" in saved
    assert f"We email {email}" not in body
    ending = body.split('id="cat-complete"', 1)[1].split("</div>", 1)[0]
    assert "so once you confirm your email address you will be told when" in ending

    # Confirmed, the line names the address it sends to.
    with db.get_session() as session:
        auth.find_user_by_email(session, email).email_verified_at = datetime.datetime.now(datetime.timezone.utc)
        session.commit()
    assert app_main._email_can_receive(email) is True
    body = _flat(_c1_open(client, fake_report, "M18 6AE", "M18", "28"))
    assert f"We email {email} when a sale of this home is recorded" in body
    ending = body.split('id="cat-complete"', 1)[1].split("</div>", 1)[0]
    assert "so you will be told when" in ending


def test_c6_my_properties_names_the_triggers_and_not_any_change(client, monkeypatch):
    """The line at the top of My properties said "We email you if anything
    on this list changes", above a crime move it highlights and never
    emails. It names what the job sends on now."""
    from app import watchlist
    monkeypatch.setattr(email_service, "is_configured", lambda: True)
    monkeypatch.setattr(email_service, "can_verify", lambda: False)

    async def _summary(pc, hn):
        return {"postcode": pc, "house_number": hn}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    email = "c6-list-line@customer.test"
    assert _signup(client, email).status_code == 303
    with db.get_session() as session:
        uid = auth.find_user_by_email(session, email).id
    watchlist.save_item(uid, "M18 6AF", "30", "")
    body = _flat(client.get("/watchlist").text)
    assert ("We email you when one of these happens to a home on this list: "
            + app_main.ALERT_TRIGGERS_LIST + ". Never on a schedule.") in body
    assert "if anything on this list changes" not in body
    assert "No need to keep checking back" not in body

    # Waiting on confirmation, the job skips the address, so it says so.
    _live(monkeypatch)
    email = "c6-list-line-unconfirmed@customer.test"
    assert _signup(client, email).status_code == 303
    with db.get_session() as session:
        uid = auth.find_user_by_email(session, email).id
    watchlist.save_item(uid, "M18 6AG", "32", "")
    body = _flat(client.get("/watchlist").text)
    assert "Once you confirm your email address, we email you when one of these happens" in body


def test_c3_premium_offers_an_unspent_free_report_and_no_price(client, monkeypatch):
    """The home block sold two plans to every account without a
    subscription, so an account that had never used its free full report,
    the one the locked PDF sends here, was offered a price for a home it
    could open with no card. While the free report is unspent it is the
    only offer, pointed at the report's own unlock (or the confirmation
    banner, when the address is not confirmed yet)."""
    _billing(monkeypatch)
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    with_home = "/premium?home=M35+1AA&hn=7"

    assert _signup(client, "c3-unspent@customer.test").status_code == 303
    block = " ".join(_home_block(client.get(with_home).text).split())
    assert "Open 7 M35 1AA in full" in block
    assert "/premium/checkout" not in block and "<button" not in block
    assert ('Your free full report can open '
            '<a href="/property?postcode=M35+1AA&amp;house_number=7#use-free-report">7 M35 1AA</a>, '
            'with no card.') in block
    for price in stripe_billing.plan_prices().values():
        assert price not in block

    # Waiting on confirmation, the report is claimed from the banner.
    _live(monkeypatch)
    email = "c3-unspent-unconfirmed@customer.test"
    assert _signup(client, email).status_code == 303
    block = " ".join(_home_block(client.get(with_home).text).split())
    assert "/premium/checkout" not in block
    assert ('<a href="/property?postcode=M35+1AA&amp;house_number=7#verify-banner">7 M35 1AA</a>, '
            f'with no card, once you confirm {email}.') in block


def test_c2_premium_types_no_price_the_plans_already_hold():
    """The lede said "Premium is £9.99 a month", typed, and named only the
    monthly plan, on the page that badges the three-month plan as made for
    one house hunt. The meta description typed the same price."""
    template = _without_template_comments(PREMIUM_TEMPLATE.read_text(encoding="utf-8"))
    for typed in ("9.99", "24.99"):
        assert typed not in template, f"premium.html types {typed} out by hand"
    lede = template.split('<p class="premium-lede">', 1)[1].split("</p>", 1)[0]
    assert lede.index("{{ plan_prices.quarterly }}") < lede.index("{{ plan_prices.monthly }}")
