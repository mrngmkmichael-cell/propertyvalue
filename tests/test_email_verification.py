"""Email confirmation, done after the report rather than before it: the
free report never waits, alerts go only to confirmed addresses once a
sending domain exists, typos are caught at sign-up, and one Gmail
mailbox is one account."""
import datetime
import re

from app import auth, db
from app import main as app_main
from app.models import User
from app.services import email as email_service


def _live(monkeypatch):
    """Pretend the sending domain is verified and record what goes out."""
    sent = []

    async def _send(to, subject, html):
        sent.append({"to": to, "subject": subject, "html": html})
        return True

    monkeypatch.setattr(email_service, "can_verify", lambda: True)
    monkeypatch.setattr(email_service, "is_configured", lambda: True)
    monkeypatch.setattr(email_service, "send_email", _send)
    return sent


def _signup(client, email, password="password123"):
    return client.post("/signup", data={"email": email, "password": password, "next": "/"}, follow_redirects=False)


def test_a_mistyped_domain_is_caught_before_an_account_exists(client, monkeypatch):
    _live(monkeypatch)
    r = _signup(client, "someone@gmail.con")
    assert r.status_code == 200
    assert "looks like a typo" in r.text and 'value="someone@gmail.com"' in r.text
    with db.get_session() as session:
        assert auth.find_user_by_email(session, "someone@gmail.con") is None


def test_one_gmail_mailbox_is_one_account(client, monkeypatch):
    _live(monkeypatch)
    assert _signup(client, "first.person@gmail.com").status_code == 303
    client.cookies.clear()
    r = _signup(client, "firstperson+ukpi@gmail.com")
    assert r.status_code == 200 and "same mailbox as first.person@gmail.com" in r.text
    # Other providers are compared exactly.
    client.cookies.clear()
    assert _signup(client, "first.person@outlook.com").status_code == 303


def test_signup_sends_a_link_that_confirms_the_address(client, monkeypatch):
    sent = _live(monkeypatch)
    assert _signup(client, "confirm-me@customer.test").status_code == 303
    assert len(sent) == 1 and sent[0]["to"] == "confirm-me@customer.test"
    link = re.search(r'href="([^"]+/verify-email\?token=[^"]+)"', sent[0]["html"]).group(1)
    with db.get_session() as session:
        user = auth.find_user_by_email(session, "confirm-me@customer.test")
        assert user.email_verified_at is None and user.verification_sent_at is not None
    # The banner asks, nothing is locked.
    assert "Confirm confirm-me@customer.test" in client.get("/watchlist").text

    r = client.get(link)
    assert r.status_code == 200 and "is confirmed" in r.text
    with db.get_session() as session:
        assert auth.find_user_by_email(session, "confirm-me@customer.test").email_verified_at is not None
    assert "Confirm confirm-me@customer.test" not in client.get("/watchlist").text

    # A bad link is a page, not a crash, and confirms nothing.
    r = client.get("/verify-email?token=not-a-real-token")
    assert r.status_code == 400 and "expired" in r.text


