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
        # Six since 18 Sep 2026: what Premium covers outside England.
        assert len(visible) == 6, (configured, pass_on)
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
    # Since E5 (18 Sep 2026) it names the triggers, not "something".
    body = client.get(url).text
    assert "an email when anything changes" not in body
    assert "an email when something changes on a saved property" not in body
    assert "an email when one of these happens to a saved home: " + app_main.ALERT_TRIGGERS_LIST in body


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
    # Worded apart from OpenStreetMap since the batch D review (D6).
    assert (f"One report per address: {total} checks from {len(app_main.OFFICIAL_SOURCES)} official bodies, "
            "with OpenStreetMap for what is nearby") in compare
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
    # The projection's "255,555" left this list on 18 Sep 2026 with the
    # projection itself (D3 below): nobody is shown it now, paid or not.
    "17.3", "247,777",                                           # Price Trend
    "2 Mar 2011", "87 m", "Locked Lane",                         # Extended or Modified (day_label since 18 Sep 2026), Aspect
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
    "Price Trend": "modal-price-trend",   # "& Forecast" until D3, 18 Sep 2026
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
    # 18 Sep 2026 (D4): every question a free card raised is now shown in
    # full, so both flood questions are here, where the teaser showed one
    # and listed the other's trigger after "The others come from:".
    assert "questions</strong> were generated for this property." in teaser
    assert "Has the property ever flooded" in teaser
    assert "Order a flood risk report and check the insurer will offer cover under Flood Re." in teaser
    assert "The others come from:" not in teaser
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
    assert visible == structured and len(visible) == 6  # outside England, 18 Sep 2026

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

    # district and country are required keywords since 18 Sep 2026.
    async def _crime(_lat, _lon, **_where):
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


# ---- D1. A search without a house number describes the postcode ---------
# KT3 4HX searched without a house number read as one house stitched from
# two: "Semi-detached house · 122 m²" and "£1,867 a year energy" were 57
# Malden Hill Gardens' newest certificate, "£823,500 last sold here" and
# "Freehold" were 55's sale, and the locked valuation divided one by the
# other. The report now offers every home it holds for the postcode under
# the heading ("Which home is yours?"), names the home the certificate
# line describes, gives the postcode's own energy range and tenure split
# from the running-costs page's helpers, keeps "last sold here" for a
# chosen home, and leaves the per square metre line out until one is
# chosen. With a house number the report is as it was.

D1_CERTS = [
    {"address": "57, Malden Hill Gardens, New Malden", "rating": "D", "date": "2025-11-04", "certificate_number": "D1-57"},
    {"address": "59 Malden Hill Gardens, New Malden", "rating": "C", "date": "2019-03-12", "certificate_number": "D1-59"},
]
D1_SALE_55 = {"address": "55 MALDEN HILL GARDENS", "street": "MALDEN HILL GARDENS", "town": "NEW MALDEN",
              "amount": "823500", "date": "2025-06-20", "tenure": "Freehold"}
D1_DETAIL_57 = {
    "dwelling_type": "Semi-detached house", "total_floor_area": 122, "habitable_room_count": 6,
    "year_built": "1900–1929", "current_score": 58, "potential_score": 79, "current_band": "D",
    "potential_band": "C", "inspection_date": "2025-11-04", "valid_until": "2035-11-04",
    "heating_cost_current": 1500, "lighting_cost_current": 120, "hot_water_cost_current": 247,
    "heating_cost_potential": None, "lighting_cost_potential": None, "hot_water_cost_potential": None,
}


def _d1_gather(**overrides):
    from tests.conftest import fake_gather
    base = {"certificates": D1_CERTS, "transactions": [D1_SALE_55], "property_detail": D1_DETAIL_57,
            "council_tax": {"authority": "Kingston upon Thames", "year": "2026-27", "band_d": 2412.0, "bands": {"D": 2412.0}}}
    base.update(overrides)
    return fake_gather(**base)


def _d1_report(client, fake_report, house_number="", **overrides):
    fake_report(location=fake_location(postcode="KT3 4HX", outcode="KT3"), gather=_d1_gather(**overrides))
    url = "/property?postcode=KT3+4HX" + (f"&house_number={house_number}" if house_number else "")
    r = client.get(url)
    assert r.status_code == 200
    return r.text


def _d1_row(body):
    assert 'class="which-home"' in body, "no Which home row"
    return body.split('class="which-home"', 1)[1].split("</nav>", 1)[0]


def _d1_links(row):
    """(href, full address, what the link shows) for each home in the row."""
    # nofollow since the batch D review: each link costs a full gather.
    found = re.findall(r'<a class="which-home-link" href="([^"]+)"(?: aria-label="([^"]+)")? rel="nofollow">([^<]+)</a>', row)
    assert len(found) == row.count('class="which-home-link"'), "a link without rel=nofollow"
    return [(href, full or shown, shown) for href, full, shown in found]


def _d1_costs(body):
    return " ".join(body.split('id="running-costs"', 1)[1].split("</div>", 1)[0].split())


def _d1_highlights(body):
    strip = body.split("What stands out", 1)[1].split("report-cards-hint", 1)[0]
    return re.findall(r'<span class="highlight-value">(.*?)</span>\s*<span class="highlight-label">(.*?)</span>', strip)


def test_d1_a_postcode_only_report_offers_each_home_it_holds_and_says_no_last_sold_here(client, fake_report):
    body = _d1_report(client, fake_report)
    row = _d1_row(body)
    assert "Which home is yours?" in row and "covers every home at KT3 4HX" in row
    links = _d1_links(row)
    # Two certificates and a sale at a third home, in number order, each
    # opening the same report on that home alone. The street is named
    # once, and each number carries its whole address.
    assert [full for _, full, _ in links] == ["55 Malden Hill Gardens", "57 Malden Hill Gardens", "59 Malden Hill Gardens"]
    assert [shown for _, _, shown in links] == ["55", "57", "59"]
    assert row.count('<p class="which-home-street">Malden Hill Gardens</p>') == 1
    # The number alone, since "57" opens exactly the records the whole
    # address opens (batch D review): an unlock kept against a typed "57"
    # is the same report through the row.
    assert links[1][0] == "/property?postcode=KT3%204HX&amp;house_number=57"
    assert "EPC Register and HM Land Registry" in row
    # Directly under the postcode, before the score and the cost line.
    assert body.index("<h1>KT3 4HX</h1>") < body.index('class="which-home"') < body.index('id="running-costs"')

    assert "last sold here" not in body
    highlights = _d1_highlights(body)
    assert ("£823,500", "the one recorded sale here, 2025") in highlights
    # The property line names the home it describes.
    assert "Newest certificate here: 57 Malden Hill Gardens, semi-detached house, 122 m², 6 habitable rooms, built 1900–1929" in body
    assert "One home's certificate from the EPC Register, not a description of every home at KT3 4HX." in body
    assert "may not exactly match a specific unit" not in body
    # Too few estimates for a range: the bill is named for its home, and
    # the tenure is the postcode's, not "at the last recorded sale".
    costs = _d1_costs(body)
    assert "&pound;1,867</strong> a year energy at 57 Malden Hill Gardens, the EPC's estimate on the newest certificate here" in costs
    assert "<strong>Freehold</strong> at the one recorded sale here" in costs
    assert "at the last recorded sale" not in costs
    # The house number filter below the map is still there.
    assert 'id="address-filter"' in body


def test_d1_with_a_house_number_the_row_goes_and_last_sold_here_returns(client, fake_report):
    body = _d1_report(client, fake_report, house_number="55+Malden+Hill+Gardens")
    assert 'class="which-home"' not in body and "Which home is yours?" not in body
    assert ("£823,500", "last sold here, 2025") in _d1_highlights(body)
    costs = _d1_costs(body)
    assert "&pound;1,867</strong> a year energy, the EPC's estimate" in costs
    assert "<strong>Freehold</strong> at the last recorded sale (2025)" in costs
    assert "Newest certificate here" not in body
    assert "Based on the most recent EPC certificate for this postcode, so it may not exactly match a specific unit." in body
    assert 'id="address-filter"' in body and 'value="55 Malden Hill Gardens"' in body


def test_d1_the_cost_line_and_the_highlight_give_the_postcodes_own_figures(client, fake_report):
    sales = [dict(D1_SALE_55, address=f"{n} MALDEN HILL GARDENS", amount=str(400000 + n * 1000),
                  date=f"{2025 - i}-05-01", tenure="Freehold" if i % 2 else "Leasehold")
             for i, n in enumerate(range(1, 30))]
    energy = {"certificates": 12, "priced": 8, "low": 462, "high": 2192, "median": 974,
              "median_potential": 700, "bands": "C x3, D x5"}
    body = _d1_report(client, fake_report, transactions=sales, postcode_energy=energy)
    costs = _d1_costs(body)
    assert "£974</strong> a year energy, the middle of 8 homes' EPC estimates here, from £462 to £2,192" in costs
    assert "<strong>15 leasehold, 14 freehold</strong> of 29 recorded sales here" in costs
    assert "1,867" not in costs
    # The middle of the last ten priced sales (2016 to 2025), and how many
    # the postcode has, in place of one home's last sale.
    middle = sorted(400000 + n * 1000 for n in range(1, 11))[5]
    assert (f"£{middle:,}", "middle of the last 10 of 29 recorded sales here, 2016 to 2025") in _d1_highlights(body)
    assert "last sold here" not in body
    # 29 homes from the sales, two more from the certificates: 24 show and
    # the other seven open from "and 7 more".
    row = _d1_row(body)
    first, more = row.split('<details class="which-home-more">', 1)
    assert len(re.findall(r'class="which-home-link"', first)) == app_main.WHICH_HOME_SHOWN == 24
    assert "<summary>and 7 more</summary>" in more and len(re.findall(r'class="which-home-link"', more)) == 7


def test_d1_one_home_in_both_sources_is_one_link_and_every_link_fits_the_column():
    certs = [{"address": "Flat 2, 12 High Street, Oldtown", "certificate_number": "A"},
             {"address": "ROSE COTTAGE, CHURCH LANE", "certificate_number": "B"},
             {"address": "10, High Street", "certificate_number": "C"}]
    sales = [{"address": "FLAT 2 12 HIGH STREET", "street": "HIGH STREET"},
             {"address": "9 HIGH STREET", "street": "HIGH STREET"},
             {"address": "FLAT 14 THE VERY LONG NAMED BUILDING 120 HIGH STREET", "street": "HIGH STREET"},
             {"address": "Address not available"}]
    homes = app_main._postcode_homes(certs, sales)
    # Street by street in number order; the named house whose street has
    # no sale keeps the register's whole address, in ordinary case.
    assert [h["label"] for h in homes] == [
        "9 High Street", "10 High Street", "Flat 2, 12 High Street",
        "Flat 14 The Very Long Named Building 120 High Street", "Rose Cottage, Church Lane"]
    assert [(h["street"], h["short"]) for h in homes] == [
        ("High Street", "9"), ("High Street", "10"), ("High Street", "Flat 2, 12"),
        ("High Street", "Flat 14 The Very Long Named Building 120"), ("", "Rose Cottage, Church Lane")]
    assert all(len(h["house_number"]) <= app_main.HOUSE_NUMBER_MAX_LEN for h in homes)
    long_one = homes[3]["house_number"]
    assert long_one == "Flat 14 The Very Long Named"
    # A cut label still opens its own home, and only that one.
    assert app_main._filter_by_address(sales, long_one) == [sales[2]]
    # Two dozen shown, street by street, and the rest behind "and N more".
    many = [{"address": f"{n} HIGH STREET", "street": "HIGH STREET"} for n in range(1, 31)]
    sections = app_main._which_home_sections(app_main._postcode_homes(certs, many))
    # 1 to 30 High Street, Flat 2 at 12 and Rose Cottage: 32 homes.
    assert [(s["more"], s["count"]) for s in sections] == [(False, 24), (True, 8)]
    assert [g["street"] for g in sections[1]["groups"]] == ["High Street", ""]


def test_d1_the_house_number_filter_finds_the_home_asked_for_and_not_its_neighbours():
    records = [{"address": a} for a in (
        "5 MALDEN HILL GARDENS", "55 MALDEN HILL GARDENS", "15 MALDEN HILL GARDENS",
        "FLAT 2 5 MALDEN HILL GARDENS", "5A MALDEN HILL GARDENS", "50 MALDEN HILL GARDENS")]
    pick = lambda q: [r["address"] for r in app_main._filter_by_address(records, q)]  # noqa: E731
    # 18 Sep 2026, item D7: where a plain 5 is recorded, "5" is that home
    # and not also 5A; "5" still finds 5A where no plain 5 is recorded.
    assert pick("5") == ["5 MALDEN HILL GARDENS"]
    assert pick("5A") == ["5A MALDEN HILL GARDENS"]
    assert pick("5 Malden Hill Gardens") == ["5 MALDEN HILL GARDENS"]
    assert pick("Flat 2, 5 Malden") == ["FLAT 2 5 MALDEN HILL GARDENS"]
    assert len(pick("Malden Hill")) == 6 and len(pick("   ")) == 6
    # The EPC Register's commas do not keep a home from its own sale.
    assert app_main._filter_by_address([{"address": "Flat 2, 12 High Street"}], "FLAT 2 12 HIGH STREET")
    # Where no address starts with the number, it still finds its flats.
    flats = [{"address": "Flat 1, 37 Avalon Road"}, {"address": "Flat 2, 37 Avalon Road"}, {"address": "137 Avalon Road"}]
    assert [r["address"] for r in app_main._filter_by_address(flats, "37")] == ["Flat 1, 37 Avalon Road", "Flat 2, 37 Avalon Road"]


def test_d1_the_per_square_metre_line_waits_for_a_house_number():
    """55's price over 57's floor area was the locked valuation's "This
    home last sold at £X per m²". Without a house number it is not made."""
    recent = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
    comparables = [{"address": f"{n} Near Road", "date": recent, "amount": str(500000 + n * 10000), "floor_area": 100 + n}
                   for n in range(1, 6)]
    sale = dict(D1_SALE_55, date=recent)
    postcode_only = {"transactions": [sale]}
    app_main._apply_valuation(postcode_only, comparables, 122, 2.0, "")
    assert postcode_only["price_per_sqm"]["subject"] is None
    assert postcode_only["price_per_sqm"]["median"]            # the local rate stays
    chosen = {"transactions": [sale]}
    app_main._apply_valuation(chosen, comparables, 122, 2.0, "55 Malden Hill Gardens")
    assert chosen["price_per_sqm"]["subject"]["amount"] == 823500.0


def test_d1_the_valuation_endpoint_passes_the_house_number_on(client, fake_report, monkeypatch):
    recent = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()

    async def _comparables(_lat, _lon):
        return [{"address": f"{n} Near Road", "date": recent, "amount": "600000", "floor_area": 110 + n} for n in range(4)]

    seen = []
    real = app_main._apply_valuation

    def _spy(context, comparables, floor_area, growth, house_number=""):
        real(context, comparables, floor_area, growth, house_number)
        seen.append((house_number, bool((context.get("price_per_sqm") or {}).get("subject"))))

    fake_report(location=fake_location(postcode="KT3 4HX", outcode="KT3"),
                gather=_d1_gather(transactions=[dict(D1_SALE_55, date=recent)]))
    monkeypatch.setattr(app_main, "_comparables_fetch", _comparables)
    monkeypatch.setattr(app_main, "_apply_valuation", _spy)
    assert client.get("/api/property/valuation?postcode=KT3+4HX").status_code == 200
    assert client.get("/api/property/valuation?postcode=KT3+4HX&house_number=55").status_code == 200
    assert seen == [("", False), ("55", True)]


def test_d1_the_gather_reads_the_postcodes_energy_once_per_home_and_only_without_a_number(monkeypatch):
    certs = [{"address": "57, Malden Hill Gardens", "rating": "D", "date": "2025-11-04", "certificate_number": "N57"},
             {"address": "57, Malden Hill Gardens", "rating": "E", "date": "2015-01-01", "certificate_number": "O57"},
             {"address": "59 Malden Hill Gardens", "rating": "C", "date": "2019-03-12", "certificate_number": "N59"},
             {"address": "61 Malden Hill Gardens", "rating": "C", "date": "2018-03-12", "certificate_number": "N61"}]
    bills = {"N57": 1867, "O57": 2500, "N59": 974, "N61": 462}
    calls = []

    async def _certs(_pc):
        return [dict(c) for c in certs]

    async def _detail(number):
        calls.append(number)
        return {**D1_DETAIL_57, "heating_cost_current": bills[number], "lighting_cost_current": 0,
                "hot_water_cost_current": 0, "current_band": "C"}

    monkeypatch.setattr(app_main.epc, "certificates_for_postcode", _certs)
    monkeypatch.setattr(app_main.epc, "certificate_detail", _detail)

    found, detail, _, energy = asyncio.run(app_main._epc_flow("KT3 4HX", "", True, postcode_energy=True))
    assert len(found) == 4 and detail["heating_cost_current"] == 1867
    # One call per home, the newest certificate's among them, never twice.
    assert sorted(calls) == ["N57", "N59", "N61"]
    assert energy["priced"] == 3 and energy["low"] == 462 and energy["high"] == 1867 and energy["median"] == 974
    # The same figures the running-costs page computes from the same helper.
    assert energy == app_main._postcode_energy_summary(certs, [asyncio.run(_detail(n)) for n in ("N57", "N59", "N61")])

    calls.clear()
    *_, energy = asyncio.run(app_main._epc_flow("KT3 4HX", "59", True, postcode_energy=True))
    assert energy is None and calls == ["N59"]
    calls.clear()
    *_, energy = asyncio.run(app_main._epc_flow("KT3 4HX", "", True))
    assert energy is None and calls == ["N57"]


# ---- D2. On a phone the answer comes first in the tables that matter ------
# At 375px the admissions hub cut its header to "ADMITTED F" and read 0.14
# miles as "0", the schools guide showed School, Phase and Ofsted with the
# reading off screen, comparables hid price, date and distance for all 300
# rows, and the private schools and area guide tables were cut the same
# way, each scrolling sideways inside its section with nothing to say so.
# Below 600px a table marked data-stack now stacks each row: the name, the
# answer line, then the rest as label and value. The desktop columns are
# unchanged. Comparables lists 25 sales at a time on a phone, every row
# still in the page, and its caption says the rows are nearest first. The
# layout itself is checked by eye at 375px; these pin the markup and CSS.

D2_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _d2_text(inner, phone):
    """A cell's words as a phone or a desktop reads them: the .st-only
    words are shown only in the stacked row."""
    if not phone:
        inner = re.sub(r'<span class="st-only">.*?</span>', "", inner, flags=re.S)
    return " ".join(re.sub(r"<[^>]+>", "", inner).split())


def _d2_cells(row):
    """(attributes, phone text, desktop text) for each cell of a row."""
    return [(attrs.strip(), _d2_text(inner, True), _d2_text(inner, False))
            for attrs, inner in re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S)]


def _d2_roles(cells):
    """Each cell's part in the stacked row: its data-st, else its
    data-label, else "" for a cell with neither."""
    return [(re.search(r'data-(?:st|label)="([^"]+)"', attrs) or [None, ""])[1] for attrs, _, _ in cells]


def _d2_row(body, name):
    return next(r for r in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S) if name in r and "<td" in r)


def _d2_first_line(cells, sep):
    """The stacked answer line: the lead cell, then each answer cell."""
    lead = [phone for attrs, phone, _ in cells if 'data-st="lead"' in attrs]
    answers = [phone for attrs, phone, _ in cells if 'data-st="answer"' in attrs]
    assert len(lead) == 1, cells
    return sep.join(lead + answers)


def _d2_comparables(client, monkeypatch, count=30):
    from tests.test_ai_search_readiness import _forget_html

    async def _lookup(_postcode):
        return fake_location()

    async def _nearby(lat, lon, **kwargs):
        return [{"postcode": "M14 5TG", "distance_m": 0, "latitude": 53.45, "longitude": -2.22},
                {"postcode": "M14 5TH", "distance_m": 400, "latitude": 53.452, "longitude": -2.221}]

    async def _sold(postcodes):
        # Newest first across both postcodes, as the Land Registry query
        # returns them; the route then sorts by distance.
        return [{"address": f"{i + 1} Stack Street", "postcode": "M14 5TH" if i % 2 == 0 else "M14 5TG",
                 "amount": str(200000 + i * 1000), "date": f"{2025 - i // 12}-{12 - i % 12:02d}-01",
                 "property_type": "flat", "tenure": "Leasehold"} for i in range(count)]

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "nearby_postcodes", _nearby)
    monkeypatch.setattr(app_main, "sold_prices_for_postcodes", _sold)
    _forget_html()
    r = client.get("/property/comparables?postcode=M14%205TG")
    assert r.status_code == 200
    return r.text.replace("\r\n", "\n")


def test_d2_each_answer_table_carries_its_phone_markup():
    templates = D2_ROOT / "app" / "templates"
    for name, table, marks in (
        ("schools_admissions_council.html", '<table class="tx-table school-table" data-stack="comma">',
         ('data-st="title"', 'data-st="lead"', 'data-st="answer"', 'data-label="Ofsted"', 'data-label="How full"')),
        ("schools_guide.html", 'id="school-table-{{ area_idx }}" data-stack>',
         ('data-st="title"', 'data-label="Phase"', 'data-label="Ofsted"', 'data-st="sub" data-label="Admitted from"',
          'data-label="Results"', '<span class="st-only"> away</span>')),
        ("comparables.html", '<table class="tx-table" id="comparables-table" data-stack>',
         ('data-st="sub"', 'data-st="skip"', 'data-label="Type"', 'data-label="Tenure"', 'data-st="lead"',
          'data-st="answer"')),
        ("area_guide.html", '<table class="tx-table" data-stack>',
         ('data-st="title"', 'data-label="Admitted from"', 'data-label="Stage"')),
        ("schools_independent_district.html", '<table class="tx-table school-table" data-stack>',
         ('data-st="title"', 'data-st="lead"', 'data-label="Faith"', 'data-label="On roll"',
          '<span class="st-only"> full</span>')),
    ):
        source = (templates / name).read_text(encoding="utf-8")
        assert table in source, name
        for mark in marks:
            assert mark in source, f"{name} lacks {mark}"
    # The area guide's second school table stacks the way the hub does.
    assert '<table class="tx-table" data-stack="comma">' in (templates / "area_guide.html").read_text(encoding="utf-8")


def test_d2_comparables_lead_each_sale_with_price_date_and_distance_nearest_first(client, monkeypatch):
    body = _d2_comparables(client, monkeypatch)
    table = body.split('<table class="tx-table" id="comparables-table" data-stack>', 1)[1].split("</table>", 1)[0]
    rows = re.findall(r"<tr data-date=\"[^\"]+\">(.*?)</tr>", table, re.S)
    assert len(rows) == 30, "every sale is in the page, with or without JavaScript"
    # Nothing is hidden or paged by the server: only the script marks rows.
    assert "comp-beyond" not in table and not re.search(r"<tr[^>]*\shidden", table)

    first = _d2_cells(rows[0])
    assert _d2_roles(first) == ["sub", "skip", "half", "half", "lead", "answer", "answer"]
    # The nearest postcode's newest sale leads, as the caption now says.
    # dates read "1 Nov 2025" since the day_label filter of 18 Sep 2026
    assert _d2_first_line(first, " · ") == "£201,000 · 1 Nov 2025 · 0 yd"
    assert first[0][1] == "2 Stack Street, M14 5TG" and first[0][2] == "2 Stack Street"
    assert 'data-label="Type"' in first[2][0] and 'data-label="Tenure"' in first[3][0]
    distances = [_d2_cells(r)[6][2] for r in rows]
    assert distances == sorted(distances, key=lambda d: d != "0 yd")
    dates = [_d2_cells(r)[5][2] for r in rows if _d2_cells(r)[6][2] == "0 yd"]
    # compared as dates: they read "1 Nov 2025" since 18 Sep 2026, which
    # does not sort as text
    import datetime
    as_dates = [datetime.datetime.strptime(d, "%d %b %Y") for d in dates]
    assert as_dates == sorted(as_dates, reverse=True)

    caption = body.split('id="comparables"', 1)[1].split("</p>", 1)[0]
    assert "Listed nearest first, and newest first within each postcode." in " ".join(caption.split())
    assert "most recent first" not in body


def test_d2_comparables_show_25_more_and_keep_the_year_filter(client, monkeypatch):
    body = _d2_comparables(client, monkeypatch)
    assert ('<div class="comp-more" id="comp-more" hidden>\n'
            '                <button type="button" class="comp-more-btn" id="comp-more-btn">Show 25 more</button>') in body
    assert "var COMP_PAGE = 25;" in body and "function compPaginate()" in body
    # The filter hides a row first; the page limit counts only rows in range.
    assert "if (tr.hidden) { tr.classList.remove('comp-beyond'); return; }" in body
    assert "tr.classList.toggle('comp-beyond', inRange > compLimit);" in body
    # A new range starts at 25 again; the maps' callback with the same
    # range leaves what the reader opened alone. Every call re-pages.
    apply_range = body.split("function compApplyRange(years) {", 1)[1].split("\n    }\n", 1)[0]
    assert "if (years !== window.__compYears) compLimit = COMP_PAGE;" in apply_range
    assert apply_range.rstrip().endswith("compPaginate();")
    # The count names sales in the range, which a phone listing 25 no longer "shows".
    assert "sales shown" not in body and "' sales'" in body
    # Production renders the Google branch: the paging lives above both.
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    body = _d2_comparables(client, monkeypatch)
    assert "var COMP_PAGE = 25;" in body and 'id="comp-more"' in body
    assert "compApplyRange(window.__compYears || 0);" in body and "marker.setMap(visible ? map : null)" in body


def _d2_page(client, monkeypatch, path, loader, data, cache_key=None):
    """One page rendered from data handed to its loader, so no row is
    written to the database the other tests share."""
    from app.services import _cache
    from tests.test_ai_search_readiness import _forget_html

    monkeypatch.setattr(app_main.schools_db, loader, lambda slug: data if slug == "stackford" else None)
    if cache_key:
        _cache._evict(cache_key)
    _forget_html()
    try:
        r = client.get(path)
    finally:
        if cache_key:
            _cache._evict(cache_key)
        _forget_html()
    assert r.status_code == 200
    return r.text


def test_d2_the_admissions_hub_reads_school_then_distance_and_round(client, monkeypatch):
    # The Birmingham rows the audit read as 0, 1 and 2.
    schools = [{"urn": 991820 + i, "name": name, "slug": name.lower().replace(" ", "-"), "phase": "Primary",
                "type": "Community school", "town": "Stackford", "ofsted_rating": 2, "ofsted_rating_label": "Good",
                "ofsted_note": None, "miles": miles, "academic_year": "2023/24", "occupancy_pct": pct}
               for i, (name, miles, pct) in enumerate((("Elm Row Primary", 0.14, 98), ("Birch Hill Primary", 1.16, None),
                                                        ("Cedar Lane Primary", 2.59, 87)))]
    council = {"name": "Stackford", "slug": "stackford", "count": 3, "schools": schools,
               "by_phase": [("Primary", schools)], "median_miles": 1.16, "tightest": schools[0],
               "widest": schools[-1], "years": ["2023/24"], "under_a_mile": 1}
    body = _d2_page(client, monkeypatch, "/schools/admissions/stackford", "admission_council", council,
                    cache_key=("admission_council", "stackford"))
    tables = re.findall(r"<table[^>]*>", body)
    assert tables == ['<table class="tx-table school-table" data-stack="comma">']
    lines = []
    for name in ("Elm Row Primary", "Birch Hill Primary", "Cedar Lane Primary"):
        cells = _d2_cells(_d2_row(body, name))
        assert _d2_roles(cells) == ["title", "Ofsted", "lead", "answer", "How full"]
        assert cells[0][2].startswith(name)
        lines.append(_d2_first_line(cells, ", "))
    assert lines == ["0.14 mi, 2023/24", "1.16 mi, 2023/24", "2.59 mi, 2023/24"]


