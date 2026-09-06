"""Conversions: the moment an account becomes Premium is recorded once,
the owner hears about it on Telegram once, and the admin page lists
who is Premium with the time it took them to convert."""
import datetime
import json

from sqlalchemy import select

from app import auth, db
from app import main as app_main
from app.models import PremiumUnlock, User
from app.services import stripe_billing, telegram


def _user(email: str, joined_days_ago: int = 3) -> int:
    with db.get_session() as session:
        u = User(email=email, password_hash=auth.hash_password("password123"),
                 created_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=joined_days_ago))
        session.add(u)
        session.commit()
        session.add(PremiumUnlock(user_id=u.id, postcode="KT3 4HX", house_number="36"))
        session.commit()
        return u.id


def _stub_telegram(monkeypatch):
    sent = []

    async def _send(text):
        sent.append(text)
        return True

    monkeypatch.setattr(telegram, "is_configured", lambda: True)
    monkeypatch.setattr(telegram, "send_message", _send)
    monkeypatch.setattr(stripe_billing, "verify_webhook_signature", lambda payload, sig: True)
    return sent


def _event(kind: str, obj: dict) -> bytes:
    return json.dumps({"type": kind, "data": {"object": obj}}).encode()


def test_a_new_subscription_is_dated_once_and_announced_once(client, monkeypatch):
    sent = _stub_telegram(monkeypatch)
    uid = _user("buyer@example.test", joined_days_ago=2)
    monkeypatch.setattr(stripe_billing, "plan_for_price_id", lambda price_id: "monthly")

    r = client.post("/webhooks/stripe",content=_event("checkout.session.completed", {
        "client_reference_id": str(uid), "customer": "cus_test1", "mode": "subscription", "subscription": "sub_test1",
    }), headers={"stripe-signature": "x"})
    assert r.status_code == 200
    r = client.post("/webhooks/stripe",content=_event("customer.subscription.created", {
        "id": "sub_test1", "customer": "cus_test1", "status": "active",
        "items": {"data": [{"price": {"id": "price_monthly"}}]},
    }), headers={"stripe-signature": "x"})
    assert r.status_code == 200

    with db.get_session() as session:
        u = session.get(User, uid)
        assert u.is_premium and u.plan == "monthly"
        first_since = u.premium_since
        assert first_since is not None
    assert len(sent) == 1
    assert "buyer@example.test" in sent[0] and "monthly" in sent[0] and "£9.99/month" in sent[0]
    assert "2 days after joining" in sent[0] and "KT3 4HX no. 36" in sent[0]

    # A renewal arrives as the same "updated" event: no new date, no second message.
    r = client.post("/webhooks/stripe",content=_event("customer.subscription.updated", {
        "id": "sub_test1", "customer": "cus_test1", "status": "active",
        "items": {"data": [{"price": {"id": "price_monthly"}}]},
    }), headers={"stripe-signature": "x"})
    assert r.status_code == 200
    with db.get_session() as session:
        assert session.get(User, uid).premium_since == first_since
    assert len(sent) == 1


def test_a_pass_purchase_is_dated_and_announced(client, monkeypatch):
    sent = _stub_telegram(monkeypatch)
    uid = _user("passbuyer@example.test", joined_days_ago=0)
    r = client.post("/webhooks/stripe",content=_event("checkout.session.completed", {
        "client_reference_id": str(uid), "customer": "cus_pass", "mode": "payment", "payment_status": "paid",
    }), headers={"stripe-signature": "x"})
    assert r.status_code == 200
    with db.get_session() as session:
        u = session.get(User, uid)
        assert u.is_premium and u.plan == "pass" and u.premium_since is not None
    assert len(sent) == 1 and "bought the pass" in sent[0] and "the same day" in sent[0]


def test_admin_lists_premium_accounts_with_days_to_convert(client, monkeypatch):
    _stub_telegram(monkeypatch)
    uid = _user("quarterly@example.test", joined_days_ago=10)
    with db.get_session() as session:
        u = session.get(User, uid)
        u.is_premium, u.plan, u.subscription_status = True, "quarterly", "active"
        u.premium_since = u.created_at + datetime.timedelta(days=4, hours=2)
        session.commit()
    with db.get_session() as session:
        rows = app_main._premium_accounts(session, datetime.datetime.now(datetime.timezone.utc))
    row = next(r for r in rows if r["email"] == "quarterly@example.test")
    assert row["days_to_convert"] == 4
    assert row["value"] == "£8.33/month"
    assert row["first_search"] == "KT3 4HX no. 36"


def test_revenue_table_lists_active_and_cancelled_and_ignores_test_purchases(client, monkeypatch):
    """Three of the owner's own test purchases carried a Stripe
    subscription ID but never a status, and the revenue table showed
    them as "Unknown 3", which read as three lost customers. They are
    excluded with the rest of the test accounts; Active and Cancelled
    are always listed, even at zero."""
    _stub_telegram(monkeypatch)
    with db.get_session() as session:
        for email, sub, status in (
            ("tester@ukpropertyinsight.co.uk", "sub_sitetest", None),      # site-domain test account, no status
            ("refunded@example.test", "sub_refunded", None),
            ("paying@customer.test", "sub_live_1", "active"),
            ("left@customer.test", "sub_live_2", "canceled"),
        ):
            session.add(User(email=email, password_hash=auth.hash_password("password123"),
                             stripe_customer_id="cus_" + sub, stripe_subscription_id=sub, subscription_status=status,
                             is_premium=status == "active", plan="monthly" if status else None))
        session.commit()
        m = app_main._admin_metrics(session, datetime.datetime.now(datetime.timezone.utc))
    rows = {r["label"]: r["count"] for r in m["subscription_status_breakdown"]}
    assert rows["Active"] >= 1 and rows["Cancelled"] == 1
    assert "Unknown" not in rows and "None" not in rows
    assert m["subscriptions_without_status"] == 0
