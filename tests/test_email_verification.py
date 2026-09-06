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


def test_can_verify_needs_a_domain_of_our_own(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test")
    monkeypatch.delenv("ALERTS_FROM_EMAIL", raising=False)
    assert email_service.can_verify() is False
    monkeypatch.setenv("ALERTS_FROM_EMAIL", "UKPropertyInsight <onboarding@resend.dev>")
    assert email_service.can_verify() is False
    monkeypatch.setenv("ALERTS_FROM_EMAIL", "UKPropertyInsight <hello@ukpropertyinsight.co.uk>")
    assert email_service.can_verify() is True