def test_d2_the_schools_guide_reads_school_then_its_reading_and_distance(client, monkeypatch):
    from tests.test_ai_search_readiness import _forget_html
    from tests.test_brainstorm_17sep import _patch_guide

    # The guide's own landscape fake: Published Primary is 400 m from the
    # point and admitted from 1.5 miles, so the reading is Likely.
    _patch_guide(monkeypatch, {"latitude": 53.4502, "longitude": -2.2202, "label": "M14 5TX", "kind": "postcode"})
    _forget_html()
    body = client.get("/schools/guide?q=M14+5TX").text
    assert 'id="school-table-0" data-stack>' in body
    cells = _d2_cells(_d2_row(body, "Published Primary"))
    assert _d2_roles(cells) == ["title", "Phase", "Ofsted", "answer", "sub", "", "Results"]
    distance, admitted, reading = cells[3], cells[4], cells[5]
    assert distance[2] == "0.2 mi" and distance[1] == "0.2 mi away", "away is a phone-only word"
    assert 'data-label="Admitted from"' in admitted[0] and admitted[2] == "1.5 mi 2025/26"
    # The reading cell keeps its bare tag (other tests read it exactly) and
    # the stylesheet finds it as the cell after the admitted-from one.
    assert reading[0] == 'data-value="1"' and reading[2] == "Likely"
    css = (D2_ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    rule = css.split('table[data-stack].school-table td[data-st="sub"] + td:not([class]) {', 1)[1].split("}", 1)[0]
    assert "order: -3;" in rule

    # A district search has no reading, so the distance leads the line.
    _patch_guide(monkeypatch, {"latitude": 53.4502, "longitude": -2.2202, "label": "M14", "kind": "outcode"})
    _forget_html()
    body = client.get("/schools/guide?q=M14").text
    cells = _d2_cells(_d2_row(body, "Published Primary"))
    assert _d2_roles(cells) == ["title", "Phase", "Ofsted", "lead", "sub", "Results"]
    assert _d2_first_line(cells, " · ") == "0.2 mi away"


def test_d2_the_private_schools_table_reads_ages_pupils_and_how_full(client, monkeypatch):
    whitworth = {"name": "Whitworth House School", "website": "https://example.org", "town": "Stackford",
                 "postcode": "M1 3CC", "age_low": 3, "age_high": 18, "gender": "Girls", "religious_character": "None",
                 "number_on_roll": 300, "occupancy_pct": 75}
    unstated = {"name": "Rowan Tutorial College", "website": "", "town": "Stackford", "postcode": "M1 3CD",
                "age_low": None, "age_high": None, "gender": "", "religious_character": "Church of England",
                "number_on_roll": None, "occupancy_pct": None}
    district = {"name": "Stackford", "slug": "stackford", "count": 2, "mainstream": 2, "special": 0,
                "single_sex": 1, "with_sixth_form": 1, "pupils": 300,
                "groups": [("Mainstream schools", "By their registered age ranges.", [whitworth, unstated])]}
    body = _d2_page(client, monkeypatch, "/schools/independent/stackford", "independent_district", district)
    assert re.findall(r"<table[^>]*>", body) == ['<table class="tx-table school-table" data-stack>']
    cells = _d2_cells(_d2_row(body, "Whitworth House School"))
    assert _d2_roles(cells) == ["title", "lead", "answer", "Faith", "On roll", "answer"]
    assert _d2_first_line(cells, " · ") == "Ages 3 to 18 · Girls · 75% full"
    # A desktop reads the columns exactly as before.
    assert [desk for _, _, desk in cells[1:]] == ["3 to 18", "Girls", "Non-faith", "300", "75%"]
    # Where the register is silent, the stacked line says which figure is missing.
    cells = _d2_cells(_d2_row(body, "Rowan Tutorial College"))
    assert _d2_first_line(cells, " · ") == "Ages: Not stated · Pupils: Not stated · How full: Not reported"
    assert [desk for _, _, desk in cells[1:]] == ["Not stated", "Not stated", "Church of England", "Not reported", "Not reported"]


def test_d2_the_area_guides_school_tables_stack_with_the_distance_on_the_first_line(client, monkeypatch):
    import time
    from app.services import _cache
    from tests.test_ai_search_readiness import AREA_PAYLOAD, _forget_html

    async def _resolve(outcode):
        return fake_location(postcode=f"{outcode} 2AA", outcode=outcode), True

    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    payload = dict(AREA_PAYLOAD)
    payload["named_schools"] = [
        {"urn": 991811, "slug": "oak-primary", "name": "Oak Primary", "phase": "Primary", "rating": "Good",
         "rating_code": 2, "distance_m": 482.8, "admitted_miles": 0.59, "admitted_year": "2025/26"},
        {"urn": 991812, "slug": "", "name": "Ash Nursery", "phase": "", "rating": "Outstanding",
         "rating_code": 1, "distance_m": 900, "admitted_miles": None, "admitted_year": None},
    ]
    payload["admission_schools_here"] = [
        {"urn": 991811, "slug": "oak-primary", "name": "Oak Primary", "phase": "Primary", "miles": 0.59,
         "academic_year": "2025/26"}]
    key = ("area_guide", app_main.AREA_GUIDE_PAYLOAD_VERSION, "AB14")
    _cache._put(key, time.time(), payload)
    _forget_html()
    try:
        body = client.get("/area/AB14").text.replace("\r\n", "\n")
    finally:
        _cache._evict(key)

    before, nearest = body.split("Nearest well-rated schools", 1)
    assert before.endswith('<table class="tx-table" data-stack>\n        <thead><tr><th>')
    nearest = nearest.split("</table>", 1)[0]
    oak = _d2_cells(_d2_row(nearest, "Oak Primary"))
    assert _d2_roles(oak) == ["title", "lead", "answer", "answer", "Admitted from"]
    assert _d2_first_line(oak, " · ") == "Primary · Good · 0.3 mi away"
    assert oak[4][2] == "0.59 mi 2025/26"
    # No stage recorded: the grade leads, so the line opens on no separator.
    ash = _d2_cells(_d2_row(nearest, "Ash Nursery"))
    assert _d2_roles(ash) == ["title", "skip", "lead", "answer", "Admitted from"]
    assert _d2_first_line(ash, " · ") == "Outstanding · 0.6 mi away"

    published = body.split("with a published admission distance</h3>", 1)[1].split("</table>", 1)[0]
    assert '<table class="tx-table" data-stack="comma">' in published
    cells = _d2_cells(_d2_row(published, "Oak Primary"))
    assert _d2_roles(cells) == ["title", "Stage", "lead", "answer"]
    assert _d2_first_line(cells, ", ") == "0.59 mi, 2025/26"


def _d2_phone_blocks(css):
    """Every @media (max-width: 600px) block, comments removed, as (start, end, text)."""
    blocks = []
    for m in re.finditer(r"@media \(max-width: 600px\) \{", css):
        depth, i = 0, m.end() - 1
        while True:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            if depth == 0:
                break
            i += 1
        blocks.append((m.start(), i, css[m.start():i]))
    return blocks


def test_d2_the_stacking_lives_only_below_600px_in_tokens_and_the_lamp_needs_no_rule():
    css = re.sub(r"/\*.*?\*/", "", (D2_ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8"),
                 flags=re.S)
    blocks = _d2_phone_blocks(css)
    phone_only = [m.start() for m in re.finditer(
        r"table\[data-stack|comp-beyond|\.comp-more:not|\.comp-more \.comp-more-btn|\.comp-more-count", css)]
    assert phone_only
    for at in phone_only:
        assert any(start < at < end for start, end, _ in blocks), css[at:at + 60]
    # Outside the phone width the only new rules hide what is phone-only,
    # so a desktop table and its page read as they did.
    for rule in (".st-only { display: none; }", ".comp-more { display: none; }"):
        at = css.index(rule)
        assert not any(start < at < end for start, end, _ in blocks), rule
    stacking = [text for _, _, text in blocks if "data-stack" in text or "comp-beyond" in text]
    assert stacking
    for text in stacking:
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", text), "a colour outside the tokens"
    joined = "".join(stacking)
    # The year filter's hidden rows stay hidden once a row is a flex box.
    assert "table[data-stack] tr[hidden] { display: none; }" in joined
    assert "table[data-stack] thead { display: none; }" in joined
    assert "#comparables-table tr.comp-beyond { display: none; }" in joined
    # Separators trail the item before them, so a wrapped answer line ends
    # on one instead of opening the next line with it.
    assert 'table[data-stack] td[data-st="answer"]:has(~ td[data-st="answer"])::after' in joined
    assert 'td[data-st="answer"]::before' not in joined
    assert not re.search(r"theme-dark[^{]*(data-stack|comp-more|st-only)", css)


# ---- D3. Three findings that overstate or contradict ---------------------
# (1) "Lower crime than the surrounding area" rested on 229 crimes against
# 230, central York compared 2 with 2, and BN1 1EE printed "1834". Lower
# or higher is now crime.compare_counts' answer on the two totals: at
# least 10 per cent of the larger and at least 5 crimes apart, "about the
# same" otherwise, and no comparison at all when both are in single
# figures. The score's reason, What stands out, the card, the pop-up, the
# PDF and its checklist all ask it.
# (2) The locked Price Trend & Forecast pop-up projected a straight line a
# year and two years on, and drew a fall beside the free "Area prices
# rising". The projection is gone and the card is "Price Trend": the
# index's own history with its one, five and ten year changes.
# (3) The verdict counted 2 things worth checking and the banner under it
# 3, because the banner also counted the council's finances. Both are now
# overview_score.attention_items, and the council's chip says what
# happened in plain words.

def _d3_crime_gather(here, area, month="2026-07", **overrides):
    from tests.conftest import fake_gather
    return fake_gather(crime={"total": here, "month": month, "by_category": []},
                       district_crime={"total": area, "month": month, "by_category": []},
                       crime_comparison=[], **overrides)


def _d3_highlights(body):
    return re.findall(r'<span class="highlight-value">(.*?)</span>', body)


def _d3_crime_card(body):
    _tag, block = _b1_card(body, "Crime &amp; Safety")
    status = _flat(re.sub(r"<[^>]+>", " ", re.search(r'<span class="dashboard-card-status">(.*?)</span>', block, re.S).group(1)))
    sub = re.search(r'<span class="dashboard-card-substat">(.*?)</span>', block, re.S)
    return status, (_flat(sub.group(1)) if sub else None)


def test_d3_one_rule_decides_lower_or_higher_and_says_so_in_its_docstring():
    from app.services import crime
    assert crime.compare_counts(229, 230) == "same"
    assert crime.compare_counts(229, 300) == "lower"
    assert crime.compare_counts(300, 229) == "higher"
    assert crime.compare_counts(2, 2) == "few" and crime.compare_counts(9, 0) == "few"
    # Both conditions, not either: 5 crimes apart but under 10 per cent...
    assert crime.compare_counts(100, 105) == "same"
    # ...and over 10 per cent but under 5 crimes.
    assert crime.compare_counts(10, 14) == "same"
    assert crime.compare_counts(56, 50) == "higher"
    assert crime.compare_counts(None, 230) is None and crime.compare_counts(229, None) is None
    assert (crime.MARGIN_SHARE, crime.MARGIN_CRIMES, crime.FEW_RECORDS) == (0.10, 5, 10)
    doc = crime.compare_counts.__doc__
    assert "10 per cent" in doc and "5 crimes" in doc and "single" in doc

    # Two different months are no comparison, whatever the counts.
    walked_back = crime.versus_area({"total": 40, "month": "2026-05"}, {"total": 400, "month": "2026-07"})
    assert walked_back["verdict"] is None and walked_back["month_label"] == "May 2026"
    # The pop-up's table rows ask the same rule.
    rows = app_main._crime_comparison(
        {"month": "2026-07", "by_category": [{"category": "burglary", "count": 3}, {"category": "violent crime", "count": 60}]},
        {"month": "2026-07", "by_category": [{"category": "burglary", "count": 1}, {"category": "violent crime", "count": 90}]},
    )
    assert {r["category"]: r["trend"] for r in rows} == {"burglary": "few", "violent crime": "lower"}


def test_d3_the_scores_crime_reason_follows_the_rule():
    from app.services import overview_score

    def positives(here, area):
        return overview_score.compute({"crime": {"total": here, "month": "2026-07"},
                                       "district_crime": {"total": area, "month": "2026-07"}})["positives"]

    assert "Lower crime than the surrounding area" not in positives(229, 230)
    assert "Lower crime than the surrounding area" in positives(229, 300)
    assert "Lower crime than the surrounding area" not in positives(2, 3)
    # The category rows no longer decide it: a gather whose categories
    # were mostly lower, on the same totals, gets no crime reason.
    mostly_lower = [{"category": c, "here": 1, "area": 2, "trend": "lower"} for c in "abc"]
    assert "Lower crime than the surrounding area" not in overview_score.compute({
        "crime": {"total": 229, "month": "2026-07"}, "district_crime": {"total": 230, "month": "2026-07"},
        "crime_comparison": mostly_lower})["positives"]


def test_d3_229_against_230_reads_about_the_same_on_the_report(client, fake_report):
    fake_report(location=fake_location(postcode="KT3 4HX", outcode="KT3"), gather=_d3_crime_gather(229, 230))
    body = client.get("/property?postcode=KT3+4HX").text

    assert "Lower" not in _d3_highlights(body) and "Higher" not in _d3_highlights(body)
    status, sub = _d3_crime_card(body)
    assert status == "229 within about a mile, July 2026"
    assert sub == "About the same as the surrounding area"
    modal = _flat(re.sub(r"<[^>]+>", " ", _b3_modal(body, "modal-crime")))
    assert "229 crimes recorded within about a mile in July 2026, against 230 in the wider KT3 postcode area." in modal
    assert "About the same as the surrounding area." in modal
    assert "at least 10 per cent of the larger and by at least 5 crimes" in modal
    assert "2026-07" not in modal and "~1 mile in" not in modal


def test_d3_229_against_300_reads_lower_and_counts_carry_separators(client, fake_report):
    fake_report(location=fake_location(postcode="KT3 4HX", outcode="KT3"), gather=_d3_crime_gather(229, 300))
    body = client.get("/property?postcode=KT3+4HX").text
    assert "Lower" in _d3_highlights(body)
    assert _d3_crime_card(body)[1] == "Lower than the surrounding area"

    # BN1 1EE's count, with its comma, and higher on a real margin.
    fake_report(location=fake_location(postcode="BN1 1EE", outcode="BN1"), gather=_d3_crime_gather(1834, 1500))
    body = client.get("/property?postcode=BN1+1EE").text
    status, sub = _d3_crime_card(body)
    assert status == "1,834 within about a mile, July 2026" and sub == "Higher than the surrounding area"
    assert "Higher" in _d3_highlights(body)
    assert "1834" not in body.split('id="modal-crime"', 1)[1].split("</dialog>", 1)[0]


def test_d3_single_figures_say_police_uk_holds_few_records_and_compare_nothing(client, fake_report):
    fake_report(location=fake_location(postcode="YO1 7HH", outcode="YO1"), gather=_d3_crime_gather(2, 2))
    body = client.get("/property?postcode=YO1+7HH").text
    status, sub = _d3_crime_card(body)
    assert status == "2 within about a mile, July 2026"
    assert sub == "Police.uk holds few records here for July 2026"
    modal = _flat(re.sub(r"<[^>]+>", " ", _b3_modal(body, "modal-crime")))
    assert "Police.uk holds few records here for July 2026, too few to compare." in modal
    for word in ("About the same as", "Lower than", "Higher than"):
        assert word not in modal, word
    assert "Lower" not in _d3_highlights(body) and "Higher" not in _d3_highlights(body)


def test_d3_the_pdf_and_its_checklist_ask_the_same_rule():
    from app.services import pdf_checklist
    from tests.test_pdf_report import _running_costs
    report = _full_pdf_report()          # KT3 4HX: 229 against 230
    ctx = app_main._pdf_context(report, _running_costs(), report["location"], "36")
    page = app_main.templates.get_template("pdf_report_full.html").render(ctx)
    crime_part = page.split("<h2>Crime</h2>", 1)[1].split("</table>", 1)[0]
    assert "About the same" in crime_part and "Lower" not in crime_part
    assert "July 2026" in crime_part and "2026-07" not in crime_part
    rows = {r["check"]: r for r in pdf_checklist.build(report, _running_costs())}
    crime_row = rows["Crime within about a mile"]
    assert crime_row["result"] == "229 recorded in July 2026, about the same as the wider district"
    assert crime_row["status"] == "neutral"


# (2) Price trend.

def _d3_series(months=121, last="2026-07"):
    from app.services import hpi
    return [{"period": hpi._months_before(last, months - 1 - i), "average_price": 400000.0 + 1500 * i}
            for i in range(months)]


def _d3_trend(months=121):
    from app.services import hpi
    series = _d3_series(months)
    changes = hpi._changes(series)
    five = next((c for c in changes if c["years"] == 5), None)
    return {"area_name": "Kingston upon Thames", "series": series, "current_price": series[-1]["average_price"],
            "current_period": series[-1]["period"], "changes": changes,
            "start_price": five["price"] if five else None, "pct_change": five["pct"] if five else None}


def test_d3_the_index_service_projects_nothing_and_reads_one_five_and_ten_years():
    from app.services import hpi
    changes = hpi._changes(_d3_series())
    assert [(c["years"], c["period"]) for c in changes] == [(1, "2025-07"), (5, "2021-07"), (10, "2016-07")]
    assert changes[0]["price"] == 400000.0 + 1500 * 108
    # A series three years long has no five or ten year change, rather
    # than one taken from its first month.
    assert [c["years"] for c in hpi._changes(_d3_series(37))] == [1]
    assert not hasattr(hpi, "PROJECTION_MONTHS") and not hasattr(hpi, "_linear_regression")

    # The whole service, on a fake SPARQL answer eleven years long: no
    # projection in what it returns, and the series cut to ten years.
    rows = [{"refMonth": {"value": p["period"]}, "label": {"value": "Kingston upon Thames"},
             "averagePrice": {"value": str(p["average_price"])}} for p in _d3_series(133)]

    class _Response:
        def raise_for_status(self): pass
        def json(self): return {"results": {"bindings": rows}}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Response()

    real = hpi.httpx.AsyncClient
    hpi.httpx.AsyncClient = lambda *a, **k: _Client()
    try:
        trend = asyncio.run(hpi.price_trend("Kingston upon Thames"))
    finally:
        hpi.httpx.AsyncClient = real
    assert "projections" not in trend and "monthly_trend" not in trend
    assert trend["series"][0]["period"] == "2016-07" and len(trend["series"]) == 121
    assert [c["years"] for c in trend["changes"]] == [1, 5, 10]
    assert trend["pct_change"] == trend["changes"][1]["pct"]
    chart = app_main._price_trend_chart(trend)
    assert "projected_path" not in chart and "projection_points" not in chart


def test_d3_the_card_is_price_trend_in_every_list_that_names_it():
    titles = [t for _, t, _, _ in app_main.PREMIUM_CHECKS]
    assert "Price Trend" in titles and not any("Forecast" in t for t in titles)
    description = next(d for _, t, d, _ in app_main.PREMIUM_CHECKS if t == "Price Trend")
    assert "Five-year" not in description and "10 years" in description
    assert "five years ·" not in app_main.LOCKED_CARD_LINES["Price Trend"]
    assert "Price Trend & Forecast" not in app_main.LOCKED_CARD_LINES
    for group, sources in app_main.DATA_SOURCE_GROUPS:
        for source in sources:
            assert "forecast" not in source["powers"].lower(), source["name"]
    template = _without_template_comments((ROOT / "app" / "templates" / "property.html").read_text(encoding="utf-8"))
    assert "Forecast" not in template and "forecast</h2>" not in template
    assert "trend-chart-line-projected" not in template and "projection_points" not in template
    assert ".trend-chart-line-projected" not in STYLE_CSS
    js = (ROOT / "browser-extension" / "content.js").read_text(encoding="utf-8")
    assert "Price Trend & Forecast" not in js and '"Price Trend"' in js


def test_d3_the_price_trend_popup_has_no_projected_row_or_line(client, fake_report, monkeypatch):
    from tests.conftest import fake_gather
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    trend = _d3_trend()
    gather = fake_gather(price_trend=trend, price_trend_chart=app_main._price_trend_chart(trend))
    fake_report(location=fake_location(postcode="KT3 4HX", outcode="KT3"), gather=gather)

    # Locked, the card says what the check answers, under its new name.
    body = client.get("/property?postcode=KT3+4HX").text
    assert _b2_line(body, "Price Trend") == _b2_escaped("Price Trend")
    locked = _flat(_b3_modal(body, "modal-price-trend"))
    assert "Projected" not in locked and "projection" not in locked.replace("no projection", "")

    _b3_subscriber(client, "d3-trend@customer.test")
    body = client.get("/property?postcode=KT3+4HX").text
    assert _card_status(body, "Price Trend") == "+" + f"{trend['changes'][1]['pct']:.1f}" + "% over 5 years"
    modal = _b3_modal(body, "modal-price-trend")
    words = _flat(re.sub(r"<[^>]+>", " ", modal))
    assert "<h2>Price trend</h2>" in modal
    assert "Projected" not in words and "projected" not in modal and "stroke-dasharray" not in modal
    assert modal.count("<path") == 1                     # the index's own line, and nothing after it
    for row in ("10 years ago, July 2016", "5 years ago, July 2021", "1 year ago, July 2025", "Now, July 2026"):
        assert row in words, row
    assert f"{trend['changes'][2]['pct']:+.1f}%" in words
    assert "does not reach back" not in words

    # An authority with three years of index behind it (North Yorkshire,
    # Cumberland) has a one-year change and says so, where the card used
    # to call its first month "5 years".
    short = _d3_trend(37)
    fake_report(location=fake_location(postcode="KT3 4HX", outcode="KT3"),
                gather=fake_gather(price_trend=short, price_trend_chart=app_main._price_trend_chart(short)))
    body = client.get("/property?postcode=KT3+4HX").text
    assert _card_status(body, "Price Trend") == f"{short['changes'][0]['pct']:+.1f}% over 1 year"
    words = _flat(re.sub(r"<[^>]+>", " ", _b3_modal(body, "modal-price-trend")))
    assert "5 years ago" not in words and "does not reach back 5 or 10 years from its latest month" in words


def test_d3_the_pdf_carries_the_changes_and_no_projection():
    from tests.test_pdf_report import _running_costs
    report = dict(_full_pdf_report(), price_trend=_d3_trend())
    ctx = app_main._pdf_context(report, _running_costs(), report["location"], "36")
    page = app_main.templates.get_template("pdf_report_full.html").render(ctx)
    part = page.split("<h2>Price trend and area comparison</h2>", 1)[1].split("</table>", 1)[0]
    assert "If the trend holds" not in page and "straight-line projection" not in page
    assert "Change over 10 years" in part and "Change over 1 year" in part and "Now, July 2026" in part
    rows = {r["check"]: r for r in ctx["checklist"]}
    assert "Price trend, five years, Kingston upon Thames" in rows


# (3) One list of things worth checking.

def _d3_york():
    loc = fake_location(postcode="YO1 7HH", outcode="YO1")
    loc.update(admin_district="York", region="Yorkshire and The Humber",
               codes={"admin_district": "E06000014", "lsoa": "E01013400"})
    return loc


def _d3_count(text, tail):
    m = re.search(r"(\d+) things? worth checking" + tail, text)
    assert m, text[:400]
    return int(m.group(1))


def test_d3_the_verdict_and_the_banner_count_one_list_with_a_council_finance_flag(client, fake_report):
    from app.services import council_finance, overview_score
    from tests.conftest import fake_gather
    loc = _d3_york()
    finance = council_finance.for_council("E06000014", "York")
    assert finance and finance["flag"], "York's exceptional support is the case the audit walked into"
    sentence = council_finance.flag_sentence(finance)

    # conftest's deprivation decile of 3 is one flag, the council the
    # other, and a locked check (no buses) a third that a signed-out
    # reader is told about only as a count. The score is computed from the
    # gather, as the real gather does, never written into the fake.
    gather = fake_gather(bus_service={"count": 0, "stops": [], "best": None, "radius_m": 500})
    gather["overview"] = overview_score.compute({**gather, "location": loc}, premium_unlocked=False)
    assert gather["overview"]["premium_extra_checks"] == 1
    fake_report(location=loc, gather=gather)
    body = client.get("/property?postcode=YO1+7HH").text

    verdict = _flat(re.sub(r"<[^>]+>", " ", re.search(r'<p class="overview-score-verdict">(.*?)</p>', body, re.S).group(1)))
    classes, banner = _a5_banner(body)
    assert _d3_count(verdict, r" \(") == _d3_count(banner, " on this property") == 2
    chips = [html.unescape(c) for c in re.findall(r'class="attention-banner-chip" data-modal-target="[^"]+">(.*?)</button>', body)]
    reasons = [html.unescape(r) for r in re.findall(r'class="verdict-reason" data-modal-target="[^"]+">(.*?)</button>', body)]
    # The same two, in the same order, in both places: the verdict's
    # reasons end with its concerns.
    assert chips == [sentence, "Among more deprived areas nationally"]
    assert reasons[-len(chips):] == chips
    assert sentence.startswith("York council needed exceptional government support for ")
    assert "section 114" not in banner and "Few or no scheduled buses" not in body
    assert 'data-modal-target="modal-council-tax">' + html.escape(sentence) in body


def test_d3_the_list_is_the_banners_old_list_with_locked_checks_kept_back():
    from app.services import overview_score
    loc = _d3_york()
    ctx = {
        "location": loc,
        "property_detail": {"year_built": "2012 onwards", "dwelling_type": "Semi-detached house"},
        "flood_zone": {"zone": 2, "label": "Zone 2 (medium probability)"},
        "brownfield": {"covered": True, "count": 2, "dwellings": 144, "hectares": 1.4, "permissioned": 1},
        "bus_service": {"count": 1, "best": {"weekday_day": 6}},
        "health": {"nearest": {"vs_median": 1.5}},
    }
    free = overview_score.attention_items(ctx, premium_unlocked=False)
    assert [i["key"] for i in free] == ["council_finance", "flood"]
    assert free[1]["text"] == "Flood Re insurance not available for this home"
    assert [i["modal"] for i in free] == ["modal-council-tax", "modal-flood"]
    opened = overview_score.attention_items(ctx, premium_unlocked=True)
    assert [i["key"] for i in opened] == ["council_finance", "flood", "brownfield", "bus", "health"]
    score = overview_score.compute(ctx, premium_unlocked=False)
    assert score["concerns"] == [i["text"] for i in free] and score["premium_extra_checks"] == 3
    assert [r["text"] for r in score["reasons"]["concerns"]] == score["concerns"]
    # The locked keys a free reader is only counted for include the three
    # the banner used to hold on its own.
    assert {"brownfield", "bus", "health"} <= overview_score._PREMIUM_ONLY_CONCERNS


def test_d3_the_council_chip_says_what_happened_in_plain_words():
    from app.services import council_finance
    base = {"flag": True, "name": "York UA", "latest_year": "2026-2027", "latest_label": "2026-27",
            "efs": [{"year": "2025-26"}, {"year": "2026-27"}], "efs_current": True,
            "county_efs": [], "county_efs_current": False, "county_name": "", "s114": [], "s114_recent": False}
    assert council_finance.flag_sentence(base) == "York council needed exceptional government support for 2026-27"
    county = dict(base, efs=[], efs_current=False, county_efs=[{"year": "2026-27"}], county_efs_current=True,
                  county_name="Surrey")
    assert council_finance.flag_sentence(county) == "Surrey County Council needed exceptional government support for 2026-27"
    notice = dict(base, name="Birmingham", efs=[{"year": "2025-26"}], efs_current=False,
                  s114=[{"date": "2023-09-05"}, {"date": "2023-09-22"}], s114_recent=True)
    assert council_finance.flag_sentence(notice) == (
        "Birmingham council issued a section 114 notice in September 2023, saying it could not balance its budget")
    assert council_finance.flag_sentence(dict(base, flag=False)) is None
    assert council_finance.flag_sentence(None) is None
    # A name that already says "Council" gets no second one.
    assert council_finance.flag_sentence(dict(base, name="Dorset Council")) == (
        "Dorset Council needed exceptional government support for 2026-27")


# ---- D3, finishing pass: what the first attempt left over ----------------
# The pop-up stated the lower-or-higher rule under a count with nothing to
# compare it against, and its table's caption still read "~1 mile". The
# spans the price trend pop-up says the index does not reach were typed
# into the template; they are now the service's own CHANGE_YEARS.

def test_d3_the_crime_rule_is_stated_only_where_a_comparison_was_made(client, fake_report):
    from tests.conftest import fake_gather
    fake_report(location=fake_location(postcode="KT3 4HX", outcode="KT3"),
                gather=fake_gather(crime={"total": 1834, "month": "2026-07", "by_category": []},
                                   district_crime=None, crime_comparison=[]))
    body = client.get("/property?postcode=KT3+4HX").text
    modal = _flat(re.sub(r"<[^>]+>", " ", _b3_modal(body, "modal-crime")))
    assert "1,834 crimes recorded within about a mile in July 2026." in modal
    assert "Lower or higher is said only" not in modal
    for word in ("About the same as", "Lower than", "Higher than", "too few to compare"):
        assert word not in modal, word
    status, sub = _d3_crime_card(body)
    assert status == "1,834 within about a mile, July 2026" and sub is None

    # With a comparison, the table's caption gives the radius in words.
    rows = [{"category": "burglary", "here": 30, "area": 60, "trend": "lower"}]
    fake_report(location=fake_location(postcode="KT3 4HX", outcode="KT3"),
                gather=fake_gather(crime={"total": 229, "month": "2026-07", "by_category": []},
                                   district_crime={"total": 300, "month": "2026-07", "by_category": []},
                                   crime_comparison=rows))
    modal = _flat(re.sub(r"<[^>]+>", " ", _b3_modal(client.get("/property?postcode=KT3+4HX").text, "modal-crime")))
    assert "Lower or higher is said only" in modal
    assert "The same radius of about a mile, centred on the KT3 postcode area instead." in modal
    assert "~1 mile" not in modal


def test_d3_the_spans_the_index_does_not_reach_come_from_the_service():
    from app.services import hpi
    assert app_main.templates.env.globals["price_trend_change_years"] == hpi.CHANGE_YEARS == (1, 5, 10)
    template = _without_template_comments((ROOT / "app" / "templates" / "property.html").read_text(encoding="utf-8"))
    assert "[1, 5, 10]" not in template and "price_trend_change_years" in template


# ---- D4. The questions come from the report's findings, and come first ---
# KT3 4HX said "4 questions were generated for this property", three of
# them fixed for every purchase, while findings the page itself shows
# raised none: listed buildings 9 yards away, Likely school readings, an
# EPC D with a cost to reach C, a council under exceptional support, a
# check that could not run. The section came after all six groups. Those
# findings now raise questions, a free card's in full for every reader
# and a locked check's counted and never shown; the section sits under the
# banner; and the viewing checklist prints the same list
# (solicitor_questions.for_reader, built once by main._buyer_questions).

D4_SCHOOL = "Riverside Primary School"
D4_LISTED = {"name": "Minster Yard Cottages", "grade": "II",
             "url": "https://historicengland.org.uk/", "distance_m": 8}
D4_QUESTIONS = (
    "Ask your solicitor whether the home is listed or within the curtilage of a listed building.",
    f"Check the distance {D4_SCHOOL} last offered places to in the council's allocation figures before relying on it.",
    "Ask the seller which of the certificate's recommended improvements have been done.",
)
D4_TRIGGERS = (
    "Listed building 9 yards away: Minster Yard Cottages, Grade II",
    f"School places: Likely for {D4_SCHOOL}",
    "EPC Band D, £8,400 to £12,200 to reach C",
)
D4_COAL_FAILED = "Ask your solicitor whether a CON29M coal mining search is needed, because the online check could not run."


def _d4_plan():
    """The certificate's own recommendation report, as epc.improvement_plan
    reads it: two measures, Band C after the second."""
    steps = [
        {"sequence": 1, "number": "6", "name": "Cavity wall insulation", "description": "",
         "cost_text": "£4,000 - £7,000", "cost_low": 4000, "cost_high": 7000, "saving": 180,
         "rating_after": 64, "band_after": "D"},
        {"sequence": 2, "number": "W1", "name": "Floor insulation", "description": "",
         "cost_text": "£4,400 - £5,200", "cost_low": 4400, "cost_high": 5200, "saving": 130,
         "rating_after": 70, "band_after": "C"},
    ]
    total = {"count": 2, "cost_low": 8400, "cost_high": 12200, "priced": 2, "saving": 310,
             "rating_after": 70, "band_after": "C"}
    return {"steps": steps, "to_c": total, "already_c": False, "all": total}


def _d4_gather(**overrides):
    """conftest's report with a listed building 9 yards from the postcode's
    centre, a primary school the address reads Likely for, and an EPC D
    with the certificate's £8,400 to £12,200 to reach C. conftest marks
    the Coal Authority check, among others, as failed."""
    from tests.conftest import fake_gather
    base = fake_gather()
    detail = dict(base["property_detail"], current_band="D", current_score=60, potential_band="C",
                  potential_score=74, improvements=_d4_plan())
    landscape = dict(base["school_landscape"], all_schools=[
        {"urn": 990401, "name": D4_SCHOOL, "distance_m": 480, "phase_group": "Primary",
         "admission_radius": {"last_distance_miles": 0.9, "academic_year": "2025/26"}},
    ])
    certificates = [dict(base["certificates"][0], rating="D")]
    fields = dict(heritage=[D4_LISTED, *base["heritage"]], school_landscape=landscape,
                  property_detail=detail, certificates=certificates)
    fields.update(overrides)
    return fake_gather(**fields)


def _d4_section(body):
    """The "Before you offer" section as a reader sees its words."""
    return html.unescape(_b3_questions(body))


def _d4_report_questions(body):
    section = html.unescape(body).split('id="buyer-questions"', 1)[1].split("</section>", 1)[0]
    return [_flat(q) for q in re.findall(r'<p class="bq-question">(.*?)</p>', section, re.S)]


def _d4_checklist_questions(body):
    # A span since 18 Sep 2026 (F2): the question is the words of a
    # checkbox's label now, and a label holds no paragraphs.
    return [_flat(q) for q in re.findall(r'<span class="checklist-heading checklist-question">(.*?)</span>',
                                         html.unescape(body), re.S)]


def test_d4_a_signed_out_reader_is_asked_about_the_listed_building_the_school_and_the_epc(client, fake_report):
    fake_report(gather=_d4_gather())
    body = client.get("/property?postcode=M14+5TG&house_number=41").text
    section = _d4_section(body)

    # Each one in full, with the finding that raised it, from a free card.
    for question in D4_QUESTIONS:
        assert question in section, question
    for trigger in D4_TRIGGERS:
        assert trigger in section, trigger
    assert 'class="bq-locked"' not in section  # nothing here came from a locked check

    # The figures are the cards' own: the listed buildings table rounds 8 m
    # to 9 yd, and the energy card gives the same cost to reach C.
    assert "9 yd" in _b3_modal(body, "modal-heritage")
    assert "£8,400 to £12,200 to reach Band C" in html.unescape(body)

    # The count says how many are this home's and how many every purchase gets.
    total = int(re.search(r"<strong>(\d+) questions</strong>", section).group(1))
    found = int(re.search(r"were generated for this property\. (\d+) come from what this report found, "
                          r"and 3 are asked on every purchase\.", _flat(section)).group(1))
    assert total == found + 3 == len(_d4_report_questions(body))
    # The three asked on every purchase fold away under their own summary.
    every = section.split('<details class="bq-every">', 1)[1].split("</details>", 1)[0]
    assert "The 3 asked on every purchase" in every
    assert every.count('class="bq-item"') == 3 and "Confirm the tenure." in every


def test_d4_the_section_sits_under_the_banner_and_before_the_first_group(client, fake_report):
    fake_report(gather=_d4_gather())
    body = client.get("/property?postcode=M14+5TG&house_number=41").text
    assert body.count('id="buyer-questions"') == 1  # moved, not copied, and the anchor kept
    banner = body.index('class="attention-banner')
    questions = body.index('<section class="report-section buyer-questions"')
    groups = body.index('id="report-categories"')
    first_group = body.index('<h3 class="dashboard-category-heading">Value &amp; Market</h3>')
    assert banner < questions < groups < first_group
    # Directly after the banner: every block of the report's top carries
    # data-animate, and the banner's is the only one before the section.
    assert body[banner:questions].count("data-animate") == 1
    assert body[banner:questions].rstrip().endswith("</div>")
    # And nothing of it is left at the foot of the report.
    assert "Before you offer" not in body[groups:]


def test_d4_the_viewing_checklist_prints_the_reports_own_questions(client, fake_report):
    fake_report(gather=_d4_gather())
    report = client.get("/property?postcode=M14+5TG&house_number=41").text
    checklist = client.get("/property/checklist?postcode=M14+5TG&house_number=41").text

    # The same questions in the same order, so the two never disagree.
    on_report = _d4_report_questions(report)
    assert _d4_checklist_questions(checklist) == on_report
    for question in D4_QUESTIONS + (D4_COAL_FAILED,):
        assert question in on_report, question
    # What to look at in the room stays the checklist's own.
    assert "Water pressure" in checklist and "Questions to ask" in checklist

    # Built by one function for one reader, not two lists kept in step:
    # the checklist module left to itself builds the report's list too.
    from app.services import solicitor_questions, viewing_checklist
    context = {"location": fake_location(), "house_number": "41", **_d4_gather()}
    context["school_verdicts"] = app_main._school_verdict_summary(context["school_landscape"])
    for opened in (False, True):
        assert (viewing_checklist.build(context, premium_unlocked=opened)["questions"]
                == solicitor_questions.for_reader(context, opened)
                == app_main._buyer_questions(dict(context), opened))


def test_d4_a_locked_checks_question_is_counted_and_never_shown_on_either(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    gather = _d4_gather(coal_mining={"present": True, "area_name": None})
    fake_report(gather=gather)
    body = client.get("/property?postcode=M14+5TG&house_number=43").text
    section = _d4_section(body)
    checklist = html.unescape(client.get("/property/checklist?postcode=M14+5TG&house_number=43").text)

    for page in (section, checklist):
        assert "Order a CON29M coal mining search." not in page
        assert "Coal Mining Reporting Area" not in page
    assert "1 more</strong> came from checks that open with a full report." in _flat(section)
    assert "1 more came from checks that open with a full report" in _flat(checklist)
    # The free cards' questions are still there in full beside the count.
    for question in D4_QUESTIONS:
        assert question in section

    # A subscriber reads it on both, and the count the locked page gave
    # was every question.
    counted = int(re.search(r"<strong>(\d+) questions</strong>", section).group(1))
    _b3_subscriber(client, "d4-questions@customer.test")
    body = client.get("/property?postcode=M14+5TG&house_number=43").text
    full = _d4_report_questions(body)
    assert "Order a CON29M coal mining search." in full and len(full) == counted
    assert 'class="bq-locked"' not in _d4_section(body)
    checklist = client.get("/property/checklist?postcode=M14+5TG&house_number=43").text
    assert _d4_checklist_questions(checklist) == full


def test_d4_a_check_that_could_not_run_asks_for_the_search_instead(client, fake_report):
    # conftest marks the Coal Authority check as failed. The locked Mining
    # Risk card says so to a signed-out reader already, so the question,
    # which says no more than the card, is theirs too.
    fake_report(gather=_d4_gather())
    body = client.get("/property?postcode=M14+5TG&house_number=41").text
    section = _d4_section(body)
    assert D4_COAL_FAILED in section
    assert "Coal mining could not be checked just now · around £40" in _flat(section)
    assert "Could not be checked just now · Mining Remediation Authority" in html.unescape(body)

    # A check that answered raises no such question.
    fake_report(gather=_d4_gather(coal_mining={"present": False, "area_name": None}))
    section = _d4_section(client.get("/property?postcode=M14+5TG&house_number=41").text)
    assert D4_COAL_FAILED not in section and "Coal mining could not be checked" not in section


def test_d4_a_council_under_exceptional_support_asks_for_two_years_of_bills(client, fake_report):
    from app.services import council_finance
    fake_report(location=_d3_york(), gather=_d4_gather())
    section = _d4_section(client.get("/property?postcode=YO1+7HH&house_number=41").text)
    sentence = council_finance.flag_sentence(council_finance.for_council("E06000014", "York"))
    assert "Ask the seller what the council tax bill was this year and last." in section
    assert sentence in section  # the trigger is the banner's own chip


def test_d4_the_triggers_hold_to_their_thresholds():
    from app.services import solicitor_questions as sq

    def asked(**context):
        return {q["question"]: q for q in sq.build(context)}

    listed, school, epc = D4_QUESTIONS
    # A listed building within about 25 metres, and not beyond.
    assert listed in asked(heritage=[dict(D4_LISTED, distance_m=25)])
    assert listed not in asked(heritage=[dict(D4_LISTED, distance_m=26)])
    # The nearest Likely or Borderline reading, named; an Unlikely one asks nothing.
    rows = [{"name": "Far Academy", "level": "unlikely", "label": "Unlikely", "kind": "published"},
            {"name": D4_SCHOOL, "level": "borderline", "label": "Borderline", "kind": "estimated"}]
    q = asked(school_verdicts={"schools": rows})[school]
    assert q["trigger"] == f"School places: Borderline for {D4_SCHOOL} (estimated distance)"
    assert "modelled estimate" in q["why"] and q["check"] == ""
    assert school not in asked(school_verdicts={"schools": rows[:1]})
    # An EPC of D to G with the certificate's cost to reach C; C asks nothing.
    detail = {"current_band": "D", "improvements": _d4_plan()}
    assert asked(property_detail=detail, house_number="41")[epc]["trigger"] == D4_TRIGGERS[2]
    # Without a house number the certificate is the postcode's newest.
    q = asked(property_detail=detail)[epc]
    assert q["trigger"] == "EPC Band D on the newest certificate at this postcode, £8,400 to £12,200 to reach C"
    assert "first check the certificate is for the home you are buying" in q["why"]
    assert epc not in asked(property_detail=dict(detail, current_band="C"))
    assert epc not in asked(property_detail={"current_band": "E", "improvements": dict(_d4_plan(), to_c=None)})
    # Every failed-check question is a free one: a failure is not a finding.
    failed = sq.build({"coal_mining_error": True, "flood_zone_error": True, "historic_landfill_error": True,
                       "heritage_error": True, "designations_error": True})
    assert len([x for x in failed if x["trigger"].endswith("could not be checked just now")]) == 5
    assert sq.without_locked(failed) == failed
    # Outside England's flood maps nothing failed, so nothing is asked.
    assert not any("flood zone" in x["question"] for x in sq.build({"flood_zone_error": True, "flood_not_covered": {"country": "Wales"}}))


def test_d4_for_reader_counts_what_it_holds_back():
    from app.services import solicitor_questions as sq
    context = {"coal_mining": {"present": True}, "heritage": [D4_LISTED]}
    locked = sq.for_reader(context, premium_unlocked=False)
    opened = sq.for_reader(context, premium_unlocked=True)
    assert locked["total"] == opened["total"] == 5
    assert locked["from_findings"] == opened["from_findings"] == 2
    assert locked["locked"] == 1 and opened["locked"] == 0
    assert len(locked["every_purchase"]) == len(opened["every_purchase"]) == 3
    shown = [q["question"] for _, qs in locked["found"] for q in qs]
    assert shown == [D4_QUESTIONS[0]]
    assert "Order a CON29M coal mining search." in [q["question"] for _, qs in opened["found"] for q in qs]


def test_d4_the_pdf_describes_the_home_its_house_number_chose():
    location = fake_location()
    for house_number, own in (("41", True), ("", False)):
        context = app_main._pdf_context(_d4_gather(), None, location, house_number)
        triggers = [q["trigger"] for _, qs in context["buyer_questions"] for q in qs]
        assert (D4_TRIGGERS[2] in triggers) is own
        assert any(t.startswith("EPC Band D on the newest certificate at this postcode") for t in triggers) is not own


# ---- D5. The wait page gets an ending, and comparables gets a cache -------
# The wait page asked every 900 ms with no limit and, once every source was
# in, said "All sources back, putting the report together" for ever. A
# gather over the cache's 4 MB entry limit was never stored, so the page
# would restart the build every 30 seconds for as long as it stayed open,
# and a build queued behind both slots read as dead after 30 seconds too.
# The report's gather is now kept whatever its size (and a refusal of
# anything else is logged); a queued build keeps its claim fresh; and the
# page names what is missing after 30 seconds, offers "Try again" and the
# district's guide after 60, and stops asking after five minutes. The
# Comparables page, 2.61 s and 2.26 s on consecutive requests for KT3 4HX,
# caches its rows and borrows the report's sales when it has them.

D5_BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                            "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"}


def test_d5_a_gather_over_the_entry_limit_is_kept_and_the_wait_page_is_told_it_is_ready(client, monkeypatch, caplog):
    """The real gather, every member failed so nothing reaches the network,
    against an entry limit smaller than its result."""
    import logging

    from fastapi.responses import HTMLResponse

    from app.services import _cache

    async def _failed(name, coro):
        close = getattr(coro, "close", None)
        if close:
            close()  # never awaited, so never sent
        return RuntimeError(f"{name} down")

    async def _report(request, postcode, house_number, _share=None):
        return HTMLResponse("the finished report")

    location = fake_location(postcode="M34 9ZX", outcode="M34")
    key = ("property_search_gather", "M34 9ZX", "5")

    async def _lookup(_postcode):
        return location

    monkeypatch.setattr(_cache, "MAX_ENTRY_BYTES", 200)
    monkeypatch.setattr(app_main, "_timed", _failed)
    monkeypatch.setattr(app_main, "_bounded", lambda coro, seconds: coro)
    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "_render_property", _report)
    _cache._evict(key)
    try:
        asyncio.run(app_main._full_property_gather(location, "5", premium_unlocked=False))
        assert _cache._store[key][2] > _cache.MAX_ENTRY_BYTES, "the fixture must be over the limit"
        assert _cache.get(key, app_main.PROPERTY_SEARCH_CACHE_TTL_S) is not None

        # The endpoint the page polls, and the page it then reloads.
        assert client.get("/api/report-ready?postcode=M34+9ZX&house_number=5").json()["ready"] is True
        r = client.get("/property?postcode=M34+9ZX&house_number=5", headers=D5_BROWSER)
        assert r.status_code == 200 and r.text == "the finished report"
    finally:
        _cache._evict(key)
        app_main._gather_progress.pop(("M34 9ZX", "5"), None)

    # Any other value over the limit is still refused, and now says so.
    with caplog.at_level(logging.WARNING):
        _cache.set(("d5_page", "M34"), ["x" * 50 for _ in range(20)])
    assert _cache.get(("d5_page", "M34"), 60) is None
    assert any("not keeping d5_page:M34" in rec.getMessage() for rec in caplog.records)


def test_d5_a_build_queued_behind_both_slots_is_not_taken_for_dead(monkeypatch):
    import time

    location = fake_location(postcode="M34 9ZW", outcode="M34")
    ran = []

    async def _gather(location, house_number, premium_unlocked):
        ran.append(house_number)

    monkeypatch.setattr(app_main, "_full_property_gather", _gather)
    monkeypatch.setattr(app_main, "_release_memory", lambda: None)
    monkeypatch.setattr(app_main, "_QUEUED_TOUCH_S", 0.02)

    async def go():
        # A semaphore of this loop's own, so the app's is never bound to it.
        slots = asyncio.Semaphore(2)
        monkeypatch.setattr(app_main, "_GATHER_CONCURRENCY", slots)
        await slots.acquire()
        await slots.acquire()
        task = app_main._spawn_gather(location, "")
        claim = app_main._gather_progress[("M34 9ZW", "")]
        claim["touched"] = time.time() - 10 * app_main.STALLED_GATHER_S
        await asyncio.sleep(0.15)
        assert time.time() - claim["touched"] < app_main.STALLED_GATHER_S, "a queued claim must stay fresh"
        # So the next poll leaves it alone rather than queueing a second build.
        assert app_main._spawn_gather(location, "") is None
        assert ran == []
        slots.release()
        await task
        assert ran == [""]
        # Once it has its slot the stamping stops.
        claim["touched"] = 0
        await asyncio.sleep(0.1)
        assert claim["touched"] == 0

    try:
        asyncio.run(go())
    finally:
        app_main._gather_progress.pop(("M34 9ZW", ""), None)


def _d5_css_rule(css, selector):
    return re.search(r"(?m)^" + re.escape(selector) + r" \{(.*?)\}", css, re.S).group(1)


def test_d5_the_wait_page_names_what_is_missing_then_offers_a_way_out_then_stops(client, fake_report):
    fake_report()
    r = client.get("/property?postcode=M14%205TG", headers=D5_BROWSER)
    assert r.status_code == 202
    body = r.text

    # The three times, from when the page opened.
    assert "var NAME_MISSING_AFTER_MS = 30000;" in body
    assert "var OFFER_EXIT_AFTER_MS = 60000;" in body
    assert "var STOP_AFTER_MS = 300000;" in body
    assert "if (waited >= NAME_MISSING_AFTER_MS) nameMissing();" in body
    assert "if (waited >= OFFER_EXIT_AFTER_MS) helpEl.hidden = false;" in body
    # 30 s: the sources the endpoint has not reported back, by name.
    assert "return !seen[src];" in body and "'Still waiting on: ' + listed(missing)" in body
    assert re.search(r'<p class="building-waiting" id="building-waiting" aria-live="polite" hidden></p>', body)
    # 60 s: a way to try again, and the district's guide.
    help_block = re.search(r'<div class="building-help" id="building-help" aria-live="polite" hidden>(.*?)\n    </div>',
                           body, re.S).group(1)
    assert '<button type="button" class="building-retry" id="building-retry">Try again</button>' in help_block
    assert '<a class="building-guide" href="/area/M14">Read the M14 area guide</a>' in help_block
    assert "retryEl.addEventListener('click', function () { window.location.reload(); });" in body
    # Five minutes: no more asking, and a plain sentence saying so.
    assert re.search(r"function poll\(\) \{\s*if \(Date\.now\(\) - opened >= STOP_AFTER_MS\) \{ stop\(\); return; \}", body)
    stopped = re.search(r'<p class="building-help-text" id="building-stopped" hidden>(.*?)</p>', body).group(1)
    assert stopped == "This page has stopped checking after five minutes. Try again now, or come back in a few minutes."
    for words in (stopped, "This is taking longer than usual.", "Still waiting on: "):
        assert "—" not in words and "!" not in words
    # The dial is untouched: a mark per source, as before.
    assert len(re.findall(r'<rect class="dial-mark"', body)) == len(app_main.GATHER_SOURCE_ORDER)


def test_d5_the_way_out_links_only_a_district_that_has_a_guide(client, fake_report):
    fake_report(location=fake_location(postcode="ZZ9 9ZZ", outcode="ZZ9"))
    body = client.get("/property?postcode=ZZ9+9ZZ", headers=D5_BROWSER).text
    assert 'id="building-retry">Try again</button>' in body
    assert 'class="building-guide"' not in body and 'href="/area/ZZ9"' not in body


def test_d5_the_ending_appears_without_motion_and_the_dial_is_unchanged():
    """Nothing added moves, so reduced motion has nothing to hold still;
    the dial's own rules and their reduced-motion block are as they were."""
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    for selector in (".building-overtime", ".building-waiting", ".building-help", ".building-help-text",
                     ".building-help-actions", ".building-retry", ".building-guide"):
        rule = _d5_css_rule(css, selector)
        assert "animation" not in rule and "transition" not in rule, selector
    assert "animation: dial-wave 2.85s ease-in-out infinite;" in _d5_css_rule(css, ".dial-mark")
    assert ".dial-mark, .dial-mark.is-done, .dial-lens, .building-latest.is-fresh { animation: none; }" in css


def _d5_comparables(monkeypatch, calls, sold_fails=False):
    location = fake_location()

    async def _lookup(_postcode):
        return location

    async def _nearby(lat, lon, **kwargs):
        calls.append("nearby")
        return [{"postcode": "M14 5TG", "distance_m": 0, "latitude": 53.45, "longitude": -2.22},
                {"postcode": "M14 5TH", "distance_m": 400, "latitude": 53.452, "longitude": -2.221}]

    async def _sold(postcodes):
        calls.append("sold")
        if sold_fails:
            raise RuntimeError("Land Registry down")
        return [
            {"address": "3 Far Street", "postcode": "M14 5TH", "amount": "300000", "date": "2025-02-01",
             "property_type": "terraced", "tenure": "Freehold", "new_build": False},
            {"address": "1 Test Street", "postcode": "M14 5TG", "amount": "250000", "date": "2024-06-01",
             "property_type": "flat", "tenure": "Leasehold", "new_build": False},
        ]

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "nearby_postcodes", _nearby)
    monkeypatch.setattr(app_main, "sold_prices_for_postcodes", _sold)
    return location, _sold