def test_resend_is_rate_limited(client, monkeypatch):
    sent = _live(monkeypatch)
    _signup(client, "again@customer.test")
    assert len(sent) == 1
    r = client.post("/verify-email/resend", data={"next": "/watchlist"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("verify=wait")
    assert len(sent) == 1
    with db.get_session() as session:
        u = auth.find_user_by_email(session, "again@customer.test")
        u.verification_sent_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=11)
        session.commit()
    r = client.post("/verify-email/resend", data={"next": "/watchlist"}, follow_redirects=False)
    assert r.headers["location"].endswith("verify=sent") and len(sent) == 2


def test_nothing_happens_until_a_sending_domain_exists(client, monkeypatch):
    sent = []

    async def _send(to, subject, html):
        sent.append(to)
        return True

    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    monkeypatch.setattr(email_service, "send_email", _send)
    assert _signup(client, "quiet@customer.test").status_code == 303
    assert sent == []
    assert 'id="verify-banner"' not in client.get("/watchlist").text  # the class name is in the inlined CSS regardless
    # And alerts are not withheld from anyone.
    assert app_main._email_can_receive("quiet@customer.test") is True


def test_alerts_wait_for_a_confirmed_address_once_live(client, monkeypatch):
    _live(monkeypatch)
    _signup(client, "unconfirmed@customer.test")
    assert app_main._email_can_receive("unconfirmed@customer.test") is False
    with db.get_session() as session:
        u = auth.find_user_by_email(session, "unconfirmed@customer.test")
        u.email_verified_at = datetime.datetime.now(datetime.timezone.utc)
        session.commit()
    assert app_main._email_can_receive("unconfirmed@customer.test") is True
    assert app_main._email_can_receive("nobody@customer.test") is False


def test_the_free_full_report_waits_for_a_confirmed_address(client, monkeypatch):
    """Michael's rule of 7 Sep 2026: the basic report is free to anyone,
    the one free full report is the reward for confirming. Until then the
    cards say so, no unlock is spent and no paywall hit is recorded."""
    from app.models import PageView, PremiumUnlock

    sent = _live(monkeypatch)
    r = client.post("/signup", data={"email": "gated@customer.test", "password": "password123",
                                     "next": "/property?postcode=M1+2AA"}, follow_redirects=False)
    assert r.status_code == 303
    with db.get_session() as session:
        uid = auth.find_user_by_email(session, "gated@customer.test").id
    assert auth.needs_confirmation(db.get_session().__enter__(), uid) is True
    with db.get_session() as session:
        assert auth.claim_unlock(session, uid, "M1 2AA", "") is False
        assert session.query(PremiumUnlock).filter_by(user_id=uid).count() == 0
        assert session.query(PageView).filter_by(user_id=uid, path="/paywall").count() == 0
    # The link carries the report they signed up for; confirming unlocks it.
    link = re.search(r'href="([^"]+/verify-email\?token=[^"]+)"', sent[0]["html"]).group(1)
    r = client.get(link)
    assert r.status_code == 200 and "free full report is ready" in r.text
    assert 'href="/property?postcode=M1+2AA"' in r.text or 'href="/property?postcode=M1%202AA"' in r.text or "Open your report" in r.text
    with db.get_session() as session:
        assert auth.claim_unlock(session, uid, "M1 2AA", "") is True
        assert session.query(PremiumUnlock).filter_by(user_id=uid).count() == 1


def test_the_free_report_is_offered_not_spent_silently(client, fake_report, monkeypatch):
    """Until 7 Sep 2026 the first property a signed-in person opened spent
    their free full report, stray postcode or not. Now the page asks, and
    the spend happens only on a yes."""
    from app.models import PremiumUnlock

    monkeypatch.setattr(email_service, "can_verify", lambda: False)  # confirmation not in play here
    fake_report()
    assert _signup(client, "chooser@customer.test").status_code == 303
    with db.get_session() as session:
        uid = auth.find_user_by_email(session, "chooser@customer.test").id

    body = client.get("/property?postcode=M14+5TG").text
    assert 'class="dashboard-card-lock-overlay"' in body     # still locked (the class name alone is in the inlined CSS)
    assert "Use your free full report here" in body            # the cards say why
    assert 'id="use-free-report-dialog"' in body                # the pop-up asks
    assert "Yes, unlock this property" in body
    with db.get_session() as session:
        assert session.query(PremiumUnlock).filter_by(user_id=uid).count() == 0

    r = client.post("/property/unlock", data={"postcode": "M14 5TG", "house_number": ""}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("unlocked=1")
    with db.get_session() as session:
        assert session.query(PremiumUnlock).filter_by(user_id=uid).count() == 1
    body = client.get(r.headers["location"]).text
    assert 'class="dashboard-card-lock-overlay"' not in body
    assert "Every card on this property is yours" in body
    assert 'id="use-free-report-dialog"' not in body

    # A second property: nothing left, so the paywall wording, no offer.
    from tests.conftest import fake_location
    fake_report(location=fake_location(postcode="M1 2AA", outcode="M1"))  # the fake lookup ignores the query
    body = client.get("/property?postcode=M1+2AA").text
    assert "Upgrade to Premium to unlock" in body and 'id="use-free-report-dialog"' not in body


def test_the_paywall_says_something_new_on_a_return_visit(client, fake_report, monkeypatch):
    """Twelve accounts reached the wall in the week to 9 Sep 2026 and one
    paid. The account that came back most, nine times, has not paid,
    which is one more visit than the person who did, and the wall was
    repeating one sentence at all of them. The second visit onward now
    counts the properties they have opened, prices the searches they
    would otherwise have bought, and points back at the free report they
    already own."""
    from tests.conftest import fake_location

    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    # A postcode of its own, not the M14 5TG the other tests unlock: two
    # accounts unlocking one address on one day is the exact pattern the
    # /admin test asserts is absent.
    fake_report(location=fake_location(postcode="M20 1AA", outcode="M20"))
    assert _signup(client, "returner@customer.test").status_code == 303

    # Spend the one free report, so every later property is walled.
    client.get("/property?postcode=M20+1AA")
    r = client.post("/property/unlock", data={"postcode": "M20 1AA", "house_number": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    client.get(r.headers["location"])

    fake_report(location=fake_location(postcode="M1 2AA", outcode="M1"))

    # The test client names itself "testclient", which the crawler filter
    # excludes on purpose, and an excluded viewer records no paywall
    # event and so never sees the return wording.
    browser = {"user-agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Safari/537.36"}

    # The crawler filter and the 202 wait page share one user-agent
    # test: a browser with a cold gather gets the wait page instead of
    # the report, and fake_report replaces the gather without ever
    # filling its cache. Marking the gather warm is what lets a request
    # be both a real browser and a finished report.
    from app.services import _cache
    _cache.set(("property_search_gather", "M1 2AA", ""), {"warm": True})

    first = client.get("/property?postcode=M1+2AA", headers=browser).text
    assert "You've used your free report." in first          # the original wording
    assert "time you have reached this wall" not in first

    second = client.get("/property?postcode=M1+2AA", headers=browser).text
    assert "This is the 2nd time you have reached this wall." in second
    assert "would be about &pound;" in second
    # The free report they already own is named, and still theirs.
    assert "Your free report went on" in second
    assert "M20 1AA" in second

    third = client.get("/property?postcode=M1+2AA", headers=browser).text
    assert "This is the 3rd time you have reached this wall." in third


def test_can_verify_needs_a_domain_of_our_own(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.delenv("ALERTS_FROM_EMAIL", raising=False)
    assert email_service.can_verify() is False
    monkeypatch.setenv("ALERTS_FROM_EMAIL", "UKPropertyInsight <onboarding@resend.dev>")
    assert email_service.can_verify() is False
    monkeypatch.setenv("ALERTS_FROM_EMAIL", "UKPropertyInsight <hello@ukpropertyinsight.co.uk>")
    assert email_service.can_verify() is True