def test_d5_two_comparables_requests_fetch_the_nearby_sales_once(client, monkeypatch):
    from app.services import _cache

    calls = []
    _d5_comparables(monkeypatch, calls, sold_fails=True)
    # A failed fetch is not kept: the page says so, and the next asks again.
    first = client.get("/property/comparables?postcode=M14%205TG")
    assert "The comparables data service didn't respond" in first.text
    assert calls == ["nearby", "sold"]

    calls.clear()
    _d5_comparables(monkeypatch, calls)
    one = client.get("/property/comparables?postcode=M14%205TG")
    two = client.get("/property/comparables?postcode=M14%205TG&house_number=9")
    assert one.status_code == two.status_code == 200
    assert calls == ["nearby", "sold"], "the second request must be served from the cache"
    assert _cache.get(app_main._comparables_page_key(53.45, -2.22), app_main.COMPARABLES_CACHE_TTL_S) is not None
    # Nearest first, as the page always listed them.
    for body in (one.text, two.text):
        assert body.index('data-date="2024-06-01"') < body.index('data-date="2025-02-01"')
    # What depends on the house number is still worked out per request:
    # the postcode's own last sale places it without one, and no home
    # numbered 9 has sold, so that page places nothing.
    assert "sits above 0% of the 2 nearby sold prices" in _flat(one.text)
    assert "sits above" not in two.text


def test_d5_comparables_borrows_the_reports_sales_and_reads_exactly_the_same(client, monkeypatch):
    from app.services import _cache

    calls = []
    _, sold = _d5_comparables(monkeypatch, calls)
    fresh = client.get("/property/comparables?postcode=M14%205TG").text
    assert calls == ["nearby", "sold"]

    # The report's own copy: the same rows with their distance and the
    # floor area its estimate looked up.
    report_rows = [dict(tx, distance_m=0 if tx["postcode"] == "M14 5TG" else 400, floor_area=61)
                   for tx in asyncio.run(sold([]))]
    _cache._evict(app_main._comparables_page_key(53.45, -2.22))
    _cache.set(app_main._comparables_key(53.45, -2.22), report_rows)
    calls.clear()
    borrowed = client.get("/property/comparables?postcode=M14%205TG").text
    assert calls == ["nearby"], "Land Registry is not asked again for sales the report holds"
    assert borrowed == fresh
    assert "floor_area" not in borrowed
    assert all("latitude" not in tx and tx["floor_area"] == 61 for tx in report_rows), \
        "the report's cached rows are never changed"


# ---- D6. Numbers and claims that disagree across pages --------------------
# The homepage said "13 official sources", typed, with OpenStreetMap among
# them; the wait page counted "0 of 19 sources back"; /methodology listed
# 14 bodies and left out ones Premium names. /methodology called the VOA's
# banding list a paid product beside its own timed "VOA band lookup", and
# listed brownfield land as something the site cannot show while Premium's
# Development Nearby shows register sites. /areas promised every guide a
# flood zone and "Band D council tax from MHCLG". The market report called
# Nottinghamshire and Aberdeenshire "major UK cities", because the index's
# area match took the shortest label containing the name, and its title
# carried the day it was built over July's figures.

def _d6_names(bodies):
    return [body["name"] for body in bodies]


def _d6_text(body):
    """What a reader sees: inline tags go without a trace, so a link
    followed by a comma reads as it does on the page."""
    body = re.sub(r"</?(?:a|strong|em|b|i|span)\b[^>]*>", "", body)
    return _flat(html.unescape(re.sub(r"<[^>]+>", " ", body)))


_D6_BLOCK_TAGS = ("address|article|aside|blockquote|br|button|caption|dd|details|dialog|div|dl|dt|fieldset|"
                  "figcaption|figure|footer|form|h1|h2|h3|h4|h5|h6|header|hr|label|legend|li|main|nav|ol|"
                  "option|p|pre|section|select|summary|table|tbody|td|textarea|tfoot|th|thead|tr|ul")


def _d6_blocks(body):
    """A page's visible text a block at a time, the way
    scripts/audit_site.py reads it."""
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    body = re.sub(rf"</?(?:{_D6_BLOCK_TAGS})\b[^>]*>", "\x00", body, flags=re.I)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    return [block for block in body.split("\x00") if block.strip()]


def test_d6_one_list_of_publishing_bodies_is_read_from_the_check_lists():
    sources = {source for _, _, _, source in app_main.FREE_CHECKS + app_main.PREMIUM_CHECKS}
    # Every source a check names maps to the bodies that publish it, so a
    # new check cannot bring a body the count misses.
    assert sources <= set(app_main._SOURCE_BODIES), sources - set(app_main._SOURCE_BODIES)
    named = {body for source in sources for body in app_main._SOURCE_BODIES[source]}
    assert named <= set(app_main._BODY_DETAILS), named - set(app_main._BODY_DETAILS)

    official = _d6_names(app_main.OFFICIAL_SOURCES)
    assert len(official) == len(set(official)), "a body read by several checks is counted once"
    assert _d6_names(app_main.OPEN_DATA_SOURCES) == ["OpenStreetMap"] and "OpenStreetMap" not in official
    assert set(official) == named - {"OpenStreetMap"}
    # The bodies the audit found Premium naming and /methodology leaving out.
    for body in ("Mining Remediation Authority", "NHS England", "Department for Transport", "MHCLG",
                 "HMRC", "Bank of England", "Defra", "National Rail"):
        assert body in official, body

    # A source naming two bodies counts both, each with the check it serves.
    by_name = {b["name"]: b for b in app_main.OFFICIAL_SOURCES + app_main.OPEN_DATA_SOURCES}
    for body in ("Natural England", "Historic England"):
        assert "Planning Constraints" in by_name[body]["checks"], body
    for body in ("MHCLG", "Welsh Government", "Scottish Government"):
        assert "Council Tax" in by_name[body]["checks"], body
    assert by_name["NHS England"]["checks"] == ("Health Services",)
    assert set(by_name["OpenStreetMap"]["checks"]) == {"Nearby Essentials", "Aspect", "Getting Around"}
    assert all(b["url"].startswith("https://") for b in by_name.values())
    assert app_main.templates.env.globals["official_sources"] is app_main.OFFICIAL_SOURCES
    assert app_main.templates.env.globals["open_data_sources"] is app_main.OPEN_DATA_SOURCES


def test_d6_the_homepage_gives_the_lists_length_wherever_it_counts_sources(client):
    body = _fresh_home(client)
    n = len(app_main.OFFICIAL_SOURCES)
    stat = re.search(r'data-target="(\d+)"[^>]*>(\d+)</span></p>\s*<p class="lx-about-stat-l">Official sources', body)
    assert stat and stat.groups() == (str(n), str(n))
    assert f"{app_main.CHECK_COUNT} checks &middot; {n} official sources &middot; no card needed" in body

    # The strip is the list. It was the list twice, a belt looping at a
    # pace set by its length, until item E1 of 18 Sep 2026 stopped it:
    # now each body appears once, in a still row (pinned in the E1 tests).
    strip = body[body.index('class="sources-strip"'):]
    strip = strip[:strip.index("</section>")]
    names = [html.unescape(name) for name in re.findall(r'class="sources-strip-name">([^<]+)<', strip)]
    assert names == _d6_names(app_main.OFFICIAL_SOURCES)

    # No other count of sources or bodies anywhere on the page.
    counts = set(re.findall(r"(\d+) official (?:sources|bodies)", _d6_text(body), re.I))
    assert counts == {str(n)}, counts

    # The FAQ answer counts the same list and names OpenStreetMap apart,
    # on the page and in its structured data alike.
    visible, structured = _home_faq(body)
    answer = dict(visible)["Where does the data come from?"]
    assert answer.startswith(f"{n} official bodies, shown below, among them HM Land Registry")
    assert "OpenStreetMap, the map its volunteers draw" in answer and " only" not in answer
    assert "the EPC Register" not in answer
    assert dict(structured)["Where does the data come from?"] == answer
    description = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', body).group(1))
    assert "entirely from official" not in description
    assert description.endswith("One free report from official government data, every figure naming its source.")


def test_d6_methodology_lists_every_body_the_homepage_counts_and_keeps_openstreetmap_apart(client):
    body = client.get("/methodology").text
    n = len(app_main.OFFICIAL_SOURCES)
    section = body[body.index('<section class="landing-section" id="sources">'):]
    section = section[:section.index("</section>")]
    rows = re.findall(r'<tr><td><a href="([^"]+)" target="_blank" rel="noopener">([^<]+)</a></td><td>([^<]*)</td></tr>',
                      section)
    assert [html.unescape(name) for _, name, _ in rows] == [b["full_name"] for b in app_main.OFFICIAL_SOURCES]
    assert len(rows) == n and f"{n} official bodies publish what the checks read." in section
    assert ("https://www.england.nhs.uk", "NHS England", "Health Services") in rows
    defra = next(r for r in rows if "Rural Affairs" in r[1])
    assert html.unescape(defra[1]) == "Department for Environment, Food & Rural Affairs"
    assert html.unescape(defra[2]) == "Noise and Air Quality"

    table = section[section.index("<table"):section.index("</table>")]
    assert "OpenStreetMap" not in table
    assert ("Not an official body, and not counted above: OpenStreetMap, the open map its volunteers draw, "
            "read for Nearby Essentials, Aspect and Getting Around.") in _d6_text(section)

    dek = _d6_text(re.search(r'<p class="dek">(.*?)</p>', body, re.S).group(1))
    assert "Every figure has a named official source" not in dek
    assert "apart from what is nearby and which way a home faces, which come from OpenStreetMap" in dek


def test_d6_the_wait_page_counts_lookups_not_sources(client, fake_report):
    fake_report()
    r = client.get("/property?postcode=M14%205TG&house_number=606", headers=D5_BROWSER)
    assert r.status_code == 202
    n = len(app_main.GATHER_SOURCE_ORDER)
    assert f'<span id="building-done">0</span> of {n} lookups back</p>' in r.text
    assert "Waiting for the first lookup to come back" in r.text
    assert "countEl.textContent = 'All lookups back, putting the report together';" in r.text
    assert "sources back" not in r.text
    # Several lookups go to one body, which is why the two counts differ.
    assert n != len(app_main.OFFICIAL_SOURCES)


def test_d6_other_pages_give_the_same_count_and_never_say_official_sources_only(client):
    n = len(app_main.OFFICIAL_SOURCES)
    premium = _d6_text(client.get("/premium").text)
    assert (f"Named official bodies, {n} of them, each listed with the checks it is read for on the "
            "methodology page") in premium
    assert "the stations nearby also read OpenStreetMap, the map its volunteers draw, and say so" in premium

    data = client.get("/data").text
    rule = _d6_text(re.search(r"<li><strong>Official sources, and one open map\.</strong>(.*?)</li>", data, re.S).group(1))
    assert rule == ("Every figure traces to a named government or public body, apart from what is nearby and which "
                    f"way a home faces, which come from OpenStreetMap and say so. The {n} bodies are listed with the "
                    "checks each is read for. Nothing is scraped from listings sites.")
    assert '<a href="/methodology#sources">' in data

    llms = client.get("/llms.txt").text
    assert f"from {n} official bodies, with OpenStreetMap for what is nearby:" in llms

    pages = {"/": _fresh_home(client), "/premium": client.get("/premium").text, "/data": data,
             "/methodology": client.get("/methodology").text, "/llms.txt": llms}
    for path, text in pages.items():
        assert "official sources only" not in _d6_text(text).lower(), path


def test_d6_the_audit_holds_every_count_of_sources_to_the_homepages(client):
    from tests.test_brainstorm_18sep import _copy_rules
    rules = _copy_rules()
    n = str(len(app_main.OFFICIAL_SOURCES))
    home = _fresh_home(client)
    methodology = client.get("/methodology").text

    # The two figures the audit reads, and what it does without them.
    assert rules.headline_source_count(home) == n
    assert rules.listed_source_count(methodology) == int(n)
    assert rules.headline_source_count("<p>No figure here</p>") == ""
    assert rules.listed_source_count("<p>No table here</p>") is None

    found = rules.source_count_problems
    assert found("Built on 13 official sources.", n) == ["13 official sources"]
    assert found("0 of 19 sources back", n) == ["19 sources"]
    assert found("from 14 official data sources", n) == ["14 official data sources"]
    # The headline itself, small per-card counts, a rival's figure in
    # quotation marks, and a rule with no headline to hold to.
    for fine in (f"{n} official sources", f"{n} sources", "two sources", "3 sources",
                 '"47 risk checks" from "14 official data sources"', "from “14 official data sources”"):
        assert found(fine, n) == [], fine
    assert found("13 official sources", "") == []

    # The pages as rendered today pass it, /alternatives's quote included.
    for path in ("/premium", "/data", "/alternatives", "/areas"):
        for block in _d6_blocks(client.get(path).text):
            assert found(block, n) == [], (path, block)
    for path, body in (("/", home), ("/methodology", methodology)):
        for block in _d6_blocks(body):
            assert found(block, n) == [], (path, block)

    # And the audit runs it, beside the check count rule.
    audit = (ROOT / "scripts" / "audit_site.py").read_text(encoding="utf-8")
    assert "HEADLINE_SOURCES = headline_source_count(_home)" in audit
    assert 'listed_source_count(pages_html.get("/methodology", ""))' in audit
    assert "source_count_problems(block, HEADLINE_SOURCES)" in audit
    assert re.search(r'"/data", "/methodology"', audit), "the audit fetches /methodology"


def test_d6_methodology_says_precisely_what_is_still_not_shown(client):
    body = client.get("/methodology").text
    start = body.index("What we deliberately don't show")
    section = _d6_text(body[start:body.index("</section>", start)])
    # Development Nearby shows brownfield register sites, so brownfield
    # land is no longer among what the site cannot show.
    assert "Brownfield" not in section and "brownfield" not in section
    assert any("Brownfield" in what for _, title, what, _ in app_main.PREMIUM_CHECKS if title == "Development Nearby")
    # A home's band: free to look up one address at a time, with no free
    # bulk data, which agrees with the timed "VOA band lookup" below it.
    assert "paid product" not in section
    assert ("It can be looked up free, one address at a time, at gov.uk/council-tax-bands (in Scotland, on the "
            "Scottish Assessors' site), but no free bulk data exists, so reports cannot show it.") in section
    assert "No free official source gives these for every address." in section
    assert "<td>VOA band lookup, then the council's charges table</td>" in body
    assert 'href="https://www.gov.uk/council-tax-bands"' in body


def test_d6_the_areas_page_says_where_each_guide_figure_holds(client):
    body = client.get("/areas").text
    dek = _d6_text(re.search(r'<p class="dek">(.*?)</p>', body, re.S).group(1))
    assert ("the Environment Agency maps England only, so guides in Wales, Scotland and Northern Ireland say "
            "the zone is not mapped here and name the body that maps it.") in dek
    assert ("Band D council tax comes from MHCLG in England, the Welsh Government in Wales and the Scottish "
            "Government in Scotland; Northern Ireland has domestic rates, not council tax.") in dek
    assert "Band D council tax from MHCLG" not in dek
    assert "Every guide gives the district's recorded sold prices" not in dek
    # What the page promises is what the guide and the council tax
    # service do.
    guide = (ROOT / "app" / "templates" / "area_guide.html").read_text(encoding="utf-8")
    assert "Not mapped here for {{ flood_not_covered.country }}." in _flat(guide)
    council_tax = (ROOT / "app" / "services" / "council_tax.py").read_text(encoding="utf-8")
    for body_name in ("MHCLG", "Welsh Government", "Scottish Government"):
        assert f'"source_short": "{body_name}"' in council_tax, body_name


def test_d6_the_index_prefers_the_city_after_an_exact_match():
    from app.services.hpi import _pick_area, is_the_place_asked_for

    # The two the market report got wrong, and the other forms a city's
    # label takes.
    assert _pick_area({"City of Nottingham", "Nottinghamshire"}, "Nottingham") == "City of Nottingham"
    assert _pick_area({"Aberdeenshire", "City of Aberdeen"}, "Aberdeen") == "City of Aberdeen"
    assert _pick_area({"Aberdeenshire", "Aberdeen City"}, "Aberdeen") == "Aberdeen City"
    assert _pick_area({"Derbyshire", "City of Derby"}, "Derby") == "City of Derby"
    # An exact match still comes first, and the rest is as it was.
    assert _pick_area({"Nottingham", "City of Nottingham", "Nottinghamshire"}, "Nottingham") == "Nottingham"
    assert _pick_area({"Manchester", "Greater Manchester"}, "Manchester") == "Manchester"
    assert _pick_area({"City of Westminster"}, "Westminster") == "City of Westminster"
    assert _pick_area({"York", "North Yorkshire", "Yorkshire and The Humber", "South Yorkshire"}, "York") == "York"
    assert _pick_area({"Greater Manchester"}, "Leeds") is None

    for label, name in (("City of Nottingham", "Nottingham"), ("Aberdeen City", "Aberdeen"),
                        ("Bristol, City of", "Bristol"), ("Leeds", "Leeds"), ("city of westminster", "Westminster")):
        assert is_the_place_asked_for(label, name), (label, name)
    for label, name in (("Nottinghamshire", "Nottingham"), ("Aberdeenshire", "Aberdeen"),
                        ("Greater Manchester", "Manchester"), ("North Yorkshire", "York")):
        assert not is_the_place_asked_for(label, name), (label, name)


def _d6_index(monkeypatch):
    """The Land Registry SPARQL endpoint, faked: the county's rows come
    back first, and the CONTAINS filter is applied as the endpoint would."""
    from app.services import hpi

    areas = {"Nottinghamshire": (268000.0, 1.9), "City of Nottingham": (187500.0, 4.6),
             "Aberdeenshire": (201000.0, -1.2), "City of Aberdeen": (139000.0, -3.8)}
    months = [f"{y:04d}-{m:02d}" for y in range(2016, 2027) for m in range(1, 13)]
    months = [m for m in months if "2016-07" <= m <= "2026-07"]

    def bindings(query):
        wanted = re.search(r'LCASE\("([^"]*)"\)', query).group(1).lower()
        matched = [label for label in areas if wanted in label.lower()]
        if "percentageAnnualChange" in query:
            return [{"label": {"value": label}, "refMonth": {"value": "2026-07"},
                     "averagePrice": {"value": str(areas[label][0])},
                     "percentageAnnualChange": {"value": str(areas[label][1])}} for label in matched]
        return [{"label": {"value": label}, "refMonth": {"value": month},
                 "averagePrice": {"value": str(areas[label][0] - 500 * (len(months) - 1 - i))}}
                for i, month in enumerate(months) for label in matched]

    class _Response:
        def __init__(self, rows):
            self.rows = rows

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": {"bindings": self.rows}}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, **k):
            return _Response(bindings(params["query"]))

    monkeypatch.setattr(hpi.httpx, "AsyncClient", lambda *a, **k: _Client())


def test_d6_an_ng1_report_and_the_nottingham_rows_read_the_city(monkeypatch):
    from app.services import hpi
    _d6_index(monkeypatch)
    monkeypatch.setattr(app_main, "_MARKET_REPORT_CONCURRENCY", asyncio.Semaphore(4))

    # A report passes the index its council's name as postcodes.io gives
    # it, which for NG1 is "Nottingham": the area price line and the
    # price trend both read the city now, not the county.
    ng1 = next(o for o in app_main.ALL_OUTCODES if o["outcode"] == "NG1")
    assert ng1["district"] == "Nottingham"
    local = asyncio.run(hpi.area_comparison(ng1["district"], "", ""))["local_authority"]
    assert local == {"name": "City of Nottingham", "average_price": 187500.0, "annual_change_pct": 4.6,
                     "period": "2026-07"}
    trend = asyncio.run(hpi.price_trend(ng1["district"]))
    assert trend["area_name"] == "City of Nottingham" and trend["current_price"] == 187500.0

    # The market report's rows for both cities, each keeping what it was
    # asked for and where it links.
    for asked, outcode, label in (("Nottingham", "NG1", "City of Nottingham"), ("Aberdeen", "AB10", "City of Aberdeen")):
        assert (asked, outcode) in app_main.MARKET_REPORT_AREAS
        row = asyncio.run(app_main._market_report_area(asked, outcode))
        assert (row["name"], row["asked"], row["outcode"]) == (label, asked, outcode)
        assert app_main._market_report_view({"areas": [row]})["area_noun"] == "cities"


def test_d6_each_market_report_row_links_its_own_citys_district():
    from app.services.hpi import is_the_place_asked_for
    districts = {o["outcode"]: o["district"] for o in app_main.ALL_OUTCODES}
    assert len(app_main.MARKET_REPORT_AREAS) == 18
    for city, outcode in app_main.MARKET_REPORT_AREAS:
        assert outcode in app_main.KNOWN_OUTCODES, outcode
        assert is_the_place_asked_for(districts[outcode], city), (city, outcode, districts[outcode])


D6_MARKET_ROWS = (
    {"name": "Leeds", "asked": "Leeds", "outcode": "LS1", "average_price": 250000.0,
     "annual_change_pct": 3.2, "period": "2026-07"},
    {"name": "City of Nottingham", "asked": "Nottingham", "outcode": "NG1", "average_price": 187500.0,
     "annual_change_pct": 2.1, "period": "2026-07"},
    {"name": "Belfast", "asked": "Belfast", "outcode": "BT1", "average_price": 190000.0,
     "annual_change_pct": 1.0, "period": "2026-06"},
    {"name": "City of Westminster", "asked": "Westminster", "outcode": "SW1A", "average_price": 1020000.0,
     "annual_change_pct": -20.7, "period": "2026-07"},
)
D6_SWING = "Small areas with few sales can swing this much in a year."


def _d6_market_report(client, monkeypatch, areas):
    import time
    from app.services import _cache
    from tests.test_ai_search_readiness import _forget_html

    async def _nothing(*a, **k):
        return {}

    # Never the network: the snapshot below is what the page reads.
    monkeypatch.setattr(app_main.hpi, "area_comparison", _nothing)
    key = app_main.MARKET_REPORT_CACHE_KEY
    _cache._put(key, time.time(), {"generated_on": "2026-09-16", "areas": [dict(a) for a in areas]})
    _forget_html()
    try:
        return client.get("/market-report").text
    finally:
        _cache._evict(key)


def test_d6_the_market_report_names_its_month_links_each_row_and_flags_big_moves(client, monkeypatch):
    body = _d6_market_report(client, monkeypatch, D6_MARKET_ROWS)
    title = html.unescape(re.search(r"<title>(.*?)</title>", body, re.S).group(1))
    assert title.startswith("House prices by city: July 2026 figures")
    assert "<h1>UK house prices by city, July 2026 figures</h1>" in body
    dek = _d6_text(re.search(r'<p class="dek">(.*?)</p>', body, re.S).group(1))
    assert dek.startswith("Average price and annual change across 4 major UK cities, straight from HM Land")
    assert ("These are July 2026 figures, the latest month the index had published when it was read on "
            "16 Sep 2026.") in dek
    assert "For 1 of them the latest month is a different one, and each row gives its own." in dek
    assert "September" not in title and "Updated" not in dek
    assert f"the weakest City of Westminster at -20.7%. {D6_SWING}" in dek

    # Each row leads to its district's guide, and the page says the row
    # is the whole council area.
    for row in D6_MARKET_ROWS:
        assert f'<a href="/area/{row["outcode"]}">{row["name"]}</a>' in body
    assert ("Each linked name opens the area guide for a postcode district at its centre (LS1 for Leeds). "
            "The figures in the row are for the whole council area.") in _d6_text(body)
    assert "June 2026" in body

    # Only the move of more than ten per cent carries the line, in the
    # row under it.
    table = body[body.index('<table class="tx-table">'):body.index("</table>")]
    assert table.count('<tr class="market-swing">') == 1
    assert table.index(f'<tr class="market-swing"><td colspan="4">{D6_SWING}</td></tr>') > table.index("City of Westminster")
    assert app_main.MARKET_REPORT_SWING_PCT == 10
    view = app_main._market_report_view({"areas": [dict(D6_MARKET_ROWS[0], annual_change_pct=10.0),
                                                   dict(D6_MARKET_ROWS[1], annual_change_pct=-10.1)]})
    assert [a["swing"] for a in view["areas"]] == [False, True]


def test_d6_the_market_report_calls_its_list_areas_once_any_row_is_not_the_city(client, monkeypatch):
    rows = [dict(r) for r in D6_MARKET_ROWS]
    rows[1]["name"] = "Nottinghamshire"
    body = _d6_market_report(client, monkeypatch, rows)
    title = html.unescape(re.search(r"<title>(.*?)</title>", body, re.S).group(1))
    assert title.startswith("House prices by area: July 2026 figures")
    assert "<h1>UK house prices by area, July 2026 figures</h1>" in body
    dek = _d6_text(re.search(r'<p class="dek">(.*?)</p>', body, re.S).group(1))
    assert dek.startswith("Average price and annual change across 4 UK areas,")
    assert "Prices rose over the year in 3 of the 4 areas." in dek
    description = re.search(r'<meta name="description" content="([^"]*)"', body).group(1)
    assert "4 UK areas" in description and "major UK cities" not in description
    assert "major UK cities" not in dek


def test_d6_a_market_snapshot_stored_before_the_city_match_is_never_read(client, monkeypatch):
    import time
    from app.models import PageCache
    from app.services import _cache
    from tests.test_ai_search_readiness import _forget_html

    assert app_main.MARKET_REPORT_CACHE_KEY == ("market_report", 2)
    old = ("market_report", 1)
    _cache._put(old, time.time(), {"generated_date": "17 September 2026", "areas": [
        {"name": "Nottinghamshire", "average_price": 268000.0, "annual_change_pct": 1.9, "period": "2026-07"}]})

    async def _index(name, region, country):
        label = {"Nottingham": "City of Nottingham", "Aberdeen": "City of Aberdeen"}.get(name, name)
        return {"local_authority": {"name": label, "average_price": 200000.0, "annual_change_pct": 2.0,
                                    "period": "2026-07"}}

    monkeypatch.setattr(app_main.hpi, "area_comparison", _index)
    monkeypatch.setattr(app_main, "_MARKET_REPORT_CONCURRENCY", asyncio.Semaphore(4))
    _cache._evict(app_main.MARKET_REPORT_CACHE_KEY)
    _forget_html()
    try:
        body = client.get("/market-report").text
    finally:
        _cache._evict(old)
        _cache._evict(app_main.MARKET_REPORT_CACHE_KEY)
        with db.get_session() as session:
            row = session.get(PageCache, "market_report:2")
            if row is not None:
                session.delete(row)
                session.commit()
    assert "Nottinghamshire" not in body and "17 September 2026" not in body
    assert '<a href="/area/NG1">City of Nottingham</a>' in body
    assert '<a href="/area/AB10">City of Aberdeen</a>' in body
    assert "across 18 major UK cities" in _d6_text(body)


# ---- D7. Small fixes a careful reader catches -------------------------------
# (1) Bus times read "first bus 00:19, last 00:14" (Fortismere) and "first
# bus 05:05, last 04:24" (LS6): the importer's last departure of the
# service day runs past midnight. One helper, bus_service.service_hours,
# words it for the report, the area guides, the school pages and the PDF.
# (2) The school page's verdict read "... from Fortismere School.
# comfortably inside the distance ... (1.883 miles" under a 1.88 mi tile.
# (3) LS6 gave Leeds's Band D as £2,284 and £2,283, and KT3 4HX's Band F
# was £3,768 in the report's pop-up and £3,769 on running costs: one
# source for the latest Band D (council_tax.json) and one rounding
# (council_tax.whole_pounds). (4) Every council tax page suggested
# "e.g. S11 8XZ", in Sheffield. (5) The Nearby Essentials pop-up showed
# "Not set up on this deployment". (6) The same card read "Data
# unavailable" on one walk and "52 nearby" on another: the page asks once
# more first. (7) House 9 counted "Flat 9, 12 Acacia Road" as its own.

D7_BUS_STOP = {"atco_code": "D7A", "name": "Muswell Hill Broadway", "distance_m": 140, "latitude": 53.45,
               "longitude": -2.22, "weekday_day": 96, "weekday_eve": 16, "sunday_day": 36,
               "weekday_day_per_hour": 8.0, "weekday_eve_per_hour": 4.0, "sunday_day_per_hour": 4.0,
               "weekday_first": "05:30", "weekday_last": "00:30", "routes": ["43", "134"]}
D7_NIGHT_STOP = dict(D7_BUS_STOP, atco_code="D7B", name="Fortis Green", distance_m=260,
                     weekday_day=60, weekday_first="00:19", weekday_last="00:14", routes=["N43"])


def _d7_bus(best, *others):
    stops = [best, *others]
    return {"radius_m": 500, "stops": stops, "count": len(stops), "nearest": best, "best": best,
            "routes": ["43", "134", "N43"], "feed_date": "2026-09-07", "ref_weekday": "2026-09-08",
            "ref_sunday": "2026-09-13"}


def _d7_unlocked(client, fake_report, monkeypatch, email, house, gather):
    """The report read by an account that has unlocked this house number
    (each test its own number: the tests share one database)."""
    from app.db import get_session
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    fake_report(gather=gather)
    with get_session() as session:
        known = auth.find_user_by_email(session, email) is not None
    if not known:
        assert _signup(client, email).status_code == 303
    with get_session() as session:
        user = auth.find_user_by_email(session, email)
        auth.claim_unlock(session, user.id, "M14 5TG", house)
    r = client.get(f"/property?postcode=M14%205TG&house_number={house}")
    assert r.status_code == 200
    return r.text


def test_d7_a_bus_service_past_midnight_is_worded_not_printed_backwards():
    from app.services import bus_service
    hours = bus_service.service_hours
    assert hours("06:10", "23:40") == {"text": "first bus 06:10, last 23:40", "first": "06:10", "last": "23:40",
                                       "overnight": False}
    # Fortismere and LS6 as the audit read them: the last bus leaves
    # within the hour before the next day's first, in the small hours.
    for first, last in (("00:19", "00:14"), ("05:05", "04:24")):
        h = hours(first, last)
        assert h["text"] == "the service runs through the night"
        assert h["overnight"] and h["cell"] == "Runs through the night"
    # A last bus after midnight with a night's gap before the first.
    h = hours("05:30", "00:30")
    assert h["text"] == "first bus 05:30, last 00:30 after midnight" and h["last"] == "00:30 after midnight"
    # Both in the small hours, but four hours without a bus is not
    # "through the night".
    assert hours("04:50", "00:40")["text"] == "first bus 04:50, last 00:40 after midnight"
    # A missing time leaves the clause out rather than printing a blank.
    assert hours("", "")["text"] == "" and hours("06:10", None)["text"] == ""
    assert bus_service.SMALL_HOURS_END_MIN == 360 and bus_service.THROUGH_NIGHT_GAP_MIN == 60


def test_d7_the_report_and_its_pdf_word_the_first_and_last_bus_the_same_way(client, fake_report, monkeypatch):
    from app.services import pdf_checklist
    from tests.conftest import fake_gather
    from tests.test_pdf_report import _running_costs
    data = _d7_bus(D7_BUS_STOP, D7_NIGHT_STOP)
    body = _d7_unlocked(client, fake_report, monkeypatch, "d7-buses@customer.test", "7101",
                        fake_gather(bus_service=data))
    modal = body.split('id="modal-bus"', 1)[1].split("</dialog>", 1)[0]
    assert "(09:00 to 18:00); first bus 05:30, last 00:30 after midnight." in _flat(modal)
    assert '<td class="num">05:30</td>' in modal and '<td class="num">00:30 after midnight</td>' in modal
    # The stop with no overnight gap says so across both columns.
    assert '<td class="num" colspan="2">Runs through the night</td>' in modal
    assert "00:14" not in modal and "00:19" not in modal and "last 00:30." not in modal

    # The PDF: its bus paragraph and its checklist row, from the same helper.
    report = dict(_full_pdf_report(), bus_service=_d7_bus(D7_NIGHT_STOP))
    ctx = app_main._pdf_context(report, _running_costs(), report["location"], "36")
    page = app_main.templates.get_template("pdf_report_full.html").render(ctx)
    assert "Fortis Green, 260 m away; the service runs through the night; routes" in page
    assert "00:14" not in page
    rows = {r["check"]: r for r in pdf_checklist.build(report, _running_costs())}
    assert rows["Bus service"]["result"].endswith("at Fortis Green (260 m); the service runs through the night")
    report = dict(report, bus_service=_d7_bus(D7_BUS_STOP))
    rows = {r["check"]: r for r in pdf_checklist.build(report, _running_costs())}
    assert rows["Bus service"]["result"].endswith("(140 m); first bus 05:30, last 00:30 after midnight")


def test_d7_the_area_guide_says_a_night_service_runs_through_the_night(client, monkeypatch):
    from tests.test_pages import V20_STUBS, _fresh_guide
    ls6 = dict(V20_STUBS["bus"], best=dict(V20_STUBS["bus"]["best"], weekday_first="05:05", weekday_last="04:24"))
    body = _fresh_guide(client, monkeypatch, "AB12", dict(V20_STUBS, bus=ls6))
    section = _flat(body.split("Buses from the centre of AB12", 1)[1].split("</section>", 1)[0])
    assert "and 12.0 on Sunday daytime; the service runs through the night." in section
    assert "04:24" not in section and "first bus 05:05" not in section


def _d7_school(urn, name, lat, lon, miles):
    from app.models import School, SchoolAdmissionRadius, SchoolDetail
    with db.get_session() as session:
        session.merge(School(urn=urn, name=name, phase="Secondary", type_name="Academy converter",
                             postcode="PL4 8AA", latitude=lat, longitude=lon, ofsted_rating=2, ofsted_rating_label="Good"))
        session.merge(SchoolDetail(urn=urn, town="Plymouth", admissions_policy="Not applicable", local_authority="Plymouth"))
        session.merge(SchoolAdmissionRadius(urn=urn, last_distance_miles=miles, academic_year="2025/26",
                                            source_authority="Plymouth"))
        session.commit()


def test_d7_the_school_page_starts_its_why_sentence_with_a_capital_and_gives_miles_to_two_places(client, monkeypatch):
    from app.models import BusStop
    from app.services import _cache
    lat, lon = 50.3700, -4.1400                 # a corner of the test database no other test uses
    _d7_school(990711, "Hoe Park Academy", lat, lon, 1.8826)
    with db.get_session() as session:
        session.merge(BusStop(atco_code="D7NIGHT", name="Hoe Road", latitude=lat + 0.0009, longitude=lon,
                              weekday_day=60, weekday_eve=12, sunday_day=27, weekday_first="00:19", weekday_last="00:14",
                              routes='["N1"]', feed_date=datetime.date(2026, 9, 7), ref_weekday=datetime.date(2026, 9, 8),
                              ref_sunday=datetime.date(2026, 9, 13)))
        session.commit()
    _cache._store.clear(); _cache._bytes = 0

    async def _lookup(_pc):
        return {"postcode": "PL1 2AA", "latitude": lat + 0.0058, "longitude": lon}

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    try:
        body = client.get("/school/990711/hoe-park-academy?check=PL1+2AA").text
    finally:
        with db.get_session() as session:
            session.query(BusStop).filter(BusStop.atco_code == "D7NIGHT").delete()
            session.commit()
    verdict = _flat(re.sub(r"<[^>]+>", "", body.split('class="admission-verdict-detail">', 1)[1].split("</span>", 1)[0]))
    miles = app_main._miles_label(round(app_main._haversine_km(lat, lon, lat + 0.0058, lon) / 1.60934, 2))
    assert verdict.startswith(f"PL1 2AA is {miles} miles from Hoe Park Academy. Comfortably inside the distance the "
                              "school admitted from last time (1.88 miles in 2025/26).")
    assert "1.8826" not in verdict and ". comfortably" not in verdict
    assert re.search(r"That is 1\.\d\d? miles inside it\.", verdict)
    assert '<span class="score-tile-value">1.88 mi</span>' in body
    # And its bus paragraph, for a stop whose last bus leaves after midnight.
    buses = _flat(body.split("Getting to Hoe Park Academy by bus", 1)[1].split("</p>", 1)[0])
    assert "on Sunday daytime; the service runs through the night." in buses
    assert "00:14" not in buses


def test_d7_the_why_phrase_is_lower_case_inside_a_sentence_and_capitalised_to_start_one():
    for distance, radius, why in ((0.4, 1.8826, "Comfortably inside the distance the school admitted from last time"),
                                  (1.9, 1.8826, "Close to last time's distance, which moves every year with demand"),
                                  (3.0, 1.8826, "Outside the distance the school admitted from last time")):
        v = app_main._admission_verdict(distance, radius)
        assert v["why_sentence"] == why and v["why"] == why[0].lower() + why[1:]
    assert app_main._admission_verdict(0.4, 621.37, no_limit=True)["why_sentence"] == "Distance did not limit entry"
    # The schools guide's tooltip starts with it.
    school = {"urn": 102156, "name": "Fortismere School", "latitude": 51.59, "longitude": -0.15, "distance_m": 644,
              "admission_radius": {"last_distance_miles": 1.8826, "academic_year": "varies"}}
    row = app_main._guide_rows({"all_schools": [school]}, verdict_from={"postcode": "N10 3JA"})[0]
    assert row["verdict_why"] == "Comfortably inside the distance the school admitted from last time"
    # The miles formatter the tile uses, and now the check and the report.
    assert [app_main._miles_label(v) for v in (1.8826, 0.4, 1.5, 2.0, None)] == ["1.88", "0.4", "1.5", "2", ""]
    assert app_main.templates.env.filters["miles"] is app_main._miles_label


def test_d7_the_reports_catchment_table_and_both_maps_give_the_radius_to_two_places():
    source = (pathlib.Path(app_main.__file__).parent / "templates" / "property.html").read_text(encoding="utf-8")
    assert '<td class="num">{{ s.radius_miles | miles }} mi</td>' in source
    # Both map branches, Google (production) and Leaflet (dev), print the label.
    assert source.count("' · published admission distance ' + s.radius_label + ' mi ('") == 2
    assert source.count("' · estimated admission distance ' + s.radius_label + ' mi (modelled") == 2
    assert "+ s.radius_miles + ' mi" not in source


# (3) One Band D, one rounding.

def test_d7_money_rounds_to_the_nearest_pound_the_same_way_everywhere():
    from app.services import council_tax, pdf_checklist
    for value, words in ((2283.73, "£2,284"), (3768.84, "£3,769"), (3768.5, "£3,769"), (3768.49, "£3,768"),
                         (2.675, "£3"), (250000, "£250,000"), ("412345", "£412,345")):
        assert app_main._format_gbp(value) == words
        assert pdf_checklist._fmt_gbp(value) == words
        assert f"£{council_tax.whole_pounds(value):,}" == words
    assert app_main._format_gbp(None) == "None" and app_main._format_gbp("Not held") == "Not held"
    assert app_main._format_gbp(float("nan")) == "nan"
    # The area guide's sentences and the trend chart's labels use it too.
    chart = app_main._trend_chart([{"value": v, "period": str(i), "note": ""} for i, v in enumerate((2100.5, 2200.49, 2283.73))])
    assert [p["value"] for p in chart["points"]] == ["£2,101", "£2,200", "£2,284"]
    page = (pathlib.Path(app_main.__file__).parent / "templates" / "council_tax_table.html").read_text(encoding="utf-8")
    assert "\"{:,.0f}\"" not in page
    assert page.count("{{ r.band_a | gbp }}") == 3 and page.count("{{ r.band_d | gbp }}") == 3


def test_d7_the_latest_band_d_is_the_council_tax_files_figure():
    from app.services import council_finance, council_tax
    ct = council_tax.for_district("E08000035")
    finance = council_finance.for_council("E08000035", "Leeds")
    assert ct["authority"] == "Leeds" and finance["history"][-1]["year"][:4] == ct["year"][:4]
    assert finance["history"][-1]["band_d"] == ct["band_d"]
    # Found by name, the same.
    assert council_finance.for_council("", "Leeds")["history"][-1]["band_d"] == ct["band_d"]
    # A payload cached before today is settled at render, without
    # touching the cached copy.
    cached = dict(finance, history=finance["history"][:-1] + [dict(finance["history"][-1], band_d=2284.0)])
    settled = council_finance.with_council_tax_band_d(cached)
    assert settled["history"][-1]["band_d"] == ct["band_d"] and cached["history"][-1]["band_d"] == 2284.0
    assert settled["history"][:-1] == cached["history"][:-1]
    # A year the council tax file does not hold, or no code, is left as it is.
    other_year = dict(cached, history=cached["history"][:-1] + [dict(cached["history"][-1], year="2027-2028")])
    assert council_finance.with_council_tax_band_d(other_year) is other_year
    assert council_finance.with_council_tax_band_d(dict(cached, code=""))["history"][-1]["band_d"] == 2284.0
    assert council_finance.with_council_tax_band_d(None) is None


def test_d7_the_ls6_guide_gives_one_band_d_in_every_section(client, monkeypatch):
    import time
    from app.services import _cache, council_finance, council_tax
    from tests.test_ai_search_readiness import AREA_PAYLOAD, _forget_html

    ct = council_tax.for_district("E08000035")
    rounded, cut = app_main._format_gbp(ct["band_d"]), f"£{int(ct['band_d']):,}"
    finance = council_finance.for_council("E08000035", "Leeds")
    # As the warm guides hold it: the finance file's whole-pound figure.
    warm = dict(finance, history=finance["history"][:-1] + [dict(finance["history"][-1], band_d=float(round(ct["band_d"])))])

    async def _resolve(outcode):
        where = fake_location(postcode=f"{outcode} 2AA", outcode=outcode)
        where.update(admin_district="Leeds", codes={"admin_district": "E08000035", "lsoa": "E01011352"})
        return where, True

    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    key = ("area_guide", app_main.AREA_GUIDE_PAYLOAD_VERSION, "LS6")
    _cache._put(key, time.time(), dict(AREA_PAYLOAD, finance=warm))
    _forget_html()
    try:
        body = client.get("/area/LS6").text
    finally:
        _cache._evict(key)
    flat = _flat(re.sub(r"<[^>]+>", " ", body))
    assert f"Council tax at Band D in Leeds: {rounded} a year" in flat                      # House prices
    assert f"A Band D household in Leeds pays {rounded} in council tax for 2026-27" in flat  # the lead
    assert f"A Band D household in Leeds pays {rounded} for 2026-27" in flat                # the council section
    assert rounded == "£2,284" and cut == "£2,283"
    assert cut not in body


def test_d7_a_band_reads_the_same_in_the_reports_pop_up_and_on_the_council_page(client, fake_report):
    from app.services import council_tax
    from tests.conftest import fake_gather
    ct = council_tax.for_district(None, "Kingston upon Thames")
    band_f = app_main._format_gbp(ct["bands"]["F"])
    assert band_f == "£3,769"                   # 3,768.84, which the pop-up cut to £3,768
    fake_report(gather=fake_gather(council_tax=ct))
    body = client.get("/property?postcode=M14+5TG", headers={"User-Agent": "Googlebot/2.1"}).text
    popup = body.split('id="modal-council-tax"', 1)[1].split("</dialog>", 1)[0]
    assert f'<tr><td>Band F</td><td class="num">{band_f}</td>' in popup and "£3,768" not in popup
    page = client.get(f"/running-costs/council-tax/{ct['slug']}").text
    description = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', page).group(1))
    assert f"F {band_f}," in description
    assert f"Band D {app_main._format_gbp(ct['band_d'])}," in html.unescape(re.search(r"<title>(.*?)</title>", page, re.S).group(1))


# (4) The postcode box on a council's page suggests a district inside it.

def test_d7_a_councils_postcode_box_suggests_a_district_inside_that_council(client, monkeypatch):
    for slug, authority in (("basildon", "Basildon"), ("leeds", "Leeds")):
        first = app_main._guides_in_council(authority)[0]
        body = client.get(f"/running-costs/council-tax/{slug}").text
        assert f'placeholder="e.g. a postcode in {first}"' in body
        assert f'<a class="tag-link" href="/area/{first}">{first}</a>' in body
        assert "S11 8XZ" not in body
    # Inside the council by postcodes.io: Basildon's first is CM11, Billericay.
    first = app_main._guides_in_council("Basildon")[0]
    assert next(o for o in app_main.ALL_OUTCODES if o["outcode"] == first)["district"] == "Basildon"
    # A council with no area guide listed asks for a full postcode.
    from tests.test_ai_search_readiness import _forget_html
    monkeypatch.setattr(app_main, "_guides_in_council", lambda authority: [])
    _forget_html()                              # the page above is kept as HTML for ten minutes
    body = client.get("/running-costs/council-tax/basildon").text
    _forget_html()
    assert 'placeholder="e.g. a full postcode"' in body and "S11 8XZ" not in body


# (5) No developer note in the Nearby Essentials pop-up.

def test_d7_the_nearby_essentials_pop_up_carries_no_developer_note(client, fake_report):
    from tests.conftest import fake_gather
    fake_report()                               # google_ratings_configured is False here
    body = client.get("/property?postcode=M14+5TG").text
    assert "Not set up on this deployment" not in body
    assert '<h3 class="subsection-heading">Google ratings</h3>' not in body
    assert "Public star ratings from Google" not in body
    fake_report(gather=fake_gather(google_ratings_configured=True, google_ratings=[
        {"name": "Cafe Uno", "type": "Cafe", "rating": 4.5, "rating_count": 120, "distance_m": 200}]))
    body = client.get("/property?postcode=M14+5TG").text
    assert '<h3 class="subsection-heading">Google ratings</h3>' in body and "Cafe Uno" in body


# (6) One more try before "Data unavailable".

def test_d7_the_amenities_reply_says_whether_the_lookup_failed(client, fake_report, monkeypatch):
    from app.services import amenities as amenities_service
    fake_report()

    async def _down(lat, lon, lite=False):
        raise RuntimeError("Overpass dropped the query")

    async def _up(lat, lon, lite=False):
        categories = {k: [] for k in ("restaurant", "supermarket", "pharmacy", "pub", "hospital", "parking", "ev_charging",
                                      "gp", "dentist", "green_space", "wind_turbine", "solar_farm")}
        return {"categories": categories, "stations": {}, "stations_list": {"rail": [], "tube": [], "tram": [], "bus": []}}

    monkeypatch.setattr(amenities_service, "nearby_amenities_and_station", _down)
    failed = client.get("/api/property/amenities?postcode=M14%205TG").json()
    assert failed["error"] is True and "Data unavailable" in failed["essentials_card"]
    monkeypatch.setattr(amenities_service, "nearby_amenities_and_station", _up)
    answered = client.get("/api/property/amenities?postcode=M14%205TG").json()
    assert answered["error"] is False and "0 nearby" in answered["essentials_card"]


def _d7_amenities_script(client, fake_report):
    from tests.conftest import fake_gather
    fake_report(gather=fake_gather(amenities_pending=True, amenities_error=False))
    body = client.get("/property?postcode=M14+5TG").text
    scripts = [s for s in re.findall(r"<script>(.*?)</script>", body, re.S) if "/api/property/amenities?postcode=" in s]
    assert len(scripts) == 1
    return scripts[0]


D7_NODE_HARNESS = r"""
const script = require('fs').readFileSync(process.argv[2], 'utf8');
const replies = JSON.parse(process.argv[3]);
let calls = 0;
const waits = [], swapped = {}, statuses = [];
global.fetch = function () {
    const reply = replies[Math.min(calls, replies.length - 1)];
    calls += 1;
    if (reply === 'network') return Promise.reject(new Error('network'));
    return Promise.resolve({ ok: true, json: function () { return Promise.resolve(reply); } });
};
global.setTimeout = function (fn, ms) { waits.push(ms); fn(); return 0; };
const classList = { add: function () {}, remove: function () {}, contains: function () { return false; } };
global.document = {
    getElementById: function (id) {
        return { classList: classList, dataset: {}, addEventListener: function () {},
                 set outerHTML(v) { swapped[id] = v; } };
    },
    querySelectorAll: function (selector) {
        if (selector.indexOf('dashboard-card-status') === -1) return [];
        return [{ closest: function () { return { classList: classList }; },
                  set textContent(v) { statuses.push(v); } }];
    },
};
global.window = {};
eval(script);
setImmediate(function () {
    console.log(JSON.stringify({ calls: calls, waits: waits, swapped: swapped, statuses: statuses }));
});
"""


def test_d7_the_page_asks_once_more_before_it_says_data_unavailable(client, fake_report, tmp_path):
    import shutil
    import subprocess
    import pytest
    script = _d7_amenities_script(client, fake_report)
    assert "const RETRY_AFTER_MS = 2500;" in script
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed; the retry is pinned by the source check above")
    (tmp_path / "page.js").write_text(script, encoding="utf-8")
    (tmp_path / "harness.js").write_text(D7_NODE_HARNESS, encoding="utf-8")

    def run(*replies):
        out = subprocess.run([node, str(tmp_path / "harness.js"), str(tmp_path / "page.js"), json.dumps(list(replies))],
                             capture_output=True, text=True, timeout=60, check=True)
        return json.loads(out.stdout)

    good = {"error": False, "essentials_card": "<b>52 nearby</b>", "transport_card": "", "essentials_body": "list",
            "transport_body": "", "stations_list": {}}
    bad = dict(good, error=True, essentials_card="<b>Data unavailable</b>", essentials_body="none")
    # Answered first time: one request, no wait.
    first = run(good)
    assert first["calls"] == 1 and first["waits"] == [] and first["swapped"]["card-amenities"] == "<b>52 nearby</b>"
    # The lookup failed, then answered: the second reply is the one shown.
    second = run(bad, good)
    assert second["calls"] == 2 and second["waits"] == [2500]
    assert second["swapped"]["card-amenities"] == "<b>52 nearby</b>" and second["statuses"] == []
    # The request itself failed, then answered.
    second = run("network", good)
    assert second["calls"] == 2 and second["swapped"]["card-amenities"] == "<b>52 nearby</b>"
    # Failed twice: once only, then the server's own "Data unavailable".
    twice = run(bad, bad, good)
    assert twice["calls"] == 2 and twice["swapped"]["card-amenities"] == "<b>Data unavailable</b>"
    twice = run(bad, "network")
    assert twice["calls"] == 2 and twice["swapped"]["card-amenities"] == "<b>Data unavailable</b>"
    twice = run("network", "network", good)
    assert twice["calls"] == 2 and twice["swapped"] == {} and set(twice["statuses"]) == {"Data unavailable"}


# (7) A house number is a whole token at the start of the address.

def test_d7_house_9_is_not_19_or_29_or_flat_9():
    records = [{"address": a} for a in ("9 ACACIA ROAD", "19 ACACIA ROAD", "29 ACACIA ROAD", "9, Acacia Road",
                                        "FLAT 9 12 ACACIA ROAD", "9A ACACIA ROAD")]
    pick = lambda recs, q: [r["address"] for r in app_main._filter_by_address(recs, q)]  # noqa: E731
    assert pick(records, "9") == ["9 ACACIA ROAD", "9, Acacia Road"]
    assert pick(records, "9 Acacia Road") == ["9 ACACIA ROAD", "9, Acacia Road"]
    assert pick(records, "19") == ["19 ACACIA ROAD"]
    # A flat's own number counts only when the search names a flat.
    assert pick(records, "Flat 9") == ["FLAT 9 12 ACACIA ROAD"]
    assert pick([r for r in records if r["address"].startswith(("1", "2", "FLAT"))], "9") == []
    assert pick([{"address": "Apartment 9, 30 Acacia Road"}, {"address": "UNIT 9 40 ACACIA ROAD"}], "9") == []
    # The building's number still finds the flats inside it, and "9"
    # still finds "9A" where no plain 9 is recorded.
    assert pick([{"address": "Flat 1, 9 Acacia Road"}, {"address": "FLAT 9 12 ACACIA ROAD"}], "9") == ["Flat 1, 9 Acacia Road"]
    assert pick([{"address": "9A ACACIA ROAD"}, {"address": "19 ACACIA ROAD"}], "9") == ["9A ACACIA ROAD"]
    assert pick(records, "9a") == ["9A ACACIA ROAD"]
    # The change alert: a sale at flat 9 at number 12, at 19 or at 9A is
    # not a sale of house 9; a sale at 9 is.
    before = _c1_sale_summary(C1_STREET, "9")
    after = _c1_sale_summary(C1_STREET + _c1_sales("FLAT 9 30 ACACIA AVENUE", "19 ACACIA AVENUE", "9A ACACIA AVENUE"), "9")
    assert app_main._alert_changes(before, after) == []
    assert app_main._alert_changes(before, _c1_sale_summary(C1_STREET + _c1_sales("9 ACACIA AVENUE"), "9"))


# ---- Batch D fix pass: what the review of D1 to D7 found left over -------
# (1) D1: a converted house with a certificate of its own beside sales of
# its flats only. The "57" link in "Which home is yours?" read Flat 1's
# £301,000 as "last sold here, 2023" and "Leasehold at the last recorded
# sale", and divided the flat's price by the house's 122 m². (2) D7(3):
# /running-costs rounded Bexley's Band B of 1,840.50 to £1,840 where the
# report's pop-up said £1,841. (3) D7(7): the Comparables caption placed
# 19 Acacia Road's sale as house 9's.

DFIX_CERTS = [
    {"address": "57, Malden Hill Gardens, New Malden", "rating": "D", "date": "2025-11-04", "certificate_number": "DF-57"},
    {"address": "Flat 1, 57, Malden Hill Gardens, New Malden", "rating": "C", "date": "2021-02-10",
     "certificate_number": "DF-57-1"},
]
DFIX_FLAT_SALE = {"address": "FLAT 1 57 MALDEN HILL GARDENS", "street": "MALDEN HILL GARDENS", "town": "NEW MALDEN",
                  "amount": "301000", "date": "2023-03-15", "tenure": "Leasehold"}


def test_dfix_a_house_link_does_not_take_the_sale_of_a_flat_inside_it(client, fake_report):
    homes = app_main._postcode_homes(DFIX_CERTS, [DFIX_FLAT_SALE])
    house = next(h for h in homes if h["short"] == "57")
    flat = next(h for h in homes if h["short"] != "57")
    hn = house["house_number"]
    assert hn == "57 Malden Hill Gardens"
    # The house's link: its own certificate, and no sale of a flat inside it.
    assert app_main._filter_by_address(DFIX_CERTS, hn) == [DFIX_CERTS[0]]
    assert app_main._filter_by_address([DFIX_FLAT_SALE], hn) == []
    # The flat's link finds the flat's certificate and its sale.
    assert app_main._filter_by_address(DFIX_CERTS, flat["house_number"]) == [DFIX_CERTS[1]]
    assert app_main._filter_by_address([DFIX_FLAT_SALE], flat["house_number"]) == [DFIX_FLAT_SALE]
    # A bare number typed in the box still finds the flats inside it, a
    # street still finds the flats on it, and a named house is not its flat.
    assert app_main._filter_by_address([DFIX_FLAT_SALE], "57") == [DFIX_FLAT_SALE]
    assert app_main._filter_by_address([DFIX_FLAT_SALE], "Malden Hill Gardens") == [DFIX_FLAT_SALE]
    named = [{"address": "ROSE COTTAGE, CHURCH LANE"}, {"address": "FLAT 1 ROSE COTTAGE CHURCH LANE"}]
    assert app_main._filter_by_address(named[1:], "Rose Cottage, Church Lane") == []
    assert app_main._filter_by_address(named, "Flat 1 Rose Cottage") == [named[1]]

    # The report on that link, gathered as the real gather filters.
    body = _d1_report(client, fake_report, house_number="57+Malden+Hill+Gardens",
                      certificates=app_main._filter_by_address(DFIX_CERTS, hn),
                      transactions=app_main._filter_by_address([DFIX_FLAT_SALE], hn))
    assert "last sold here" not in body and "301,000" not in body
    assert "at the last recorded sale" not in body

    # And no per square metre line from the flat's price over the house's floor area.
    recent = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
    comparables = [{"address": f"{n} Near Road", "date": recent, "amount": str(500000 + n * 10000), "floor_area": 100 + n}
                   for n in range(1, 6)]
    context = {"transactions": app_main._filter_by_address([dict(DFIX_FLAT_SALE, date=recent)], hn)}
    app_main._apply_valuation(context, comparables, 122, 2.0, hn)
    assert context["price_per_sqm"]["subject"] is None and context["price_per_sqm"]["median"]


def test_dfix_running_costs_rounds_a_band_the_way_the_reports_pop_up_does(client, fake_report, monkeypatch):
    from app.services import _cache, council_tax
    from tests.conftest import fake_gather
    ct = council_tax.for_district(None, "Bexley")
    assert ct["bands"]["B"] == 1840.5                  # a real half: "{:,.0f}" made it £1,840
    band_b = app_main._format_gbp(ct["bands"]["B"])
    assert band_b == "£1,841"

    fake_report(gather=fake_gather(council_tax=ct))
    body = client.get("/property?postcode=M14+5TG", headers={"User-Agent": "Googlebot/2.1"}).text
    popup = body.split('id="modal-council-tax"', 1)[1].split("</dialog>", 1)[0]
    assert f'<tr><td>Band B</td><td class="num">{band_b}</td>' in popup

    async def _lookup(_postcode):
        return fake_location(postcode="DA6 7AT", outcode="DA6")

    async def _answer(where, house_number):
        return {"postcode": "DA6 7AT", "district": "Bexley", "outcode": "DA6", "house_number": "",
                "latitude": 51.45, "longitude": 0.14, "council_tax": ct, "energy": None,
                "home": None, "sales": None, "stamp_duty": None, "rent": None, "district_prices": None,
                "area_prices": None, "broadband": None, "flood": None, "typical_year": None,
                "energy_figure": None, "income_value": None, "income_la": None, "income_la_name": "",
                "typical_share_pct": None}

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "_running_costs_for_postcode", _answer)
    _cache._store.clear()
    _cache._bytes = 0
    page = client.get("/running-costs?postcode=DA6%207AT").text
    _cache._store.clear()
    _cache._bytes = 0
    bands = page.split("Council tax at every band in Bexley", 1)[1].split("</table>", 1)[0]
    assert f'<td class="num">{band_b}</td>' in bands and "£1,840<" not in bands
    assert all(f'<td class="num">{app_main._format_gbp(v)}</td>' in bands for v in ct["bands"].values())
    # Every pound on the page goes through the one filter.
    source = (pathlib.Path(app_main.__file__).parent / "templates" / "running_costs.html").read_text(encoding="utf-8")
    assert "\"{:,.0f}\"" not in source and "&pound;{{" not in source


def test_dfix_comparables_places_house_9s_own_sale_and_not_19s(client, monkeypatch):
    from app.services import _cache
    from tests.test_ai_search_readiness import _forget_html

    async def _lookup(_postcode):
        return fake_location()

    async def _nearby(lat, lon, **kwargs):
        return [{"postcode": "M14 5TG", "distance_m": 0, "latitude": 53.45, "longitude": -2.22},
                {"postcode": "M14 5TH", "distance_m": 400, "latitude": 53.452, "longitude": -2.221}]

    async def _sold(postcodes):
        return [
            {"address": "19 ACACIA ROAD", "postcode": "M14 5TG", "amount": "410000", "date": "2025-04-01",
             "property_type": "terraced", "tenure": "Freehold", "new_build": False},
            {"address": "3 FAR STREET", "postcode": "M14 5TH", "amount": "300000", "date": "2024-02-01",
             "property_type": "terraced", "tenure": "Freehold", "new_build": False},
            {"address": "9 ACACIA ROAD", "postcode": "M14 5TG", "amount": "250000", "date": "2019-05-01",
             "property_type": "terraced", "tenure": "Freehold", "new_build": False},
        ]

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "nearby_postcodes", _nearby)
    monkeypatch.setattr(app_main, "sold_prices_for_postcodes", _sold)
    keys = (app_main._comparables_page_key(53.45, -2.22), app_main._comparables_key(53.45, -2.22))
    for key in keys:
        _cache._evict(key)
    _forget_html()
    try:
        nine = _flat(client.get("/property/comparables?postcode=M14%205TG&house_number=9").text)
        nineteen = _flat(client.get("/property/comparables?postcode=M14%205TG&house_number=19").text)
    finally:
        for key in keys:
            _cache._evict(key)
        _forget_html()
    assert '£250,000 (for "9") sits above 0% of the 3 nearby sold prices' in nine
    assert "£410,000 (for" not in nine
    assert '£410,000 (for "19") sits above 67% of the 3 nearby sold prices' in nineteen
    # The page reads the report's own matcher, not a substring.
    source = (pathlib.Path(app_main.__file__)).read_text(encoding="utf-8")
    assert "house_number.lower() in t[\"address\"].lower()" not in source


# (4) The review's quicker suggestions. The row's links: the number alone
# where it opens the same records, a comma after a flat's own number, and
# nofollow, since each link costs a full gather on a noindex page.

def test_dfix_the_row_sets_the_number_alone_only_where_it_opens_the_same_home():
    certs = [{"address": "57, Malden Hill Gardens", "certificate_number": "A"},
             {"address": "57 Acacia Road", "certificate_number": "B"},
             {"address": "12 Acacia Road", "certificate_number": "C"}]
    sales = [{"address": "FLAT 1 2 ACACIA ROAD", "street": "ACACIA ROAD"},
             {"address": "12 ACACIA ROAD", "street": "ACACIA ROAD"},
             {"address": "MALDEN HOUSE 3 MALDEN HILL GARDENS", "street": "MALDEN HILL GARDENS"}]
    homes = {h["label"]: h for h in app_main._postcode_homes(certs, sales)}
    # 57 is on two streets here, so each keeps its street; 12 is one home.
    assert homes["57 Malden Hill Gardens"]["house_number"] == "57 Malden Hill Gardens"
    assert homes["57 Acacia Road"]["house_number"] == "57 Acacia Road"
    assert homes["12 Acacia Road"]["house_number"] == "12"
    # HM Land Registry's "FLAT 1 2" reads with the register's comma, and
    # still opens its own sale and nothing else.
    flat = homes["Flat 1, 2 Acacia Road"]
    assert flat["short"] == "Flat 1, 2" and flat["house_number"] == "Flat 1, 2"
    assert app_main._filter_by_address(sales, flat["house_number"]) == [sales[0]]
    assert app_main._filter_by_address(certs, flat["house_number"]) == []
    # A building's name is not a flat's number: no comma there.
    assert app_main._sale_label("FLAT 14 THE VERY LONG NAMED BUILDING 120 HIGH STREET") == \
        "Flat 14 The Very Long Named Building 120 High Street"
    assert app_main._sale_label("MALDEN HOUSE 3 MALDEN HILL GARDENS") == "Malden House 3 Malden Hill Gardens"


def test_dfix_the_which_home_links_are_nofollow(client, fake_report):
    row = _d1_row(_d1_report(client, fake_report))
    links = _d1_links(row)                      # asserts every link carries rel="nofollow"
    assert len(links) == 3 and row.count('rel="nofollow"') == 3


# D1 follow-up: without a house number, the lines that called one home's
# figures "this home" or "here" name what they describe or step aside.

def test_dfix_without_a_house_number_no_line_prices_the_newest_certificates_floor_area():
    recent = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
    comparables = [{"address": f"{n} Near Road", "date": recent, "amount": str(500000 + n * 10000), "floor_area": 100 + n}
                   for n in range(1, 6)]
    postcode_only = {"transactions": [dict(D1_SALE_55, date=recent)]}
    app_main._apply_valuation(postcode_only, comparables, 122, 2.0, "")
    psqm = postcode_only["price_per_sqm"]
    assert psqm["implied_value"] is None and psqm["subject"] is None and psqm["median"]
    page = app_main.templates.get_template("_valuation.html").module.valuation_body(
        postcode_only["valuation"], psqm, False, True, False, True)
    assert "would be worth about" not in str(page) and "Price per square metre" in str(page)
    chosen = {"transactions": [dict(D1_SALE_55, date=recent)]}
    app_main._apply_valuation(chosen, comparables, 122, 2.0, "55 Malden Hill Gardens")
    assert chosen["price_per_sqm"]["implied_value"] and chosen["price_per_sqm"]["subject_floor_area"] == 122


def test_dfix_a_postcode_reports_leasehold_line_and_epc_highlight_name_what_they_describe(client, fake_report):
    lease = dict(D1_SALE_55, tenure="Leasehold")
    certs = [dict(D1_CERTS[0], rating="C"), D1_CERTS[1]]
    # The strip shows four: the schools and prices fakes step aside for it.
    quiet = {"hpi": None, "school_landscape": None}
    body = _d1_report(client, fake_report, transactions=[lease], certificates=certs, **quiet)
    flat = _flat(body)
    assert "The most recent sale at KT3 4HX was recorded as <strong>leasehold</strong>." in flat
    assert "If the home you are buying is leasehold too, expect a service charge and ground rent." in flat
    assert "Most recent sale here" not in flat
    assert ("EPC C", "on the newest certificate here, cheaper than most to run") in _d1_highlights(body)

    body = _d1_report(client, fake_report, house_number="55+Malden+Hill+Gardens", transactions=[lease], certificates=certs, **quiet)
    flat = _flat(body)
    assert "Most recent sale here was recorded as <strong>leasehold</strong>. Expect a service charge and ground rent." in flat
    assert ("EPC C", "energy rating, cheaper than most to run") in _d1_highlights(body)


def test_dfix_the_pdfs_sale_figure_is_the_homes_own_or_named_as_the_postcodes():
    from tests.test_pdf_report import _running_costs
    report = dict(_full_pdf_report(), valuation=None)
    fig = '<span class="fig-v">{}</span><br/><span class="fig-l">{}</span>'

    def page(rc):
        ctx = app_main._pdf_context(report, rc, report["location"], "36")
        return app_main.templates.get_template("pdf_report_full.html").render(ctx)

    # 36's own sale has a price: that is the figure, as its own.
    own = dict(_running_costs(), home=dict(_running_costs()["home"], sale_amount=116000.0))
    assert fig.format("£116,000", "Last sold, 1996") in page(own)
    # Its sale has no price held: the postcode's latest, named as the postcode's.
    text = page(_running_costs())
    assert fig.format("£823,500", "Latest sale at this postcode, 2025") in text
    assert "Last sale here" not in text


# D4 and D3 follow-ups: one rounding for the EPC question, the banner's
# words on the checklist, and the district comparison's crime margin.

def test_dfix_the_epc_question_rounds_its_cost_as_the_energy_card_does():
    from app.services import solicitor_questions
    assert solicitor_questions._gbp(1240.5) == app_main._format_gbp(1240.5) == "£1,241"
    assert solicitor_questions._gbp(8400) == "£8,400" and solicitor_questions._gbp("12200.49") == "£12,200"


def test_dfix_the_checklist_names_a_flood_re_finding_as_the_banner_does():
    from app.services import overview_score, viewing_checklist
    ctx = {"property_detail": {"year_built": "2012 onwards", "dwelling_type": "Semi-detached house"},
           "flood_zone": {"zone": 2, "label": "Zone 2 (medium probability)"}}
    banner = overview_score.attention_items(ctx, premium_unlocked=False)
    checklist = viewing_checklist.build(ctx, premium_unlocked=False, questions={})
    flood = [i for i in checklist["flagged"] if i["heading"] == "Signs of past water"]
    assert [i["text"] for i in banner if i["key"] == "flood"] == [i["finding"] for i in flood]
    assert flood[0]["finding"] == "Flood Re insurance not available for this home"
    # Zone 3 is still "Flood risk" in both.
    ctx["flood_zone"] = {"zone": 3, "label": "Zone 3 (high probability)"}
    assert viewing_checklist.build(ctx, questions={})["flagged"][0]["finding"] == "Flood risk"


def test_dfix_a_district_comparison_calls_crime_fewer_only_past_the_margin():
    side = {"local_median": 250000, "imd_decile": 5}
    pair = lambda a, b: (dict(side, crime_total=a), dict(side, crime_total=b))  # noqa: E731
    faq = lambda a, b: dict(app_main._versus_faqs("LS6", "LS7", *pair(a, b)))["Which has less crime, LS6 or LS7?"]  # noqa: E731
    assert app_main._versus_differences("LS6", "LS7", *pair(229, 230)) == []
    assert faq(229, 230).startswith("About the same. LS6 recorded 229 crimes in the same period and LS7 230 (Police.uk).")
    assert "fewer" not in faq(229, 230)
    assert app_main._versus_differences("LS6", "LS7", *pair(229, 300)) == [
        "LS6 recorded fewer crimes in the same period, though busier places always record more."]
    assert faq(1834, 1500).startswith("LS7 recorded fewer crimes in the same period (1,834 in LS6 against 1,500 in LS7")
    assert app_main._versus_differences("LS6", "LS7", *pair(2, 3)) == []
    assert faq(2, 3).startswith("Too few records to say. Police.uk holds 2 in LS6 and 3 in LS7")
    assert faq(1330, 1330).startswith("Neither. Both recorded 1,330 crimes")


def test_dfix_the_extensions_crime_panel_writes_the_month_and_counts_as_the_site_does():
    js = (ROOT / "browser-extension" / "content.js").read_text(encoding="utf-8")
    panel = js.split("crime: function (data) {", 1)[1].split("account: function", 1)[0]
    assert "escapeHtml(c.month)" not in panel and "monthLabel(c.month)" in panel
    assert "countText(c.total)" in panel and "countText(r.here)" in panel
    assert 'toLocaleString("en-GB")' in js.split("function countText", 1)[1].split("}", 1)[0]


# ---- E1. The homepage puts trust before its showpieces ---------------------
# The demo report card sat above the headline, pushed the Search button to
# y=833 on a 900px screen, did nothing when clicked, and clipped its cells
# on a phone ("£147,62"). The accuracy log started on the ninth phone
# screen, behind the scroll-built report; that report's hint was a video
# editor's word; and the sources strip slid sideways for ever, which is the
# auto-sliding carousel the owner rules out.

def _hero(body):
    start = body.index('<section class="lx-hero" id="home">')
    return body[start:body.index("</section>", start)]


def _css_rules(selector_part):
    """(selector, declarations) of every innermost rule naming it."""
    css = re.sub(r"/\*.*?\*/", "", STYLE_CSS, flags=re.S)
    return [(sel.strip(), decls) for sel, decls in re.findall(r"([^{}]*)\{([^{}]*)\}", css)
            if selector_part in sel]


def test_e1_the_demo_card_is_a_link_that_follows_the_search_box_and_stays_out_of_the_tab_order(client):
    body = _fresh_home(client)
    hero = _hero(body)
    tag = re.search(r'<a class="lx-strip" id="lx-strip"[^>]*>', hero)
    assert tag, "the card is not a link inside the hero"
    tag = tag.group(0)
    assert 'href="/property?postcode=M1+1AE"' in tag
    assert 'tabindex="-1"' in tag and 'aria-hidden="true"' in tag
    assert '<div class="lx-strip"' not in body and body.count('id="lx-strip"') == 1
    # The same report the text link opens, and that link is met once.
    assert hero.count('<a href="/property?postcode=M1+1AE">See a real report for M1 1AE') == 1
    # After the headline and the search form, no longer above them.
    assert hero.index('class="lx-hero-h1"') < hero.index('<form class="lx-hero-form') < hero.index('id="lx-strip"')
    assert hero.index('class="lx-hero-sample"') < hero.index('id="lx-strip"')

    # It opens the report it is showing as it rotates, and holds still
    # under a pointer.
    script = body[body.index("const strip = document.getElementById('lx-strip');"):]
    paint = script[script.index("function paint(d)"):script.index("const ok = function")]
    assert "strip.href = '/property?postcode=' + encodeURIComponent(d.postcode).replace(/%20/g, '+');" in paint
    assert "if (strip.matches(':hover')) return;" in script[script.index("const rotate = function"):]

    base = [d for s, d in _css_rules(".lx-strip") if s == ".lx-strip" and "min-height: 152px;" in d]
    assert len(base) == 1
    assert "display: block;" in base[0] and "text-decoration: none;" in base[0] and "margin: 2rem 0 0;" in base[0]
    assert "border-color: var(--accent);" in dict(_css_rules(".lx-strip:hover"))[".lx-hero .lx-strip:hover"]
    # Two by two on a narrow phone, and a fixed height either way.
    narrow = STYLE_CSS[STYLE_CSS.index("@media (max-width: 480px) {\n    .lx-strip-rows {"):]
    narrow = narrow[:narrow.index("\n}\n")]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in narrow and "height: 188px;" in narrow


def test_e1_the_sources_the_log_and_the_reviews_come_before_the_scroll_built_report(client):
    body = _fresh_home(client)
    order = ['<section class="lx-hero" id="home">', '<section class="promo-banner">',
             '<section class="lx-section lx-about" id="about">', '<section class="accuracy-strip"',
             '<section class="lx-section lx-voices">', '<section class="lx-section lx-areas" id="areas">',
             '<section class="lx-build" id="build"', '<section class="lx-section lx-checks lx-plan" id="checks">',
             '<section class="landing-section" id="compare-alternatives">',
             '<span class="section-pill" data-animate>Questions</span>', '<section class="sources-strip"',
             '<section class="lx-section lx-contact" id="contact">']
    at = [body.index(marker) for marker in order]
    assert at == sorted(at), [marker for marker, _ in sorted(zip(order, at), key=lambda p: p[1])]
    # Nothing but the offer band between the hero and the sources paragraph.
    between = body[body.index("</section>", at[0]):at[2]]
    assert re.findall(r'<section class="([^"]+)"', between) == ["promo-banner"]
    assert "Every figure names its source" in body[at[2]:at[3]]
    assert "Read the accuracy log" in body[at[3]:at[4]]

    # Every piece still there, with its scripts: the scroll-built report
    # and its pinned scrub, the trust figures' count-up, the parallax.
    for piece in ('id="lx-build-track"', 'id="lx-build-pin"', 'id="lx-build-orb"', "Watch a report",
                  "lx-about-stats .stat-count", "document.querySelector('.lx-about-art')",
                  "targetP = Math.min(Math.max(-r.top / span, 0), 1);", "What people say",
                  "Start with an area you know", "Questions people ask first", "What buyers do instead"):
        assert body.count(piece) == 1, piece


def test_e1_the_build_hint_says_scroll_in_plain_words(client):
    body = _fresh_home(client)
    assert "the page is the scrubber" not in body
    assert '<p class="lx-build-hint" aria-hidden="true">Scroll to watch it build</p>' in body


def test_e1_the_sources_strip_stands_still(client):
    body = _fresh_home(client)
    strip = body[body.index('<section class="sources-strip"'):]
    strip = strip[:strip.index("</section>")]
    assert "marquee" not in strip and "--sources-n" not in strip
    assert '<div class="sources-strip-list">' in strip
    names = [html.unescape(n) for n in re.findall(r'class="sources-strip-name">([^<]+)<', strip)]
    assert names == [b["name"] for b in app_main.OFFICIAL_SOURCES]
    # Every link is a real, reachable one: no hidden second pass.
    assert 'aria-hidden="true" tabindex="-1"' not in strip
    assert strip.count('class="sources-strip-item"') == len(app_main.OFFICIAL_SOURCES)

    # Nothing in the sheet moves it.
    assert "sources-marquee" not in STYLE_CSS and "@keyframes sources-scroll" not in STYLE_CSS
    rules = _css_rules("sources-strip")
    assert rules
    for selector, declarations in rules:
        assert "animation" not in declarations, selector
    # On a phone it is two columns of icon and name rather than a column
    # of tiles a screen and a half long.
    phone = STYLE_CSS[STYLE_CSS.index("@media (max-width: 560px) {\n    .sources-strip {"):]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in phone[:phone.index("\n}\n")]


# ---- E2. The wait page lists answers, not datasets -------------------------
# Its rows read "Nearby sold comparables", "ONS demographics" and "Noise &
# air quality models"; the heading read "Building the report for BN11EE";
# the header box kept typing other postcodes during the wait; and on a
# phone the list ran past the first screen after row nine, so later ticks
# landed out of sight.

# Each row's publisher as the row names it, and the bodies of
# OFFICIAL_SOURCES that name stands for.
E2_PUBLISHERS = {
    "HM Land Registry": ("HM Land Registry",),
    "Local councils": ("Local councils",),
    "EPC register": ("MHCLG",),
    "Environment Agency": ("Environment Agency",),
    "Police.uk": ("Police.uk",),
    "DfE and Ofsted": ("Department for Education", "Ofsted"),
    "ONS census": ("Office for National Statistics",),
    "Defra": ("Defra",),
    "British Geological Survey": ("British Geological Survey",),
    "Mining Remediation Authority": ("Mining Remediation Authority",),
    "Ofcom": ("Ofcom",),
    "Natural England and Historic England": ("Natural England", "Historic England"),
    "MHCLG planning data": ("MHCLG",),
    "DfT Bus Open Data Service": ("Department for Transport",),
    "NHS England": ("NHS England",),
}


def _e2_wait_page(client, fake_report, location=None, query="postcode=M14%205TG"):
    fake_report(location=location)
    r = client.get("/property?" + query, headers=D5_BROWSER)
    assert r.status_code == 202
    return r.text


def _e2_script(body):
    script = body[body.index("var live = document.getElementById('building-live');"):]
    return script[:script.index("</script>")]


def test_e2_every_wait_row_is_the_buyers_question_then_an_official_publisher():
    official = {b["name"] for b in app_main.OFFICIAL_SOURCES}
    for bodies in E2_PUBLISHERS.values():
        assert set(bodies) <= official, bodies

    labels = list(app_main.GATHER_SOURCE_LABELS.values())
    assert labels == app_main.GATHER_SOURCE_ORDER and len(labels) == 19
    for label in labels:
        parts = label.split(" · ")
        assert len(parts) == 2, label
        question, publisher = parts
        assert question[0].isupper() and len(question.split()) >= 2, label
        assert publisher in E2_PUBLISHERS, label
        # The page lists what it still waits for as "A, B and C", so a
        # comma inside a row would split it in two there.
        assert "," not in label, label
        assert "—" not in label and "!" not in label, label

    for row in ("What it last sold for · HM Land Registry", "What sold nearby · HM Land Registry",
                "Flood zone and live warnings · Environment Agency", "Who lives here · ONS census",
                "Road and rail noise and air quality · Defra"):
        assert row in labels, row
    for dataset in ("Nearby sold comparables", "ONS demographics", "Noise & air quality models",
                    "Historic landfill records", "Sewage discharge records", "Planning designations"):
        assert dataset not in labels, dataset


def test_e2_the_done_list_still_names_the_rows_the_page_draws(client, fake_report):
    """The keys did not change, so a member that comes back reports the
    same string the page keys its row and dial mark by."""
    sink = app_main._new_progress_sink()

    async def go():
        app_main._progress_sink.set(sink)

        async def _zone():
            return {"zone": 1}

        await app_main._timed("flood-zones-zone-for", _zone())
        await app_main._timed("sold-prices-for-postcode", _zone())

    asyncio.run(go())
    assert sink["done"] == ["Flood zone and live warnings · Environment Agency",
                            "What it last sold for · HM Land Registry"]

    body = _e2_wait_page(client, fake_report)
    for label in app_main.GATHER_SOURCE_ORDER:
        question, publisher = label.split(" · ")
        attr = html.escape(label)
        assert body.count(f'data-source="{attr}"') == 2, label  # its row and its dial mark
        assert (f'<li class="building-item" data-source="{attr}"><span class="building-tick"></span>'
                f'<span class="building-label">{html.escape(question)} '
                f'<span class="building-publisher">· {html.escape(publisher)}</span></span></li>') in body
    # The count's word stays the one item D6 chose.
    assert f'<span id="building-done">0</span> of {len(app_main.GATHER_SOURCE_ORDER)} lookups back</p>' in body
    # The publisher takes the quieter ink the report gives a source.
    assert [d for s, d in _css_rules(".building-publisher") if s == ".building-publisher"] == [
        " color: var(--ink-faint); "]


def test_e2_the_postcode_has_its_space_in_the_report_headers_mono_face(client, fake_report):
    assert app_main._spaced_postcode("bn11ee") == "BN1 1EE"
    assert app_main._spaced_postcode(" M145TG ") == "M14 5TG"
    assert app_main._spaced_postcode("ec1a1bb") == "EC1A 1BB"
    assert app_main._spaced_postcode("SW1A  1AA") == "SW1A 1AA"
    assert app_main._spaced_postcode("m14") == "M14"
    # _home_from_query reads the same rule it was lifted from.
    assert app_main._home_from_query("m145tg", "")["postcode"] == "M14 5TG"

    body = _e2_wait_page(client, fake_report, location=fake_location(postcode="BN11EE", outcode="BN1"),
                         query="postcode=bn11ee")
    assert ('<h1 class="building-heading">Building the report for '
            '<span class="building-postcode">BN1 1EE</span></h1>') in body
    assert "BN11EE" not in body
    # The way out after a minute reads the district from the spaced form.
    assert ('href="/area/BN1"' in body) == ("BN1" in app_main.KNOWN_OUTCODES)

    rule = [d for s, d in _css_rules(".building-postcode") if s == ".building-postcode"]
    assert len(rule) == 1
    assert "font-family: var(--font-mono);" in rule[0] and "letter-spacing: normal;" in rule[0]
    assert "white-space: nowrap;" in rule[0]
    head = [d for s, d in _css_rules(".report-head h1") if s == ".report-head h1"][0]
    assert "font-family: var(--font-mono);" in head and "letter-spacing: normal;" in head


def test_e2_the_tab_carries_the_count_and_the_postcode(client, fake_report):
    body = _e2_wait_page(client, fake_report)
    assert "<title>Building the report for M14 5TG | UKPropertyInsight</title>" in body
    script = _e2_script(body)
    assert 'var postcode = "M14 5TG";' in script
    assert "function titled(text) { document.title = text + ' · ' + postcode; }" in script
    land = script[script.index("function land(src) {"):script.index("function drain()")]
    assert "titled(landed + ' of ' + total);" in land
    stop = script[script.index("function stop() {"):script.index("retryEl.addEventListener")]
    assert "titled('Stopped checking');" in stop


def test_e2_the_header_box_does_not_type_demo_postcodes_during_the_wait(client, fake_report):
    body = _e2_wait_page(client, fake_report)
    box = re.search(r'<input type="text" id="header-postcode"[^>]*>', body).group(0)
    assert "data-typing-demo" not in box
    assert 'placeholder="Postcode"' in box and box.endswith(" required>")
    # Every other page with the header box keeps its demo.
    other = re.search(r'<input type="text" id="header-postcode"[^>]*>', client.get("/methodology").text).group(0)
    assert "data-typing-demo" in other


def test_e2_on_a_phone_the_list_scrolls_inside_itself_so_the_dial_and_count_stay_on_screen(client, fake_report):
    phone = STYLE_CSS[STYLE_CSS.index("@media (max-width: 480px) {\n    .building-wrap { margin-top: 1.5rem; }"):]
    phone = phone[:phone.index("\n}\n")]
    assert ".building-list.is-fitted {" in phone and ".building-list.is-fitted:focus-visible {" in phone

    rule = [d for s, d in _css_rules(".building-list.is-fitted") if s == ".building-list.is-fitted"]
    assert len(rule) == 1
    rule = rule[0]
    assert "overflow-y: auto;" in rule and "overscroll-behavior: contain;" in rule
    # Ends at the bottom of the first screen, measured from where the list
    # starts, and never shorter than about four rows.
    assert "max-height: max(7rem, calc(100svh - var(--list-top) - 0.75rem));" in rule
    # Motion, not boxes: no border, fill or shadow, and nothing animates.
    for word in ("border", "background", "box-shadow", "animation", "transition"):
        assert word not in rule, word
    focus = [d for s, d in _css_rules(".building-list.is-fitted:focus-visible")][0]
    assert "outline: 2px solid var(--accent);" in focus and "mask-image: none;" in focus
    # Only a script that ticks rows fits the list; without one it stays whole.
    assert not [s for s, d in _css_rules(".building-list") if s == ".building-list" and "max-height" in d]

    body = _e2_wait_page(client, fake_report)
    assert '<ul class="building-list" id="building-list" aria-label="Lookups">' in body
    script = _e2_script(body)
    assert "list.classList.add('is-fitted');" in script
    assert "list.style.setProperty('--list-top', top);" in script
    assert "watch.observe(live);" in script and "watch.observe(document.querySelector('.building-overtime'));" in script
    # A landing row is brought into view by scrolling the list, never the
    # page, and without smoothing under reduced motion.
    land = script[script.index("function land(src) {"):script.index("function drain()")]
    assert "reveal(rows[src]);" in land
    assert "list.scrollBy({ top: shift, behavior: still ? 'auto' : 'smooth' });" in script
    assert "scrollIntoView" not in script and not re.search(r"window\.scroll(To|By)?\(", script)
    # A list that scrolls is a tab stop, and one that does not is not.
    assert "if (listScrolls()) list.setAttribute('tabindex', '0');" in script
    assert "else list.removeAttribute('tabindex');" in script


# ---- E3. The school page asks its question once ------------------------------
# Two "Check this postcode" boxes a screen apart, the nearest schools listed
# twice (the table, and a list the map's tick box opened), raw embed code
# between the bus times and the fee-paying schools, and district labels on
# the map that took a tap only on their type. One checker now, the upper
# one, coming back as "Check another postcode" inside an answer; one list,
# the table, which the tick box draws rings for; the badge last, after
# Common questions; and a 48 by 32 tap box round each label in both map
# branches.

E3_LAT, E3_LON = 54.8900, -2.9300           # Carlisle, a corner no other test uses
E3_PAGE = "/school/990731/eden-bank-academy"


def _e3_school(urn, name, lat, lon, miles, phase="Secondary"):
    from app.models import School, SchoolAdmissionRadius, SchoolDetail
    with db.get_session() as session:
        session.merge(School(urn=urn, name=name, phase=phase, type_name="Academy converter",
                             postcode="CA3 8AA", latitude=lat, longitude=lon))
        session.merge(SchoolDetail(urn=urn, town="Carlisle", admissions_policy="Not applicable",
                                   local_authority="Cumberland"))
        session.merge(SchoolAdmissionRadius(urn=urn, last_distance_miles=miles, academic_year="2025/26",
                                            source_authority="Cumberland"))
        session.commit()


def _e3_page(client, query=""):
    from app.services import _cache
    _e3_school(990731, "Eden Bank Academy", E3_LAT, E3_LON, 1.2)
    _e3_school(990732, "Stanwix Rise Primary", E3_LAT + 0.0050, E3_LON - 0.0050, 0.8, phase="Primary")
    _e3_school(990733, "Caldew Vale School", E3_LAT - 0.0060, E3_LON + 0.0080, 0.95)
    _e3_school(990734, "Petteril Grange High", E3_LAT - 0.0100, E3_LON - 0.0100, 25.0)  # did not limit entry
    _cache._store.clear(); _cache._bytes = 0
    r = client.get(E3_PAGE + query)
    assert r.status_code == 200
    return r.text


def _e3_check_boxes(body):
    """The postcode boxes themselves. A hidden field carrying an
    already-checked postcode through another form, so that form's own
    choice does not throw the answer away (the second-school picker and
    the budget box of 18 Sep 2026, audit item F6), is not a second
    checker: E3 is about how many boxes ask for a postcode."""
    return re.findall(r'<input type="text"[^>]*name="check"', body)


def _e3_checkers(body):
    """Every form on the page that checks a postcode against this school."""
    forms = re.findall(r'<form[^>]*action="' + E3_PAGE + r'[#"][^>]*>.*?</form>', body, re.S)
    return [f for f in forms if _e3_check_boxes(f)]


def test_e3_before_a_check_the_page_has_one_postcode_checker(client):
    body = _e3_page(client)
    forms = _e3_checkers(body)
    assert len(forms) == 1 and len(_e3_check_boxes(body)) == 1
    assert 'id="check-postcode-top"' in forms[0] and f'action="{E3_PAGE}#verdict"' in forms[0]
    assert body.count(">Check this postcode</button>") == 1
    assert "Check another postcode" not in body and 'id="verdict"' not in body
    # The house report's own box asks a different question, and stays.
    assert 'id="school-postcode"' in body and ">Run the full report</button>" in body


def test_e3_after_a_check_the_verdict_offers_another_postcode(client, monkeypatch):
    async def _lookup(_pc):
        return {"postcode": "CA3 9AA", "latitude": E3_LAT + 0.004, "longitude": E3_LON}

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    body = _e3_page(client, "?check=CA3+9AA")
    forms = _e3_checkers(body)
    assert len(forms) == 1 and len(_e3_check_boxes(body)) == 1
    assert 'id="check-postcode-top"' not in body              # the landing box gives way to the answer
    again = forms[0]
    assert '<label for="check-postcode">Check another postcode</label>' in again
    assert f'action="{E3_PAGE}#verdict"' in again and ">Check</button>" in again
    box = re.search(r'<input type="text" id="check-postcode"[^>]*>', again).group(0)
    assert "value=" not in box and 'autocomplete="postal-code"' in box and box.endswith(" required>")
    # Inside the answer, as its last row, and before the map.
    verdict = body[body.index('id="verdict"'):body.index('id="school-page-map"')]
    assert again in verdict
    assert re.search(r"<button type=\"submit\">Check</button>\s*</form>\s*</div>\s*"
                     r'<div class="school-page-map"', body)
    # The offer to save the school still follows the answer, ahead of the box.
    offer = "to save this school and be told when Cumberland republishes the distance this answer rests on"
    assert verdict.index(offer) < verdict.index("Check another postcode")
    # The row is the search form's field and button without its card.
    rule = [d for s, d in _css_rules(".admission-check-again") if s == ".admission-verdict .admission-check-again"]
    assert len(rule) == 1
    for decl in ("flex-basis: 100%;", "padding: 0;", "background: none;", "border: 0;", "box-shadow: none;"):
        assert decl in rule[0], decl

    # Signed in, the save button is still the next thing after the answer.
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, "e3-shortlist@customer.test").status_code == 303
    signed = client.get(E3_PAGE + "?check=CA3+9AA").text
    verdict = signed[signed.index('id="verdict"'):signed.index('id="school-page-map"')]
    assert verdict.index(">Save this school</button>") < verdict.index("Check another postcode")
    assert len(_e3_checkers(signed)) == 1


def test_e3_a_postcode_that_cannot_be_found_comes_back_in_the_one_box(client, monkeypatch):
    async def _lookup(_pc):
        return None

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    body = _e3_page(client, "?check=ZZ9+9ZZ")
    forms = _e3_checkers(body)
    assert len(forms) == 1 and len(_e3_check_boxes(body)) == 1
    box = re.search(r'<input type="text" id="check-postcode-top"[^>]*>', forms[0]).group(0)
    assert 'value="ZZ9 9ZZ"' in box and 'aria-invalid="true"' in box and 'aria-describedby="check-error"' in box
    assert 'id="check-error"' in forms[0]
    assert "Couldn't find \"ZZ9 9ZZ\" as a UK postcode. Check the spelling and try again." in forms[0]
    assert body.count("as a UK postcode") == 1 and 'id="verdict"' not in body
    # A page with no error says nothing of one.
    clean = _e3_page(client)
    assert 'id="check-error"' not in clean and "aria-invalid" not in clean


def test_e3_the_nearest_schools_are_listed_once_in_the_table_the_tick_box_draws(client):
    body = _e3_page(client)
    for urn, slug in ((990732, "stanwix-rise-primary"), (990733, "caldew-vale-school"),
                      (990734, "petteril-grange-high")):
        assert body.count(f'href="/school/{urn}/{slug}"') == 1, slug
    assert 'id="nearby-rings-legend"' not in body and 'class="map-legend"' not in body
    assert _css_rules(".map-legend") == []
    toggle = _flat(re.search(r'<label class="map-toggle">(.*?)</label>', body, re.S).group(1))
    # Worded for what it draws (fix pass): the table below also lists the
    # school with no limit, which gets no ring.
    assert toggle.endswith("Draw the admission distances of the 2 nearest schools in the table below that have a limit")
    # The table follows the map's section, ahead of the picture and the
    # school's own facts, so the rings and their names are a scroll apart.
    at = [body.index(s) for s in ('id="school-page-map"', 'id="nearby-rings-toggle"', "This is not a catchment area",
                                  '<section class="report-section" id="nearby-schools">',
                                  "<h2>Other schools nearby with a published distance</h2>",
                                  "<h2>The distance as a picture</h2>")]
    assert at == sorted(at)
    table = body.split('id="nearby-schools"', 1)[1].split("</section>", 1)[0]
    assert "Stanwix Rise Primary" in table and "Caldew Vale School" in table and "No limit" in table
    # The tick box draws rings for the two with a limit, and neither
    # script looks for the list any more.
    assert '"url": "/school/990732/stanwix-rise-primary"' in body and '"url": "/school/990733/caldew-vale-school"' in body
    assert '"url": "/school/990734/' not in body
    scripts = body[body.index("window.SCHOOL_PAGE = {"):]
    assert "legend" not in scripts[:scripts.index("})();")]


def test_e3_the_toggle_names_a_single_school_in_the_singular(client):
    from app.models import SchoolAdmissionRadius
    from app.services import _cache
    _e3_page(client)
    with db.get_session() as session:
        session.merge(SchoolAdmissionRadius(urn=990733, last_distance_miles=30.0, academic_year="2025/26",
                                            source_authority="Cumberland"))
        session.commit()
    try:
        _cache._store.clear(); _cache._bytes = 0
        body = client.get(E3_PAGE).text
    finally:
        _e3_school(990733, "Caldew Vale School", E3_LAT - 0.0060, E3_LON + 0.0080, 0.95)
    toggle = _flat(re.search(r'<label class="map-toggle">(.*?)</label>', body, re.S).group(1))
    assert toggle.endswith("Draw the admission distance of the nearest school in the table below that has a limit")


def test_e3_the_embed_badge_comes_last_after_common_questions(client):
    from app.models import BusStop
    with db.get_session() as session:
        session.merge(BusStop(atco_code="E3STOP", name="Eden Bank Road", latitude=E3_LAT + 0.0009, longitude=E3_LON,
                              weekday_day=24, weekday_eve=6, sunday_day=12, weekday_first="06:40", weekday_last="22:50",
                              routes='["60"]', feed_date=datetime.date(2026, 9, 7), ref_weekday=datetime.date(2026, 9, 8),
                              ref_sunday=datetime.date(2026, 9, 13)))
        session.commit()
    try:
        body = _e3_page(client)
    finally:
        with db.get_session() as session:
            session.query(BusStop).filter(BusStop.atco_code == "E3STOP").delete()
            session.commit()
    bus = body.index("<h2>Getting to Eden Bank Academy by bus</h2>")
    fees = body.index("<h2>The fee-paying alternative</h2>")
    faq = body.index("<h2>Common questions</h2>")
    badge = body.index("<h2>Put Eden Bank Academy's figure on your own site</h2>")
    assert bus < fees < faq < badge
    assert body.count('class="embed-code"') == 1 and body.index('class="embed-code"') > badge
    assert body.count("/school/990731/badge.svg") == 2 and body.index("/school/990731/badge.svg") > badge
    # The last section on the page: only the sources line and the scripts follow.
    assert body.rindex('<section class="report-section"') < badge


def test_e3_the_district_labels_take_a_32px_tap_in_both_map_branches(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "")
    leaflet = _e3_page(client)
    assert "districts: [" in leaflet and "districts: []" not in leaflet
    assert "L.divIcon({ className: 'map-district-label', html: d.code, iconSize: [48, 32] })" in leaflet
    assert "iconSize: null" not in leaflet
    rule = [d for s, d in _css_rules(".map-district-label") if s == ".map-district-label"]
    assert len(rule) == 1
    assert "font: 600 12px/32px var(--font-sans);" in rule[0] and "text-align: center;" in rule[0]
    assert "transform" not in rule[0]
    # The look is as it was: the same type, colour and halo, on no ground.
    for kept in ("color: #1c1714;", "white-space: nowrap;", "background: transparent;", "border: 0;",
                 "text-shadow: 0 0 3px #fff, 0 0 3px #fff, 0 0 3px #fff;", "cursor: pointer;"):
        assert kept in rule[0], kept

    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "e3-test-key")
    google = _e3_page(client)
    assert "initSchoolPageMap" in google and "L.divIcon" not in google
    script = google[google.index("function initSchoolPageMap()"):]
    assert "legend" not in script[:script.index("</script>")]      # the rings' list is gone here too
    hit = google[google.index("const districtHit = {"):google.index("(S.districts || []).forEach")]
    assert "path: 'M -24 -16 H 24 V 16 H -24 Z', scale: 1," in hit
    assert "fillOpacity: 0, strokeOpacity: 0, strokeWeight: 0" in hit
    districts = google[google.index("(S.districts || []).forEach"):google.index("if (S.check) {")]
    assert "icon: districtHit," in districts and "scale: 0" not in districts
    assert ("label: { text: d.code, fontSize: '12px', fontWeight: '600', color: '#1c1714', "
            "className: 'map-district-label-g' }") in districts


# ---- E4. The admissions hub saves a school and says what it holds ----------
# Signed out, the hub said "Sign up to save the schools you care about", and
# signed in there was nothing to save on any row. Birmingham's hub held only
# primary schools from the 2023/24 round and never said so. Each row now has
# Save for a signed-in account, or Saved for a school already on the
# shortlist, read in one statement for the page; a save returns to its row.
# Under the tiles a line names the phases and rounds the hub's rows hold and
# the phase it does not, then the closing date for each phase held, from the
# school page's own helper. A hub holding one phase names it in its title and
# description. Signed out, the sign-up link stays generic: saving is a POST
# and sign-up returns by a GET, which could only carry the school through a
# GET that writes. The imported rows hold no link to a council's admissions
# page, so none is given.

E4_ONLY = "/schools/admissions/ormsby-fen"          # three primaries, 2023/24
E4_BOTH = "/schools/admissions/fenholt-marsh"       # a primary and a secondary, different rounds
E4_TODAY = datetime.date(2026, 9, 18)


def _e4_school(urn, name, phase, miles, year, authority):
    from app.models import School, SchoolAdmissionRadius, SchoolDetail
    with db.get_session() as session:
        session.merge(School(urn=urn, name=name, phase=phase, type_name="Community school",
                             postcode="PE12 6AA", latitude=52.79, longitude=0.15,
                             ofsted_rating=2, ofsted_rating_label="Good"))
        session.merge(SchoolDetail(urn=urn, town="Holbeach", admissions_policy="Not applicable",
                                   local_authority=authority))
        session.merge(SchoolAdmissionRadius(urn=urn, last_distance_miles=miles, academic_year=year,
                                            source_authority=authority))
        session.commit()


def _e4_page(client, path, monkeypatch=None):
    from app.services import _cache
    for urn, name, miles in ((990941, "Ormsby Mill Primary", 0.21), (990942, "Fen Drove Primary", 0.64),
                             (990943, "Ormsby Church Primary", 1.37)):
        _e4_school(urn, name, "Primary", miles, "2023/24", "Ormsby Fen")
    _e4_school(990944, "Fenholt Marsh Infants", "Primary", 0.5, "2025/26", "Fenholt Marsh")
    _e4_school(990945, "Fenholt Marsh Academy", "Secondary", 2.4, "2024/25", "Fenholt Marsh")
    if monkeypatch is not None:
        # The deadlines move with the date; the page is read as on 18 Sep 2026.
        coverage = app_main._admissions_hub_coverage
        monkeypatch.setattr(app_main, "_admissions_hub_coverage", lambda council, today=None: coverage(council, E4_TODAY))
    _cache._store.clear(); _cache._bytes = 0
    r = client.get(path)
    assert r.status_code == 200
    return r.text


def _e4_row(body, urn):
    return re.search(rf'<tr id="school-{urn}">(.*?)</tr>', body, re.S).group(1)


def _e4_text(fragment):
    return _flat(re.sub(r"<[^>]+>", " ", fragment)).replace(" ,", ",").replace(" .", ".")


def _e4_account(client, monkeypatch, email):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, email).status_code == 303
    with db.get_session() as session:
        return auth.find_user_by_email(session, email).id


def test_e4_signed_in_each_row_has_save_and_a_shortlisted_school_reads_saved(client, monkeypatch):
    from app import school_shortlist
    uid = _e4_account(client, monkeypatch, "e4-hub@customer.test")
    school_shortlist.save_item(uid, 990942, "")
    body = _e4_page(client, E4_ONLY)
    assert "Save a school on its row to keep it on" in body and 'href="/signup?next=' not in body
    for urn, name in ((990941, "Ormsby Mill Primary"), (990943, "Ormsby Church Primary")):
        row = _e4_row(body, urn)
        assert row.count('<form action="/schools/shortlist/save" method="post" class="hub-save-form">') == 1
        assert f'<input type="hidden" name="urn" value="{urn}">' in row
        assert f'<input type="hidden" name="next" value="{E4_ONLY}#school-{urn}">' in row
        assert f'Save<span class="visually-hidden"> {name} to your shortlist</span></button>' in row
        assert "Saved" not in row
    saved = _e4_row(body, 990942)
    assert '<a href="/schools/shortlist" class="hub-saved">Saved<span class="visually-hidden">: Fen Drove Primary is on your shortlist</span></a>' in saved
    assert "/schools/shortlist/save" not in saved
    # The column sits last and out of the sort, so the sort script's
    # header positions still match the cells it reads.
    head = re.search(r"<thead>(.*?)</thead>", body, re.S).group(1)
    headers = re.findall(r"<th[^>]*>", head)
    assert headers[-1] == '<th class="hub-save">' and all("data-sort" in h for h in headers[:-1])
    assert "Your shortlist" in head

    # A save goes back to that row, which now reads Saved.
    r = client.post("/schools/shortlist/save", data={"urn": "990941", "next": f"{E4_ONLY}#school-990941"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"{E4_ONLY}#school-990941"
    again = _e4_page(client, E4_ONLY)
    assert 'class="hub-saved">Saved<' in _e4_row(again, 990941)
    assert 'class="hub-saved">Saved<' not in _e4_row(again, 990943)


def test_e4_signed_out_no_row_has_a_control_and_the_sign_up_link_stays_generic(client):
    body = _e4_page(client, E4_ONLY)
    page = body[body.index("<h1>"):]                # the stylesheet is inlined in the head
    assert "/schools/shortlist/save" not in page and "hub-save" not in page and "hub-saved" not in page
    assert body.count(f'href="/signup?next={E4_ONLY}"') == 1
    assert "Save a school on its row" not in body
    # Every row keeps its anchor and its five cells.
    for urn in (990941, 990942, 990943):
        assert len(re.findall(r"<td[ >]", _e4_row(body, urn))) == 5


def test_e4_the_shortlist_is_read_in_one_statement_however_many_it_holds(client, monkeypatch):
    from sqlalchemy import event
    from app import school_shortlist
    uid = _e4_account(client, monkeypatch, "e4-rounds@customer.test")
    school_shortlist.save_item(uid, 990941, "")

    def _one_by_one(*_a, **_k):
        raise AssertionError("the hub read the shortlist one school at a time")

    monkeypatch.setattr(app_main.school_shortlist, "list_items", _one_by_one)
    engine = db._get_engine()

    def _shortlist_reads():
        seen = []

        def _count(_conn, _cursor, statement, *_rest):
            if "school_shortlist_items" in statement:
                seen.append(statement)

        event.listen(engine, "before_cursor_execute", _count)
        try:
            body = _e4_page(client, E4_ONLY)
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        return body, seen

    body, one = _shortlist_reads()
    assert body.count('class="hub-saved">Saved<') == 1 and len(one) == 1
    for urn in (990942, 990943, 990944):
        school_shortlist.save_item(uid, urn, "")
    body, four = _shortlist_reads()
    assert body.count('class="hub-saved">Saved<') == 3 and len(four) == 1
    assert school_shortlist.saved_urns(uid) == {990941, 990942, 990943, 990944}


def test_e4_a_primary_only_hub_says_so_with_its_round_and_names_the_phase(client, monkeypatch):
    body = _e4_page(client, E4_ONLY, monkeypatch)
    line = '<p class="section-sub hub-coverage">Primary schools only, from the 2023/24 round. Secondary distances are not on this page.</p>'
    assert body.count(line) == 1
    # Under the tiles, ahead of the tightest and the tables.
    at = [body.index(s) for s in ('class="scorecard-row"', line, "The tightest is", "<h2>Primary schools</h2>")]
    assert at == sorted(at)
    assert "<title>Ormsby Fen primary school catchments: how far each school admitted from</title>" in body
    assert '<meta name="description" content="How far the last child admitted lived, for 3 Ormsby Fen primary schools, from' in body
    assert "<h1>How far Ormsby Fen primary schools admitted from</h1>" in body
    assert "This is that figure for 3 primary schools, tightest first." in _flat(body)
    assert "Ormsby Fen primary school admission distances (last distance offered)" in body
    # The school page's deadline line, for the one phase held.
    deadline = re.search(r'<p class="section-sub admission-deadline">(.*?)</p>', body, re.S).group(1)
    assert _e4_text(deadline) == (
        "Applying for September 2027? Primary applications close on 15 January 2027, with offers on "
        "16 April 2027. The address on the deadline is the one the council uses. How it works.")
    assert '<strong>15 January 2027</strong>' in deadline and 'href="/schools/how-admissions-work"' in deadline
    assert "Secondary applications" not in body


def test_e4_a_hub_with_both_phases_gives_each_its_round_and_deadline_and_keeps_its_title(client, monkeypatch):
    body = _e4_page(client, E4_BOTH, monkeypatch)
    assert ('<p class="section-sub hub-coverage">Primary schools from the 2025/26 round and secondary schools '
            'from the 2024/25 round.</p>') in body
    assert "are not on this page" not in body
    assert "<title>Fenholt Marsh school catchments: how far each school admitted from</title>" in body
    assert "<h1>How far Fenholt Marsh schools admitted from</h1>" in body
    deadline = re.search(r'<p class="section-sub admission-deadline">(.*?)</p>', body, re.S).group(1)
    assert _e4_text(deadline) == (
        "Applying for September 2027? Secondary applications close on 31 October 2026, with offers on "
        "1 March 2027; primary applications close on 15 January 2027, with offers on 16 April 2027. "
        "The address on the deadline is the one the council uses. How it works.")


def test_e4_the_coverage_line_and_the_deadlines_follow_the_rows():
    def rows(*years):
        return [{"academic_year": y} for y in years]

    both_same = app_main._admissions_hub_coverage(
        {"by_phase": [("Primary", rows("2025/26", "2025/26")), ("Secondary", rows("2025/26"))]}, E4_TODAY)
    assert both_same["sentence"] == "Primary and secondary schools, from the 2025/26 round."
    assert both_same["only_phase"] == "" and both_same["phase_word"] == ""

    secondary = app_main._admissions_hub_coverage(
        {"by_phase": [("Secondary", rows("2025/26", "2024/25", ""))]}, E4_TODAY)
    assert secondary["sentence"] == ("Secondary schools only, from the 2024/25 and 2025/26 rounds. "
                                     "Primary distances are not on this page.")
    assert secondary["only_phase"] == "secondary" and secondary["phase_word"] == "secondary "
    assert [[d["phase"] for d in g["items"]] for g in secondary["deadline_groups"]] == [["secondary"]]

    # A round that was not recorded is left out, never guessed.
    nursery = app_main._admissions_hub_coverage({"by_phase": [("Nursery", rows(""))]}, E4_TODAY)
    assert nursery["sentence"] == "Nursery schools only. Primary and secondary distances are not on this page."
    assert nursery["deadline_groups"] == []          # not admitted through the national closing dates

    mixed = app_main._admissions_hub_coverage(
        {"by_phase": [("Primary", rows("2025/26")), ("Special", rows(""))]}, E4_TODAY)
    assert mixed["sentence"] == ("Primary schools from the 2025/26 round and special schools with no round "
                                 "recorded. Secondary distances are not on this page.")

    # Between the October and January deadlines the two phases are for
    # different Septembers, so each gets its own question.
    december = app_main._admissions_hub_coverage(
        {"by_phase": [("Primary", rows("2025/26")), ("Secondary", rows("2025/26"))]}, datetime.date(2026, 12, 1))
    assert [(g["entry_year"], [(d["phase"], d["deadline_text"]) for d in g["items"]])
            for g in december["deadline_groups"]] == [
        (2027, [("primary", "15 January 2027")]), (2028, [("secondary", "31 October 2027")])]
    # The same helper the school page renders from.
    assert december["deadline_groups"][0]["items"][0] == app_main._admissions_deadline("Primary", datetime.date(2026, 12, 1))

    assert app_main._admissions_hub_coverage({"by_phase": []}, E4_TODAY) == {
        "sentence": "", "only_phase": "", "phase_word": "", "deadline_groups": []}


def test_e4_the_save_column_is_drawn_from_tokens_and_keeps_focus_visible():
    rules = dict((s, d) for s, d in _css_rules(".hub-save") if s in (".hub-save-form", ".hub-saved"))
    assert "display: inline;" in rules[".hub-save-form"]
    assert "color: var(--good);" in rules[".hub-saved"] and "#" not in rules[".hub-saved"]
    target = [d for s, d in _css_rules("tr:target") if s == ".school-table tr:target"]
    assert len(target) == 1 and target[0].strip() == "background-color: var(--accent-soft);"
    # Nothing here removes the site's focus ring.
    assert all("outline" not in d for s, d in _css_rules("hub-save"))


# ---- E5. Dead ends and small labels ------------------------------------------
# Links on the content pages sent a reader to the homepage and lost the
# council, district or postcode behind the click: "full property report" on
# the admissions pages and private schools, "Run a free report" on council
# tax, "search it directly" on the market report and the area guide's "Check
# sold prices for a specific address". Each now goes to a postcode box on the
# page it is on, the page's own where it has one and _address_box.html where
# it had none. The schools guide for a full postcode links that postcode's
# report and carries the share row. The buying guide links the free stamp
# duty calculator, names only checks in FREE_CHECKS and dates the base rate
# in words. /areas says its box takes a district, filters its councils as
# you type, and files NG21 with East Midlands. Sign-up and My properties
# name the alert triggers. The menu says "Chrome extension", the log-in page
# on the way to My properties says so and carries next to sign-up, and the
# 404 page drops the header's box and links running costs and council tax.

def _e5_goes_to_its_box(body, words, box_id):
    """The link with these words points at the box on this page, and the
    box is one that runs the report."""
    assert f'<a href="/">{words}</a>' not in body, words
    assert body.count(f'<a href="#{box_id}">{words}</a>') == 1, words
    form = re.search(r'<form action="/property" method="get"[^>]*>((?:(?!</form>).)*?id="' + box_id + r'".*?)</form>',
                     body, re.S)
    assert form, f"no report box holds #{box_id}"
    field = re.search(r'<input type="text" id="' + box_id + r'"[^>]*>', form.group(1)).group(0)
    assert 'name="postcode"' in field and "required" in field
    src = re.search(r'<input type="hidden" name="src" value="([^"]*)">', form.group(1))
    return form.group(1), src.group(1) if src else None


def _e5_address_box(body, words, box_id, label, src):
    """A box from _address_box.html: labelled, with a house number, the
    offer sentence under it and a source the admin page counts."""
    form, found_src = _e5_goes_to_its_box(body, words, box_id)
    assert f'<label for="{box_id}">{label}</label>' in form
    assert f'id="{box_id}-house" name="house_number"' in form
    assert '<button type="submit">Run the free report</button>' in form
    assert html.escape(app_main.OFFER_SENTENCE, quote=False) in form
    assert found_src == src and src in app_main.REPORT_SOURCES
    return form


def test_e5_the_admissions_pages_send_the_report_link_to_a_box_on_the_page(client, monkeypatch):
    # Whatever schools the shared database holds: the box does not depend on them.
    index = client.get("/schools/admissions").text
    box = _e5_address_box(index, "full property report", "admissions-check-postcode",
                          "Check an address against every school near it", "admissions-index")
    # In the section that asks the question, after the words.
    section = index.split("<h2>How do I check one address?</h2>", 1)[1].split("</section>", 1)[0]
    assert box in section and section.index("full property report") < section.index("<form")

    tightest = client.get("/schools/tightest-catchments").text
    _e5_address_box(tightest, "full property report", "tightest-check-postcode",
                    "Check an address against every school near it", "tightest")

    # A council's hub already had its own box, and the link now goes to it.
    hub = _e4_page(client, E4_ONLY)
    form, src = _e5_goes_to_its_box(hub, "full property report", "hub-check-postcode")
    assert src == "council-hub" and hub.count('action="/property"') == 1


def test_e5_the_private_schools_and_market_pages_send_their_links_to_a_box(client, monkeypatch):
    school = {"name": "Whitworth House School", "website": "", "town": "Stackford", "postcode": "M1 3CC",
              "age_low": 3, "age_high": 18, "gender": "Girls", "religious_character": "None",
              "number_on_roll": 300, "occupancy_pct": 75}
    district = {"name": "Stackford", "slug": "stackford", "count": 1, "mainstream": 1, "special": 0,
                "single_sex": 1, "with_sixth_form": 1, "pupils": 300,
                "groups": [("Mainstream schools", "By their registered age ranges.", [school])]}
    body = _d2_page(client, monkeypatch, "/schools/independent/stackford", "independent_district", district)
    _e5_address_box(body, "full property report", "independent-check-postcode",
                    "Check an address in Stackford", "independent")

    market = _d6_market_report(client, monkeypatch, D6_MARKET_ROWS)
    box = _e5_address_box(market, "search it directly", "market-check-postcode", "Check an address", "market-report")
    section = market.split("<h2>Check a specific area</h2>", 1)[1].split("</section>", 1)[0]
    assert box in section


def test_e5_council_tax_and_the_area_guide_link_to_their_own_boxes(client, monkeypatch):
    import time
    from app.services import _cache
    from tests.test_ai_search_readiness import AREA_PAYLOAD, _forget_html

    council = client.get("/running-costs/council-tax/basildon").text
    _, src = _e5_goes_to_its_box(council, "A free report", "ct-check-postcode")
    assert src == "council-tax"

    async def _resolve(outcode):
        return fake_location(postcode=f"{outcode} 2AA", outcode=outcode), True

    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    key = ("area_guide", app_main.AREA_GUIDE_PAYLOAD_VERSION, "AB12")
    bodies = {}
    for name, payload in (("data", dict(AREA_PAYLOAD)), ("no data", {"has_data": False})):
        _cache._put(key, time.time(), payload)
        _forget_html()
        try:
            bodies[name] = client.get("/area/AB12").text
        finally:
            _cache._evict(key)
            _forget_html()
    guide = bodies["data"]
    _, src = _e5_goes_to_its_box(guide, "Check sold prices for a specific address in AB12 →", "area-check-postcode")
    assert src == "area-guide"
    assert "/#postcode=" not in guide
    # The district is carried: the box is labelled for it.
    assert '<label for="area-check-postcode">Check an address in AB12</label>' in guide
    # The notice shown when the district's data could not be read.
    quiet = bodies["no data"]
    assert "We couldn't pull any data for AB12 right now" in quiet
    _e5_goes_to_its_box(quiet, "search an address in AB12 directly", "area-check-postcode")
    assert "/#postcode=" not in quiet

    # None of the named templates links its report words to the homepage.
    for name, words in (("schools_admissions_council.html", "full property report"),
                        ("schools_admissions_index.html", "full property report"),
                        ("schools_tightest.html", "full property report"),
                        ("schools_independent_district.html", "full property report"),
                        ("market_report.html", "search it directly"),
                        ("council_tax_council.html", "A free report"),
                        ("area_guide.html", "Check sold prices for a specific address in")):
        text = _without_template_comments((ROOT / "app" / "templates" / name).read_text(encoding="utf-8"))
        assert not re.search(r'<a href="/(?:#[^"]*)?">' + re.escape(words), text), name


def test_e5_the_schools_guide_for_a_postcode_links_its_report_and_can_be_shared(client, monkeypatch):
    from tests.test_ai_search_readiness import _forget_html
    from tests.test_brainstorm_17sep import _patch_guide

    _patch_guide(monkeypatch, {"latitude": 53.4502, "longitude": -2.2202, "label": "M14 5TX", "kind": "postcode"})
    _forget_html()
    body = client.get("/schools/guide?q=M14+5TX").text
    line = re.findall(r'<p class="section-sub guide-report-link">\s*(.*?)\s*</p>', body, re.S)
    assert line == ['<a href="/property?postcode=M14%205TX">M14 5TX itself: sold prices, flood, running costs '
                    'and the rest &rarr;</a>']
    # One line above the table, then the share row, then the table.
    at = [body.index(s) for s in ('class="section-sub guide-report-link"', 'class="share-send-row"',
                                  'id="school-table-0"')]
    assert at == sorted(at)
    share = body[at[1]:at[2]]
    for label in (">WhatsApp</a>", ">Email</a>", ">Copy link</button>"):
        assert share.count(label) == 1, label
    copy = re.search(r'data-copy-link="([^"]*)"', share).group(1)
    assert copy.endswith("/schools/guide?q=M14%205TX") and copy.startswith("http")
    assert "Schools%20near%20M14%205TX" in share          # the message names the postcode
    assert body.count('class="share-send-row"') == 1

    # A district or a town has no report of its own to link, and no row.
    _patch_guide(monkeypatch, {"latitude": 53.4502, "longitude": -2.2202, "label": "M14", "kind": "outcode"})
    _forget_html()
    district = client.get("/schools/guide?q=M14").text
    assert "guide-report-link" not in district and 'class="share-send-row"' not in district

    # Compared with a district, the postcode keeps its line; the row is for
    # a guide to that one postcode.
    two = app_main._areas_param([
        {"latitude": 53.4502, "longitude": -2.2202, "label": "M14 5TX", "kind": "postcode"},
        {"latitude": 53.46, "longitude": -2.23, "label": "M14", "kind": "outcode"}])
    _forget_html()
    both = client.get("/schools/guide", params={"areas": two}).text
    assert "From M14 5TX" in both
    assert both.count('class="section-sub guide-report-link"') == 1 and 'class="share-send-row"' not in both


def _e5_buying_guide(client, monkeypatch):
    from tests.test_ai_search_readiness import _forget_html

    async def _rate():
        return {"rate": 4.0, "since": "2025-12-18", "history": [
            {"date": "2020-03-19", "rate": 0.1}, {"date": "2024-08-01", "rate": 5.0},
            {"date": "2025-12-18", "rate": 4.0}]}

    monkeypatch.setattr(app_main.boe_rate, "current_rate", _rate)
    _forget_html()
    r = client.get("/buying-guide")
    assert r.status_code == 200
    return r.text


def test_e5_the_buying_guide_links_the_free_calculator_and_dates_the_rate_in_words(client, monkeypatch):
    body = _e5_buying_guide(client, monkeypatch)
    assert '<span class="snap-stat-label">since 18 December 2025</span>' in body
    assert "2025-12-18</span>" not in body
    rule = [d for s, d in _css_rules(".snap-stat-label") if s == ".snap-stat-label"]
    assert len(rule) == 1 and "text-transform" not in rule[0]
    assert app_main._day_label("2025-12-18", full_month=True) == "18 December 2025"
    assert app_main._day_label("2025-12-18") == "18 Dec 2025"            # every other date as it was

    tax = _flat(body.split('id="tax"', 1)[1].split("</section>", 1)[0])
    assert "kept current" not in tax and "The calculator on any property report here" not in tax
    assert ('The free <a href="/tools/stamp-duty-calculator">stamp duty calculator</a> does the maths on any price '
            "in England and Northern Ireland, Scotland or Wales, and every property report here carries the same "
            "calculator, free, for that home.") in tax
    # The report's calculator is on a free card.
    assert any(title == "Costs & Affordability" for _, title, _, _ in app_main.FREE_CHECKS)
    assert client.get("/tools/stamp-duty-calculator").status_code == 200


def test_e5_the_buying_guide_names_only_free_checks_and_links_a_box(client, monkeypatch):
    body = _e5_buying_guide(client, monkeypatch)
    tips = body.split('id="tips"', 1)[1].split("</section>", 1)[0]
    item = _flat(re.search(r"<li><strong><a href=\"#guide-check-postcode\">Run the free report first\.</a></strong>"
                           r"(.*?)</li>", tips, re.S).group(1))
    assert item == (f"{len(app_main.FREE_CHECKS)} checks free on any address, among them flood, surface water, "
                    "radon, noise, planning constraints, sold prices, the EPC, crime and schools, before you pay anyone.")
    for locked in ("subsidence", "contamination", "mining"):
        assert locked not in item.lower(), locked
    # Every name the guide uses is a free check's, never a locked one's.
    free = {title for _, title, _, _ in app_main.FREE_CHECKS}
    locked = {title for _, title, _, _ in app_main.PREMIUM_CHECKS}
    for title, _words in app_main.BUYING_GUIDE_FREE_NAMES:
        assert title in free and title not in locked, title
    _e5_address_box(body, "Run the free report first.", "guide-check-postcode", "Check an address", "buying-guide")
    assert tips.count('<form action="/property"') == 1

    # A check that moves behind the wall leaves the sentence, and the
    # count follows the list.
    monkeypatch.setattr(app_main, "FREE_CHECKS", tuple(c for c in app_main.FREE_CHECKS if c[1] != "Radon Gas"))
    moved = _flat(_e5_buying_guide(client, monkeypatch).split('id="tips"', 1)[1].split("</section>", 1)[0])
    assert f"{len(app_main.FREE_CHECKS)} checks free on any address" in moved
    assert "surface water, noise," in moved and "radon" not in moved


E5_AREAS_HARNESS = r"""
const script = require('fs').readFileSync(process.argv[2], 'utf8');
const spec = JSON.parse(process.argv[3]);
const typed = JSON.parse(process.argv[4]);
function el(extra) { return Object.assign({ hidden: false }, extra); }
const box = el({ hidden: true }), status = el({ textContent: '' });
let onInput = null;
const input = el({ value: '', addEventListener: function (type, fn) { if (type === 'input') onInput = fn; } });
const jumps = {};
const sections = spec.map(function (region) {
    jumps['#' + region.id] = el({});
    const blocks = region.councils.map(function (c) {
        return el({
            council: c.name,
            querySelector: function (sel) { return sel === 'h3' ? { textContent: ' ' + c.name + ' ' } : null; },
            querySelectorAll: function (sel) {
                return sel === '.areas-codes a' ? c.codes.map(function (code) { return { textContent: code }; }) : [];
            },
        });
    });
    return el({ id: region.id, blocks: blocks,
                querySelectorAll: function (sel) { return sel === '.areas-district' ? blocks : []; } });
});
global.document = {
    getElementById: function (id) {
        return { 'areas-filter': box, 'areas-filter-input': input, 'areas-filter-status': status }[id] || null;
    },
    querySelectorAll: function (sel) { return sel === '.areas-region' ? sections : []; },
    querySelector: function (sel) { const m = sel.match(/href="(#[^"]+)"/); return m ? (jumps[m[1]] || null) : null; },
};
eval(script);
const out = { shown_box: !box.hidden, states: [] };
typed.forEach(function (t) {
    input.value = t;
    onInput();
    out.states.push({
        status: status.textContent,
        councils: [].concat.apply([], sections.map(function (s) {
            return s.blocks.filter(function (b) { return !b.hidden; }).map(function (b) { return b.council; });
        })),
        regions: sections.filter(function (s) { return !s.hidden; }).map(function (s) { return s.id; }),
        jumps: Object.keys(jumps).filter(function (k) { return !jumps[k].hidden; }),
    });
});
console.log(JSON.stringify(out));
"""


def test_e5_the_areas_box_says_it_takes_a_district(client):
    body = client.get("/areas").text
    form = body.split('<form action="/property" method="get" class="search-form area-check" role="search">', 1)[1]
    form = form.split("</form>", 1)[0]
    assert '<label for="areas-postcode">Find a district or check an address</label>' in form
    field = re.search(r'<input type="text" id="areas-postcode"[^>]*>', form).group(0)
    assert 'placeholder="e.g. LS6 or SW1A 1AA"' in field and "data-typing-demo" in field
    assert "Check one address instead" not in body
    note = _flat(form.split('<p class="section-sub" style="margin: 0.5rem 0 0;">', 1)[1].split("</p>", 1)[0])
    assert note.startswith("A district such as LS6 opens its area guide. A full postcode gets sold prices")
    # Which is what the box does with one.
    r = client.get("/property?postcode=LS6&src=areas", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/area/LS6"


def test_e5_the_areas_filter_is_hidden_without_a_script_and_hides_councils_as_you_type(client, tmp_path):
    import shutil
    import subprocess
    import pytest
    body = client.get("/areas").text
    wrapper = re.search(r'<div class="address-filter-field areas-filter" id="areas-filter"( hidden)?>(.*?)</div>',
                        body, re.S)
    assert wrapper and wrapper.group(1) == " hidden", "without a script the whole index shows and no control"
    assert '<label for="areas-filter-input">Filter the list below by district or council</label>' in wrapper.group(2)
    assert 'role="status" aria-live="polite"' in wrapper.group(2)
    # Between the box and the index, and no request: the script reads the page.
    assert body.index('id="areas-postcode"') < body.index('id="areas-filter"') < body.index('class="areas-jump"')
    scripts = [s for s in re.findall(r"<script>(.*?)</script>", body, re.S) if "areas-filter-input" in s]
    assert len(scripts) == 1
    script = scripts[0]
    assert "fetch(" not in script and "XMLHttpRequest" not in script
    for selector in (".areas-filter[hidden]", ".areas-region[hidden]", ".areas-district[hidden]", ".areas-jump a[hidden]"):
        assert any(selector in s and "display: none;" in d for s, d in _css_rules(selector)), selector
    # Names are matched as words: both sides are cut at punctuation and led
    # by a space, so a match can only begin a word.
    assert "return ' ' + text.toLowerCase().replace(/[^a-z0-9\\u00c0-\\u024f]+/g, ' ').trim();" in script
    assert "var name = words(typed);" in script and "name: words(block.querySelector('h3').textContent)," in script

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed; the filter is pinned by the source checks above")
    spec = [
        {"id": "region-1", "councils": [{"name": "Gedling", "codes": ["NG4", "NG21"]},
                                         {"name": "Nottingham", "codes": ["NG1", "NG2", "NG7"]}]},
        {"id": "region-2", "councils": [{"name": "Leeds", "codes": ["LS1", "LS6", "LS10"]}]},
        {"id": "region-3", "councils": [{"name": "Westminster", "codes": ["SW1A", "W1B"]},
                                         {"name": "Kensington and Chelsea", "codes": ["SW3", "W8"]},
                                         {"name": "Tunbridge Wells", "codes": ["TN1", "TN2"]},
                                         {"name": "Newcastle-under-Lyme", "codes": ["ST5"]}]},
    ]
    (tmp_path / "page.js").write_text(script, encoding="utf-8")
    (tmp_path / "harness.js").write_text(E5_AREAS_HARNESS, encoding="utf-8")
    typed = ["NG21", "gedling", "LS1", "ls6 3aa", "minster", "zz9", "", "LS", "wells", "west", "under-ly", "-"]
    out = subprocess.run([node, str(tmp_path / "harness.js"), str(tmp_path / "page.js"), json.dumps(spec),
                          json.dumps(typed)], capture_output=True, text=True, encoding="utf-8", timeout=60,
                         check=True)
    result = json.loads(out.stdout)
    assert result["shown_box"] is True
    ng21, gedling, ls1, ls6, minster, none, cleared, ls, wells, west, under, dash = result["states"]
    assert ng21 == {"status": "1 council matches.", "councils": ["Gedling"], "regions": ["region-1"],
                    "jumps": ["#region-1"]}
    assert gedling["councils"] == ["Gedling"]
    assert ls1["councils"] == ["Leeds"] and ls1["regions"] == ["region-2"]     # LS1 and LS10 start with it
    assert ls6["councils"] == ["Leeds"]                                           # a full postcode, by its district
    assert none["councils"] == [] and none["regions"] == [] and none["jumps"] == []
    assert none["status"] == "No council or district matches “zz9”. The box above takes any full postcode."
    assert cleared["status"] == "" and len(cleared["councils"]) == 7 and len(cleared["jumps"]) == 3
    # A council name matches from the start of a word, never mid-word (fix
    # pass, 18 Sep 2026): "LS" found Tunbridge Wells and Kensington and
    # Chelsea as well as Leeds, and a single letter most of the index.
    assert ls["councils"] == ["Leeds"] and ls["status"] == "1 council matches."
    assert minster["councils"] == [] and minster["status"].startswith("No council or district matches “minster”")
    assert wells["councils"] == ["Tunbridge Wells"]
    assert west["councils"] == ["Westminster"]
    assert under["councils"] == ["Newcastle-under-Lyme"]                         # a hyphen counts as a space
    assert dash["councils"] == []                                                 # punctuation alone matches nothing


def test_e5_ng21_is_filed_with_east_midlands_and_no_region_is_called_england(client):
    import importlib.util
    ng21 = [o for o in app_main.ALL_OUTCODES if o["outcode"] == "NG21"]
    assert ng21 == [{"outcode": "NG21", "lat": 53.1445, "lon": -1.102, "district": "Gedling",
                     "region": "East Midlands", "country": "England"}]
    assert app_main.OUTCODE_REGION["NG21"] == "East Midlands"
    assert not [o["outcode"] for o in app_main.ALL_OUTCODES if o["region"] == "England"]
    regions = dict(app_main._area_index())
    assert "England" not in regions and len(regions) == 12
    assert "NG21" in dict(regions["East Midlands"])["Gedling"]
    body = client.get("/areas").text
    assert "{:,} postcode districts across 12 regions and nations".format(len(app_main.ALL_OUTCODES)) in body
    assert ">England</a>" not in body.split('class="areas-jump"', 1)[1].split("</nav>", 1)[0]

    # The build script settles the same case, so a re-run cannot bring it back.
    spec = importlib.util.spec_from_file_location("build_outcode_list", ROOT / "scripts" / "build_outcode_list.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    rows = [{"outcode": "NG4", "district": "Gedling", "region": "East Midlands", "country": "England"},
            {"outcode": "NG21", "district": "Gedling", "region": "England", "country": "England"},
            {"outcode": "ZZ1", "district": "Nowhere", "region": "England", "country": "England"},
            {"outcode": "CF10", "district": "Cardiff", "region": "Wales", "country": "Wales"}]
    settled = {o["outcode"]: o["region"] for o in build.settle_english_regions(rows)}
    assert settled == {"NG4": "East Midlands", "NG21": "East Midlands", "ZZ1": "England", "CF10": "Wales"}


def test_e5_the_menu_says_chrome_extension(client):
    body = client.get("/premium").text
    nav = body.split('<nav class="site-nav" id="site-nav-menu">', 1)[1].split("</nav>", 1)[0]
    assert '<a href="/browser-extension">Chrome extension</a>' in nav
    assert ">Extension</a>" not in nav


def test_e5_log_in_on_the_way_to_my_properties_says_so_and_sign_up_carries_next(client):
    r = client.get("/watchlist", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/watchlist"
    body = client.get(r.headers["location"]).text
    assert "<h1>Your saved homes</h1>" in body and "<h1>Log in</h1>" not in body
    assert '<p class="dek">Log in to see them and what has changed since you last looked.</p>' in body
    assert "<title>Your saved homes | UKPropertyInsight</title>" in body
    assert '<a href="/signup?next=/watchlist">Sign up</a>' in body
    assert '<input type="hidden" name="next" value="/watchlist">' in body

    # A wrong password keeps the heading.
    again = client.post("/login", data={"email": "e5-nobody@customer.test", "password": "wrong-password",
                                        "next": "/watchlist"}).text
    assert "Incorrect email or password." in again and "<h1>Your saved homes</h1>" in again

    # Anywhere else it is the plain log-in, and sign-up still carries next.
    plain = client.get("/login").text
    assert "<h1>Log in</h1>" in plain and "Your saved homes" not in plain
    assert '<a href="/signup">Sign up</a>' in plain
    to_report = client.get("/login?next=/property%3Fpostcode%3DKT3%204HX%26house_number%3D36").text
    assert "<h1>Log in</h1>" in to_report
    assert '<a href="/signup?next=/property%3Fpostcode%3DKT3%204HX%26house_number%3D36">Sign up</a>' in to_report
    assert "<h1>Log in</h1>" in client.get("/login?next=/watchlist/compare").text
    # Carried safely: whatever next holds is quoted, and sign-up checks it.
    hostile = client.get('/login?next=/x"><script>alert(1)</script>').text
    assert "<script>alert(1)" not in hostile
    assert '<a href="/signup?next=/x%22%3E%3Cscript%3Ealert%281%29%3C/script%3E">Sign up</a>' in hostile


def test_e5_the_404_page_asks_for_a_postcode_once_and_links_running_costs(client):
    for path in ("/no-such-page-e5", "/tools/no-such-tool-e5"):
        r = client.get(path)
        assert r.status_code == 404, path
        body = r.text
        assert 'id="header-search"' not in body and 'id="header-postcode"' not in body, path
        assert len(re.findall(r'<input[^>]*name="postcode"', body)) == 1 and 'id="nf-postcode"' in body, path
        assert '<a href="/running-costs">Running costs by postcode</a>' in body, path
        assert '<a href="/running-costs/council-tax">Council tax by council</a>' in body, path
    # Every other page keeps the header's box.
    assert 'id="header-search"' in client.get("/premium").text


E5_CHANGE_PROMISE = re.compile(
    r"\b(?:e-?mails?|emailed|tells?|told|alerts?|alerted|hear from us|notif(?:y|ied|ications?))\b[^.]{0,80}?"
    r"\b(?:if|when|whenever|as soon as)\s+(?:anything|something)\b", re.I)


def test_e5_no_template_promises_an_email_when_anything_or_something_changes(client, monkeypatch):
    templates = ROOT / "app" / "templates"
    for path in sorted(templates.rglob("*.html")):
        # Template comments are for the next developer, and the ones that
        # record this change quote the old promise on purpose.
        text = _flat(re.sub(r"<[^>]+>", " ", _without_template_comments(path.read_text(encoding="utf-8"))))
        found = E5_CHANGE_PROMISE.search(text)
        assert not found, f"{path.name} says {found.group(0)!r}"
    # The rule catches the old wordings.
    for old in ("an email when something changes on a saved property",
                "get an email when something changes", "We email you if anything on this list changes",
                "emails an account only when something on one of its homes changed",
                "so we can tell you when something changes on a property or school you follow"):
        assert E5_CHANGE_PROMISE.search(old), old

    # Sign-up names the triggers, from the one list.
    signup = _flat(client.get("/signup").text)
    assert ("<strong>My properties</strong>: notes, side-by-side comparison, and an email when one of these "
            f"happens to a saved home: {app_main.ALERT_TRIGGERS_LIST}, never on a schedule</li>") in signup
    assert app_main.templates.env.globals["alert_triggers_list"] is app_main.ALERT_TRIGGERS_LIST

    # So does My properties' list of reports opened but not saved.
    _c5_quiet(monkeypatch)
    monkeypatch.setattr(email_service, "is_configured", lambda: True)
    _c5_account(client, "e5-opened@customer.test", saved=[], opened=("M14 5TG", "505"))
    body = client.get("/watchlist").text
    line = _flat(re.search(r'<h2 class="landing-heading">Reports you\'ve opened</h2>\s*<p class="landing-subheading">'
                           r"(.*?)</p>", body, re.S).group(1))
    assert line == ("Full reports you've already used. Save one to keep notes on it, and we email you when one of "
                    f"these happens to it: {app_main.ALERT_TRIGGERS_LIST}. Never on a schedule.")
    # With no way to send one, no email is promised.
    monkeypatch.setattr(email_service, "is_configured", lambda: False)
    body = client.get("/watchlist").text
    line = _flat(re.search(r'<h2 class="landing-heading">Reports you\'ve opened</h2>\s*<p class="landing-subheading">'
                           r"(.*?)</p>", body, re.S).group(1))
    assert line == "Full reports you've already used. Save one to keep notes on it."


# ---- E6. Premium on a phone, and the list view that forgets returns --------
# /premium on a 375px phone was about seventeen screens, most of them the 44
# check cards one per row under 9.5px titles. On a phone the checks now come
# under the report's six group names, each a tap to open and each saying how
# many of its checks come with Premium, counted from the plan lists; a wider
# screen keeps the two columns. The titles are 12px on every width. On the
# report, "Show every card at once" is remembered on the device and hides
# the tiles, so a return visit's jump to the group that changed did nothing:
# in that view the group's lead card is now scrolled to and wears the ring
# an open tile wears, and the view's card labels are 12px on a desktop too.

E6_GROUP_NAMES = ["Value & Market", "Property & Condition", "Risk & Safety", "Planning & Heritage",
                  "Location & Connectivity", "Area & Community"]


def _e6_text(fragment):
    return html.unescape(_flat(re.sub(r"<[^>]+>", "", fragment)))


def _e6_phone_groups(body):
    """[(summary words, [(title, plan line)], open?)] from /premium's phone list."""
    block = body.split('<div class="premium-groups">', 1)[1].split('<div class="premium-tiers">', 1)[0]
    groups = []
    for attrs, inner in re.findall(r'<details class="premium-group"([^>]*)>(.*?)</details>', block, re.S):
        summary = _e6_text(re.search(r"<summary>(.*?)</summary>", inner, re.S).group(1))
        rows = [(_e6_text(re.search(r'<p class="lx-check-title">(.*?)</p>', li, re.S).group(1)),
                 _e6_text(re.search(r'<p class="premium-group-source">(.*?)</p>', li, re.S).group(1)))
                for li in re.findall(r'<li class="premium-group-check">(.*?)</li>', inner, re.S)]
        groups.append((summary, rows, "open" in attrs))
    return groups


def _e6_expected(free_checks, premium_checks):
    """What each summary should say, counted here from the lists."""
    free = {c[1]: c for c in free_checks}
    locked = {c[1]: c for c in premium_checks}
    expected = []
    for name, titles in app_main.REPORT_GROUPS:
        held = [t for t in titles if t in free or t in locked]
        paid = sum(1 for t in held if t in locked)
        expected.append(f"{name}: {len(held)} checks, {paid} with Premium" if paid else f"{name}: {len(held)} checks, all free")
    return expected


def test_e6_premium_lists_the_checks_under_the_reports_six_groups_on_a_phone(client, monkeypatch):
    _billing(monkeypatch)
    body = _fresh_premium(client)
    groups = _e6_phone_groups(body)
    assert [summary.split(":", 1)[0] for summary, _, _ in groups] == E6_GROUP_NAMES
    assert [summary for summary, _, _ in groups] == _e6_expected(app_main.FREE_CHECKS, app_main.PREMIUM_CHECKS)
    assert groups[2][0] == "Risk & Safety: 10 checks, 5 with Premium"
    # Every group is closed until tapped.
    assert not any(is_open for _, _, is_open in groups)

    # All 44, each once, each saying which plan it comes with, and a
    # Premium check where its source reaches, as the desktop columns do.
    free = {c[1]: c for c in app_main.FREE_CHECKS}
    locked = {c[1]: c for c in app_main.PREMIUM_CHECKS}
    rows = [row for _, group_rows, _ in groups for row in group_rows]
    assert len(rows) == app_main.CHECK_COUNT and {t for t, _ in rows} == set(free) | set(locked)
    for title, line in rows:
        if title in locked:
            assert line == f"Premium · {locked[title][3]} · {app_main.premium_reach_label(title)}", title
        else:
            assert line == f"Free · {free[title][3]}", title
    phone = _e6_text(body.split('<div class="premium-groups">', 1)[1].split("<details", 1)[0])
    assert phone == (f"{len(app_main.FREE_CHECKS)} free on every report, with no account. "
                     f"{len(app_main.PREMIUM_CHECKS)} more with Premium, free on your first property with no card.")

    # The desktop columns are all still there, "Before you pay" still sits
    # above the list (item C2), and the homepage's anchor holds both.
    assert body.count('class="lx-check"') == app_main.CHECK_COUNT
    assert body.count('<div class="premium-tiers">') == 1 and body.count('id="all-checks"') == 1
    at = [body.index(s) for s in ("Before you pay", 'id="all-checks"', '<div class="premium-groups">',
                                  '<div class="premium-tiers">')]
    assert at == sorted(at)


def test_e6_the_group_counts_follow_the_lists(client, monkeypatch):
    _billing(monkeypatch)
    # A Premium check that became free, and a free check that went.
    mining = next(c for c in app_main.PREMIUM_CHECKS if c[1] == "Mining Risk")
    free = tuple(c for c in app_main.FREE_CHECKS if c[1] != "Radon Gas") + (mining,)
    premium = tuple(c for c in app_main.PREMIUM_CHECKS if c[1] != "Mining Risk")
    monkeypatch.setattr(app_main, "FREE_CHECKS", free)
    monkeypatch.setattr(app_main, "PREMIUM_CHECKS", premium)
    groups = _e6_phone_groups(_fresh_premium(client))
    assert [summary for summary, _, _ in groups] == _e6_expected(free, premium)
    assert groups[2][0] == "Risk & Safety: 9 checks, 4 with Premium"
    assert ("Mining Risk", "Free · Mining Remediation Authority") in groups[2][1]
    # A group with nothing behind the wall says so in words.
    assert app_main.checks_by_group()[0]["count"] == "7 checks, 2 with Premium"
    monkeypatch.setattr(app_main, "PREMIUM_CHECKS", tuple(c for c in premium if c[1] not in ("Valuation Estimate", "Price Trend")))
    monkeypatch.setattr(app_main, "FREE_CHECKS", free + tuple(c for c in premium if c[1] in ("Valuation Estimate", "Price Trend")))
    assert app_main.checks_by_group()[0]["count"] == "7 checks, all free"


def test_e6_the_groups_are_the_reports_own(client, fake_report):
    from tests.test_property_page import NOT_AN_OFFICIAL_SOURCE_CHECK
    fake_report()
    body = client.get("/property?postcode=M14+5TG").text
    report = body.split('id="report-categories"', 1)[1].split('id="cat-complete"', 1)[0]
    parts = re.split(r'<h3 class="dashboard-category-heading">(.*?)</h3>', report)[1:]
    rendered = {}
    for name, part in zip(parts[0::2], parts[1::2]):
        titles = []
        for m in re.finditer(r'<(?:button|a|div)[^>]*class="dashboard-card [^"]*"[^>]*>', part):
            titles.append(html.unescape(re.search(r'dashboard-card-title">(.*?)</span>', part[m.end():m.end() + 3000]).group(1)))
        rendered[html.unescape(name)] = [t for t in titles if t not in NOT_AN_OFFICIAL_SOURCE_CHECK]
    assert list(rendered) == E6_GROUP_NAMES
    # Each group holds the cards the report shows under its heading, in the
    # report's order.
    assert {name: list(titles) for name, titles in app_main.REPORT_GROUPS} == rendered
    # And every check in the two lists is in exactly one group.
    grouped = [t for _, titles in app_main.REPORT_GROUPS for t in titles]
    assert sorted(grouped) == sorted(c[1] for c in app_main.FREE_CHECKS + app_main.PREMIUM_CHECKS)


def test_e6_the_phone_list_replaces_the_columns_only_on_a_phone_and_titles_are_12px():
    css = re.sub(r"/\*.*?\*/", "", STYLE_CSS, flags=re.S).replace("\r\n", "\n")
    # Hidden unless the screen is a phone's; there, the columns go instead.
    assert re.search(r"^\.premium-groups \{ display: none; \}$", css, re.M)
    phone = re.search(r"@media \(max-width: 560px\) \{\s*\.premium-tiers \{ display: none; \}\s*"
                      r"\.premium-groups \{([^}]*)\}\s*\}", css)
    assert phone and "display: flex;" in phone.group(1)
    # The same width the columns' grids drop to one card per row.
    assert "@media (max-width: 560px) {\n    .lx-check-grid { grid-template-columns: 1fr; }" in css

    # 12px on every width: the one rule that sizes a check title uses the
    # 12px token, and nothing sizes it smaller.
    assert "--text-xs: 0.75rem;" in STYLE_CSS
    sized = [d for s, d in _css_rules(".lx-check-title") if "font-size" in d]
    assert sized and all("font-size: var(--text-xs);" in d for d in sized)
    assert "9.5px" not in "".join(d for _, d in _css_rules(".lx-check-title"))
    # The chevron turns without motion for a reader who asks for none.
    assert re.search(r"@media \(prefers-reduced-motion: reduce\) \{\s*\.premium-group > summary::after "
                     r"\{ transition: none; \}", css)


def _e6_tile_script(client, fake_report):
    fake_report()
    body = client.get("/property?postcode=M14+5TG").text
    scripts = [s for s in re.findall(r"<script>(.*?)</script>", body, re.S) if "uki-report-view" in s]
    assert len(scripts) == 1
    return scripts[0]


def _e6_function(script, name):
    start = script.index(f"function {name}(")
    depth = 0
    for i in range(script.index("{", start), len(script)):
        depth += {"{": 1, "}": -1}.get(script[i], 0)
        if depth == 0:
            return script[start:i + 1]
    raise AssertionError(f"{name} does not close")


E6_JUMP_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[2], 'utf8');
const spec = JSON.parse(process.argv[3]);
var scrolls = [], opened = [];
var window = { pageYOffset: 1000, location: { hash: spec.hash }, scrollTo: function (o) { scrolls.push(o); } };
var still = spec.still, listMode = spec.list;
var cats = spec.groups.map(function (slug, n) {
    var marks = {};
    var lead = { classList: { add: function (c) { marks[c] = true; } } };
    return { slug: slug, marks: marks,
             grid: { querySelector: function (sel) { return sel === '.dashboard-card' ? lead : null; } },
             panel: { querySelector: function (sel) {
                 return sel === '.cat-panel-heading' ? { getBoundingClientRect: function () { return { top: 400 + n * 100 }; } } : null;
             } } };
});
var root = { getAttribute: function (name) { return name === 'data-open-group' ? spec.open_group : null; } };
function open(c) { opened.push(c.slug); }
eval(src);
console.log(JSON.stringify({ scrolls: scrolls, opened: opened,
                             marked: cats.filter(function (c) { return c.marks['cat-card-jumped']; }).map(function (c) { return c.slug; }) }));
"""


def test_e6_the_every_card_view_jumps_to_what_changed(client, fake_report, tmp_path):
    import shutil
    import subprocess
    import pytest
    script = _e6_tile_script(client, fake_report)
    # The code path: in the every-card view the changed group is jumped to,
    # not left alone.
    assert "if (!listMode && wanted === c.slug) open(c);" not in script
    dispatch = re.search(r"var wanted = [^\n]*\n\s*cats\.forEach\(function \(c\) \{.*?\n\s*\}\);", script, re.S)
    assert dispatch and re.search(r"if \(listMode\) jumpTo\(c\);\s*else open\(c\);", dispatch.group(0))
    jump = _e6_function(script, "jumpTo")
    assert "card.classList.add('cat-card-jumped');" in jump
    assert "window.scrollTo(" in jump and "behavior: still ? 'auto' : 'smooth'" in jump
    # Leaving the view takes the ring off, and the remembered view stays.
    set_list = _e6_function(script, "setList")
    assert "el.classList.remove('cat-card-jumped');" in set_list
    assert "localStorage.setItem('uki-report-view', on ? 'list' : 'groups');" in set_list
    assert "setList(saved === 'list', false);" in script

    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed; the jump is pinned by the source checks above")
    (tmp_path / "page.js").write_text(jump + "\n" + dispatch.group(0), encoding="utf-8")
    (tmp_path / "harness.js").write_text(E6_JUMP_HARNESS, encoding="utf-8")
    groups = ["cat-value-market", "cat-property-condition", "cat-risk-safety"]

    def run(**spec):
        spec = {"groups": groups, "hash": "", "open_group": "", "still": False, "list": True, **spec}
        out = subprocess.run([node, str(tmp_path / "harness.js"), str(tmp_path / "page.js"), json.dumps(spec)],
                             capture_output=True, text=True, timeout=60, check=True)
        return json.loads(out.stdout)

    # A return visit in the every-card view: the changed group's lead card
    # is marked and scrolled to, under its heading, and no tile opens.
    listed = run(open_group="cat-risk-safety")
    assert listed == {"scrolls": [{"top": 400 + 2 * 100 + 1000 - 16, "behavior": "smooth"}], "opened": [],
                      "marked": ["cat-risk-safety"]}
    assert run(open_group="cat-risk-safety", still=True)["scrolls"][0]["behavior"] == "auto"
    # A link to a group does the same, ahead of the server's choice.
    assert run(hash="#cat-value-market", open_group="cat-risk-safety")["marked"] == ["cat-value-market"]
    # In groups the tile opens as before, and nothing is marked.
    assert run(open_group="cat-risk-safety", list=False) == {"scrolls": [], "opened": ["cat-risk-safety"], "marked": []}
    # Nothing changed, nothing moves.
    assert run() == {"scrolls": [], "opened": [], "marked": []}


def test_e6_the_every_card_view_marks_like_a_tile_and_labels_at_12px():
    rings = _css_rules("cat-card-jumped")
    assert len(rings) == 1
    selector, jumped = rings[0]
    # Only in the every-card view, where there is no tile to open.
    assert [s.strip() for s in selector.split(",")] == [".report-categories.is-list .dashboard-card.cat-card-jumped",
                                                        ".report-categories.is-list .dashboard-card.cat-card-jumped:hover"]
    jumped = _flat(jumped)
    tile = _flat(next(d for s, d in _css_rules('.cat-tile[aria-expanded="true"]') if "background: var(--accent-soft)" in d))
    for declaration in ("background: var(--accent-soft);", "border-color: var(--accent);", "box-shadow: 0 0 0 1px var(--accent);"):
        assert declaration in jumped and declaration in tile, declaration
    # Card labels in this view are 12px at every width; elsewhere they
    # keep their size.
    labels = [d for s, d in _css_rules(".dashboard-card-title") if s == ".report-categories.is-list .dashboard-card-title"]
    assert labels == [" font-size: var(--text-xs); "]
    assert "--text-xs: 0.75rem;" in STYLE_CSS


# ---- E7. A postcode-only report stops describing one home ----------------
# What batch D's review found D1 had left: the score's energy positive was
# the newest certificate's band ("Energy-efficient property (EPC C)" on
# KT3 4HX, for 57 Malden Hill Gardens alone), the locked valuation narrowed
# the sales nearby to that home's floor area and called it "this
# property's", and the PDF said "Last sale" and set the newest certificate
# out as the home being bought. The district comparison's 229 against 230
# was put right in the batch D fix pass; the page itself is pinned here.

E7_CERTS = [
    {"address": "57, Malden Hill Gardens, New Malden", "rating": "C", "date": "2025-11-04", "certificate_number": "E7-57"},
    {"address": "59 Malden Hill Gardens, New Malden", "rating": "E", "date": "2024-03-12", "certificate_number": "E7-59"},
    {"address": "61 Malden Hill Gardens, New Malden", "rating": "E", "date": "2023-03-12", "certificate_number": "E7-61"},
    {"address": "63 Malden Hill Gardens, New Malden", "rating": "D", "date": "2022-03-12", "certificate_number": "E7-63"},
    # 57's older certificate: each home counts once, at its newest.
    {"address": "57 Malden Hill Gardens, New Malden", "rating": "E", "date": "2012-01-01", "certificate_number": "E7-57-old"},
]


def _e7_rated(*bands):
    """E7_CERTS' four homes, newest first, rated as given."""
    homes = [c for c in E7_CERTS if c["certificate_number"] != "E7-57-old"]
    return [dict(c, rating=b) for c, b in zip(homes, bands)]


def test_e7_the_newest_certificate_at_c_does_not_score_a_postcode_of_es():
    from app.services import overview_score
    postcode = overview_score.compute({"certificates": E7_CERTS}, postcode_only=True)
    assert not any("nerg" in p for p in postcode["positives"]), postcode["positives"]
    assert postcode["verdict"] == "No major signals either way from the data available for this property."
    # A house number reads that home's own certificate, as it always has.
    home = overview_score.compute({"certificates": E7_CERTS})
    assert home["positives"] == ["Energy-efficient property (EPC C)"]
    assert home["score"] == postcode["score"] + overview_score._POSITIVE_BONUS


def test_e7_most_homes_at_c_or_better_is_the_postcodes_own_positive_with_its_count():
    from app.services import overview_score
    out = overview_score.compute({"certificates": _e7_rated("C", "B", "B", "D")}, postcode_only=True)
    assert out["positives"] == ["Most homes here with an energy certificate rated C or better (3 of 4)"]
    assert out["verdict"] == "Good overall: Most homes here with an energy certificate rated C or better (3 of 4)."
    # It opens the EPC card, as the one-home positive does.
    assert out["reasons"]["positives"] == [{"text": out["positives"][0], "modal": "modal-epc"}]
    # Half is not most, and two homes are too few to speak for a postcode.
    for certs in (_e7_rated("C", "C", "E", "E"), _e7_rated("C", "B")):
        assert overview_score.compute({"certificates": certs}, postcode_only=True)["positives"] == []
    # A certificate without a band is not counted as a home either way.
    unbanded = _e7_rated("C", "B", "?", "?")
    assert overview_score.compute({"certificates": unbanded}, postcode_only=True)["positives"] == []


def test_e7_the_gather_scores_a_postcode_by_its_homes_and_a_house_by_its_own(monkeypatch):
    """The real gather, every member failed but the EPC flow, which
    answers with E7_CERTS, so nothing reaches the network."""
    async def _members(name, coro):
        close = getattr(coro, "close", None)
        if close:
            close()  # never awaited, so never sent
        if name == "-epc-flow":
            return [dict(c) for c in E7_CERTS], dict(D1_DETAIL_57, current_band="C"), None, None
        return RuntimeError(f"{name} down")

    async def _uncached(cache_key, ttl_s, factory):
        return await factory()

    monkeypatch.setattr(app_main, "_timed", _members)
    monkeypatch.setattr(app_main, "_bounded", lambda coro, seconds: coro)
    monkeypatch.setattr(app_main, "_deduped", _uncached)
    location = fake_location(postcode="KT3 9ZE", outcode="KT3")
    positives = {}
    try:
        for house_number in ("", "57"):
            context = asyncio.run(app_main._full_property_gather(location, house_number, premium_unlocked=False))
            positives[house_number] = context["overview"]["positives"]
    finally:
        for house_number in ("", "57"):
            app_main._gather_progress.pop(("KT3 9ZE", house_number), None)
    assert positives[""] == []
    assert positives["57"] == ["Energy-efficient property (EPC C)"]


def test_e7_a_postcode_valuation_is_for_the_homes_around_it_not_one_floor_area(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    recent = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
    # Two sales the size of 57's 122 m², four of other sizes.
    sizes = (120, 124, 60, 75, 200, 90)
    comparables = [{"address": f"{n} Near Road", "date": recent, "amount": str(300000 + n * 1000), "floor_area": size}
                   for n, size in enumerate(sizes, start=1)]

    async def _comparables(_lat, _lon):
        return [dict(c) for c in comparables]

    fake_report(location=fake_location(postcode="KT3 4HX", outcode="KT3"), gather=_d1_gather())
    monkeypatch.setattr(app_main, "_comparables_fetch", _comparables)

    # Signed out, the estimate is not in the reply at all (batch B's rule).
    out = client.get("/api/property/valuation?postcode=KT3+4HX").json()
    assert "any size" not in out["card"] + out["body"] and "Middle (median)" not in out["body"]
    assert B3_WAY_IN in _flat(out["body"])

    _b3_subscriber(client, "e7-valuation@customer.test")
    out = client.get("/api/property/valuation?postcode=KT3+4HX").json()
    body = _flat(out["body"])
    assert "this property's" not in body and "floor area within" not in body and "122 m²" not in body
    assert "No home is chosen, so this is for the homes around this postcode, not for one of them." in body
    assert "Based on 6 sales of any size within about 0.6 miles, sold in the last 1 year," in body
    assert "Middle (median)" in body and "Estimate (median)" not in body
    assert "for homes of any size nearby" in _flat(out["card"])

    # With a house number the estimate is that home's size, as before.
    out = client.get("/api/property/valuation?postcode=KT3+4HX&house_number=57").json()
    body = _flat(out["body"])
    assert "Based on 2 sales within about 0.6 miles" in body
    assert "of a similar size (floor area within 5% of this property's)" in body
    assert "any size" not in body + out["card"]


def test_e7_the_estimate_counts_every_size_only_without_a_house_number():
    recent = (datetime.date.today() - datetime.timedelta(days=40)).isoformat()
    comparables = [{"address": f"{n} Near Road", "date": recent, "amount": "500000", "floor_area": size}
                   for n, size in enumerate((122, 60, 200, None), start=1)]
    postcode = {"transactions": []}
    app_main._apply_valuation(postcode, comparables, 122, 0, "")
    assert postcode["valuation"]["any_size"] is True and postcode["valuation"]["sample_size"] == 4
    assert postcode["valuation"]["floor_area_variance_pct"] is None
    home = {"transactions": []}
    app_main._apply_valuation(home, comparables, 122, 0, "57")
    assert home["valuation"]["any_size"] is False and home["valuation"]["sample_size"] == 1
    # No floor area for a chosen home is still no estimate, as before.
    unknown = {"transactions": []}
    app_main._apply_valuation(unknown, comparables, None, 0, "57")
    assert unknown["valuation"] is None
    # Nothing sold nearby in the year: the pop-up says so for the postcode.
    empty = {"transactions": []}
    app_main._apply_valuation(empty, [], 122, 0, "")
    page = _flat(str(app_main.templates.get_template("_valuation.html").module.valuation_body(
        empty["valuation"], empty["price_per_sqm"], False, True, False, True, postcode_only=True)))
    assert "No recorded sale within about 0.6 miles of this postcode in the last year to reference." in page
    assert "similar-sized property" not in page and "this property" not in page


def test_e7_a_postcode_pdf_gives_the_postcodes_figures_and_names_the_certificates_home():
    from tests.test_pdf_report import _running_costs
    certs = [{"address": "57, Malden Hill Gardens, New Malden", "rating": "E", "date": "2026-01-28"},
             {"address": "59 Malden Hill Gardens, New Malden", "rating": "C", "date": "2019-03-12"}]
    report = dict(_full_pdf_report(), certificates=certs, transactions=[dict(D1_SALE_55)],
                  valuation={"estimate": 612000.0, "low": 450000.0, "high": 790000.0, "sample_size": 41,
                             "years_window": 1, "floor_area_variance_pct": None, "any_size": True})
    sales = dict(_running_costs()["sales"], recent_from_year="2016", recent_to_year="2025")
    rc = dict(_running_costs(), house_number="", home=None, sales=sales,
              stamp_duty={"price": 412000.0, "basis": "the middle of the postcode's recent sales",
                          "standard": 10600, "first_time": 0, "additional": 31200})
    ctx = app_main._pdf_context(report, rc, report["location"], "")
    text = html.unescape(_flat(app_main.templates.get_template("pdf_report_full.html").render(ctx)))
    assert "Last sale" not in text and "Last sold" not in text

    # The cover: the postcode's middle price and count, its energy middle.
    fig = '<span class="fig-v">{}</span><br/><span class="fig-l">{}</span>'
    assert fig.format("£412,000", "Middle of the last 10 of 29 recorded sales here, 2016 to 2025") in text
    assert fig.format("£974 a year", "Energy, the middle of 8 homes here") in text
    assert "Estimated value" not in text
    # The valuation, for the homes around the postcode, and the stamp duty on it.
    assert "No home was chosen, so this is what homes of every size around this postcode sold for" in text
    assert "matched on floor area" not in text and "what the house is likely worth" not in text
    assert ("Stamp Duty Land Tax on £612,000, the middle of the last year's sales of any size around this "
            "postcode.") in text
    # The tenure split, and the certificate named as one home's.
    assert "13 freehold, 16 leasehold of 29 recorded sales here" in text
    assert ("Newest certificate here: 57 Malden Hill Gardens. One home's certificate from the EPC Register, "
            "not a description of every home at KT3 4HX.") in text
    assert "about this home itself" not in text

    rows = {r["check"]: r["result"] for r in ctx["checklist"]}
    assert rows["Sold prices at this postcode"] == "£412,000, middle of the last 10 of 29 recorded sales here, 2016 to 2025"
    assert rows["Valuation estimate"] == ("£612,000, the middle of 41 sales of any size nearby in the last year, "
                                          "range £450,000 to £790,000; no home chosen, so not one home's value")
    assert rows["Energy: heating, hot water, lighting"] == (
        "£974 a year, the middle of 8 homes' EPC estimates at this postcode, from £462 to £2,192")
    assert rows["Tenure at this postcode"] == "13 freehold and 16 leasehold of 29 recorded sales"
    assert rows["Energy performance certificate"].startswith("Newest certificate here, 57 Malden Hill Gardens: Band E, score 51")
    assert rows["Size and layout"] == "57 Malden Hill Gardens: Semi-detached house, 130 sq m, 8 habitable rooms"
    assert rows["Lettable (MEES minimum E)"] == "57 Malden Hill Gardens: Yes"
    assert "(the middle of the last year's sales of any size around this postcode)" in rows["Stamp duty, one-off"]


def test_e7_a_numbered_pdf_reads_as_it_did():
    from tests.test_pdf_report import _running_costs
    ctx = app_main._pdf_context(_full_pdf_report(), _running_costs(), _full_pdf_report()["location"], "36")
    rows = {r["check"]: r["result"] for r in ctx["checklist"]}
    assert ctx["postcode_only"] is False and ctx["postcode_sales_label"] == ""
    assert rows["Sold prices at this postcode"] == "Last sale £823,500 in 2025; recent median £412,000 across 10 sales"
    assert rows["Energy performance certificate"].startswith("Band E, score 51")
    assert rows["Size and layout"] == "Semi-detached house, 130 sq m, 8 habitable rooms"
    assert "(the valuation estimate)" in rows["Stamp duty, one-off"]
    text = html.unescape(_flat(app_main.templates.get_template("pdf_report_full.html").render(ctx)))
    assert "Stamp Duty Land Tax on the valuation estimate of £928,000." in text
    assert "What the certificate and the register say about this home itself." in text


def test_e7_a_district_comparison_of_229_against_230_crimes_names_no_winner(client, monkeypatch):
    from tests.test_brainstorm_18sep import _versus
    left, right = sorted(["LS6", app_main._neighbour_outcodes("LS6")[0]])
    side = {"local_median": 310000, "local_sales_count": 104, "crime_total": 229, "imd_decile": 4}
    body = html.unescape(_flat(_versus(client, monkeypatch, left, right, {left: side, right: dict(side, crime_total=230)})))
    assert "Traceback" not in body
    assert "fewer crimes" not in body
    assert f"About the same. {left} recorded 229 crimes in the same period and {right} 230 (Police.uk)." in body
    assert "229 recorded" in body and "230 recorded" in body


# ---- Batch E fix pass: what the review of E1 to E7 found left over -------
# (1) E5(5): "Chrome extension" is about 58px wider than "Extension", and
# between 840 and 1210px the header gained a row on every page (84 to
# 132px on the homepage at 1180px, iPad landscape). The menu's links and
# gap are tighter where it wraps, and a sweep of 641 to 1340px in 3px steps
# found the header nowhere taller than with the short label. (2) E5(7):
# the verification email and the alert email's footer still promised an
# email "when something changes". (3) E5(4): the /areas filter matched
# council names mid-word, so "LS" showed Tunbridge Wells (pinned in the E5
# filter test above). (4) E1: the moved homepage sections typed 44 four
# times and painted "2291 recorded". (5) E3: the map's tick box named
# "the nearest schools that have one, listed below" over a table that also
# lists the schools with no limit (pinned in the E3 tests above).


def _media_rules(query):
    """(selector, declarations) of every rule inside `@media <query> {`."""
    css = re.sub(r"/\*.*?\*/", "", STYLE_CSS, flags=re.S)
    rules = []
    for m in re.finditer(r"@media " + re.escape(query) + r"\s*\{", css):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        rules += [(sel.strip(), decls) for sel, decls in re.findall(r"([^{}]*)\{([^{}]*)\}", css[m.end():i - 1])]
    return rules


def test_fix_e_the_menu_wins_back_the_width_chrome_extension_took_where_it_wraps(client):
    nav = client.get("/premium").text.split('<nav class="site-nav" id="site-nav-menu">', 1)[1].split("</nav>", 1)[0]
    assert '<a href="/browser-extension">Chrome extension</a>' in nav
    wrapping = [(s, d) for s, d in _media_rules("(min-width: 641px) and (max-width: 1320px)")
                if "gap" in d or "padding" in d]
    assert wrapping == [(".site-nav", " gap: 0.1rem; "),
                        (".site-nav a, .site-nav .nav-form button", " padding: 0.45rem 0.45rem; ")], wrapping
    # After the rules it tightens, so it wins where both apply.
    assert STYLE_CSS.index("padding: 0.45rem 0.45rem;") > STYLE_CSS.index(".site-nav .nav-form button {")
    # Wide screens, where the menu shares the logo's row, keep the roomier
    # spacing, and phones keep their full-width menu rows.
    assert ".site-nav {\n    display: flex;\n    align-items: center;\n    gap: 0.25rem;\n}" in STYLE_CSS
    assert STYLE_CSS.count("padding: 0.45rem 0.6rem;") == 2
    assert STYLE_CSS.count("padding: 0.6rem 0.75rem;") >= 1
    # Only spacing: no dark-mode rule and no size or font change rides along.
    assert not [s for s, _ in _media_rules("(min-width: 641px) and (max-width: 1320px)") if "theme-dark" in s]


def _fix_e_email_text(markup):
    return _flat(html.unescape(re.sub(r"<[^>]+>", " ", markup)))


def test_fix_e_the_verification_and_alert_emails_name_what_sends_an_alert():
    verify = _fix_e_email_text(app_main._verification_email_html("https://example.test/verify-email?token=t"))
    alert = _fix_e_email_text(app_main._watchlist_alert_email_html(
        [{"label": "36 Acacia Road, KT3 4HX", "postcode": "KT3 4HX", "house_number": "36",
          "changes": ["A sale was recorded on 3 Jul 2026"]}], "https://example.test/watchlist"))
    assert ("Confirm this is your address to unlock your free full report on UKPropertyInsight, and so we can "
            "email you when one of these happens to a home saved in My properties: "
            f"{app_main.ALERT_TRIGGERS_LIST}. If you ask for it on your school shortlist, we also email you when "
            "a council publishes a new admission distance for a school you saved. Never on a schedule.") in verify
    assert ("You get this email only when one of these happens to a property saved in My properties, never on "
            f"a schedule: {app_main.ALERT_TRIGGERS_LIST}. Remove a property from My properties and its "
            "alerts stop.") in alert
    for name, text in (("verification", verify), ("alert", alert)):
        found = E5_CHANGE_PROMISE.search(text)
        assert not found, f"the {name} email says {found.group(0)!r}"
        assert "crime" not in text.lower() and "on a schedule" in text.lower(), name

    # And no other string the app builds makes the old promise: every
    # literal in app/, implicit concatenations joined as Python joins them,
    # docstrings left out (they are for the next developer).
    import ast
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                      and node.body and isinstance(node.body[0], ast.Expr)
                      and isinstance(node.body[0].value, ast.Constant)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
            else:
                continue
            found = E5_CHANGE_PROMISE.search(_fix_e_email_text(text))
            assert not found, f"{path.name} line {node.lineno} says {found.group(0)!r}"


def test_fix_e_the_homepage_counts_its_checks_from_check_count_and_separates_the_crime_count(client, monkeypatch):
    template = INDEX_TEMPLATE.read_text(encoding="utf-8")
    code = _without_template_comments(template)
    for typed in ("('44', 'Checks per property')", "/ 44 checks", "All <strong>44 checks", ">44 checks &middot;"):
        assert typed not in code, typed
    # The scroll-built report names one check per mote, and its tally
    # counts them landing against CHECK_COUNT, so the two agree.
    names = re.findall(r"'([^']+)'", re.search(r"const CHECKS = \[([\s\S]*?)\n\s*\];", template).group(1))
    assert len(names) == len(set(names)) == app_main.CHECK_COUNT

    monkeypatch.setattr(app_main, "CHECK_COUNT", 45)
    body = _fresh_home(client)
    assert re.search(r'data-target="45">45</span></p>\s*<p class="lx-about-stat-l">Checks per property', body)
    assert '<span id="lx-build-tally-n">0</span> / 45 checks</p>' in body
    assert 'id="lx-build-done-head">All <strong>45 checks</strong> &middot; one search</p>' in body
    assert '<p class="lx-contact-note">45 checks &middot; ' in body
    assert "44 checks" not in body

    # The card and the build chips write a crime count as the report does,
    # "2,291 recorded", not "2291 recorded".
    assert "out.push(['Crime', d.crime_total.toLocaleString('en-GB') + ' recorded', '']);" in body
    assert "rows.push(['Crime', seed.crime_total.toLocaleString('en-GB') + ' recorded']);" in body
    assert "crime_total + ' recorded'" not in body


def test_fix_e_the_check_count_script_still_moves_every_typed_count(tmp_path):
    """The script found the old count in the trust-section stat, which now
    reads CHECK_COUNT. Run on copies, so nothing in the repository moves."""
    import importlib.util
    import shutil
    for rel in ("app/main.py", "app/templates/index.html", "app/templates/area_guide.html",
                "app/templates/running_costs.html"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, tmp_path / rel)
    spec = importlib.util.spec_from_file_location("bump_check_count_fix_e", ROOT / "scripts" / "bump_check_count.py")
    bump = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bump)
    bump.ROOT, bump.INDEX, bump.MAIN = tmp_path, tmp_path / "app/templates/index.html", tmp_path / "app/main.py"
    n = app_main.CHECK_COUNT
    assert bump.main(["bump_check_count.py", str(n + 1), "Fix E Check"]) == 0
    assert bump.main(["bump_check_count.py", str(n + 1), "Fix E Check"]) == 0          # safe to re-run
    read = lambda rel: (tmp_path / rel).read_text(encoding="utf-8")
    assert f"\nCHECK_COUNT = {n + 1}\n" in read("app/main.py")
    index = read("app/templates/index.html")
    assert f"{bump.WORDS[n + 1].capitalize()} checks on any UK address" in index
    assert index.count("'Fix E Check'") == 1
    assert f"Run the {n + 1} checks" in read("app/templates/area_guide.html")
    assert f"the rest of its {bump.WORDS[n + 1]} checks" in read("app/templates/running_costs.html")
    # The repository itself is untouched.
    assert "Fix E Check" not in INDEX_TEMPLATE.read_text(encoding="utf-8")
