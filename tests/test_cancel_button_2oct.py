"""A cancel button that goes straight to Stripe (2 Oct 2026).

A customer subscribed on 6 September and emailed ninety-five minutes
later asking to cancel. The email was missed for four weeks and the
subscription renewed. Cancelling was possible the whole time, but only
by finding the way out inside Stripe's portal, and whether a cancel
button appeared there at all rested on a dashboard setting nobody had
checked.

These tests hold the three things that stop that happening again: a
subscriber sees a cancel button, it goes to Stripe rather than
cancelling anything here, and the page promises the paid month is kept,
which is a promise the portal configuration has to keep too.
"""
from app import main as app_main
from app.services import stripe_billing


def flat(text):
    return " ".join(text.split())


def test_the_portal_configuration_asks_for_cancelling_at_period_end():
    """The promise on the page is 'you keep the month you paid for'.
    That is this setting, and nothing else makes it true."""
    features = stripe_billing.PORTAL_FEATURES
    assert features["features[subscription_cancel][enabled]"] == "true"
    assert features["features[subscription_cancel][mode]"] == "at_period_end"
    assert features["features[subscription_cancel][proration_behavior]"] == "none"


def test_a_cancel_link_is_a_stripe_link_and_nothing_is_cancelled_here(monkeypatch):
    """The route hands off. It must never write a cancellation itself:
    Stripe decides and the webhook tells us."""
    import inspect
    source = inspect.getsource(app_main.premium_cancel_subscription)
    assert "create_billing_portal_session" in source
    for writing in ("is_premium = False", "subscription_status =", "DELETE", "delete("):
        assert writing not in source, f"the route writes: {writing}"


def test_the_session_opens_on_the_cancellation_screen_for_that_subscription():
    import inspect
    source = inspect.getsource(stripe_billing.create_billing_portal_session)
    assert "flow_data[type]" in source and "subscription_cancel" in source
    assert "flow_data[subscription_cancel][subscription]" in source
    # And the plain manage link must not become a cancel link by accident.
    assert "cancel_subscription: str | None = None" in source


class _Account:
    """The few fields premium_state reads, and nothing else."""
    is_premium = True
    plan = "monthly"
    pass_expires_at = None
    id = 1

    def __init__(self, customer=None, subscription=None, plan="monthly"):
        self.stripe_customer_id = customer
        self.stripe_subscription_id = subscription
        self.plan = plan


def test_only_a_real_subscription_gets_the_button():
    """A comped account and a one-off pass have nothing to cancel, so
    offering it would be a dead end."""
    from app import auth
    comped = auth.premium_state(_Account(plan="comped"), None)
    subscriber = auth.premium_state(_Account("cus_123", "sub_123"), None)
    assert "has_subscription" in comped
    assert comped["has_subscription"] is False
    assert subscriber["has_subscription"] is True
    # Both still count as Premium: the difference is only what can be cancelled.
    assert comped["subscribed"] and subscriber["subscribed"]


def test_signed_out_is_sent_to_log_in_rather_than_to_stripe(client):
    client.cookies.clear()
    reply = client.post("/premium/cancel-subscription", follow_redirects=False)
    assert reply.status_code == 303
    assert reply.headers["location"] == "/login?next=/premium"


def test_the_page_says_what_cancelling_does_to_the_paid_month(client, monkeypatch):
    """The suite blanks the Stripe key, and with no key the page is the
    "not open for sign-up yet" version with no buttons at all. A dummy
    key is enough to render the subscriber view; nothing here calls
    Stripe."""
    from app import auth, db
    from app.services import email as email_service
    from tests.test_email_verification import _signup
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_not_a_real_key")
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, "cancel-button@customer.test").status_code == 303
    with db.get_session() as session:
        user = auth.find_user_by_email(session, "cancel-button@customer.test")
        user.is_premium, user.plan = True, "monthly"
        user.subscription_status = "active"
        user.stripe_customer_id, user.stripe_subscription_id = "cus_t", "sub_t"
        session.commit()
    try:
        body = flat(client.get("/premium").text)
        assert 'action="/premium/cancel-subscription"' in body
        assert "Cancel subscription" in body
        assert "end of the month you have already paid for" in body
        assert "Stripe handles it and emails you the confirmation" in body
    finally:
        # The suite shares one SQLite file and /admin's revenue table
        # counts every row carrying a subscription id, so a subscriber
        # left behind here is a phantom customer in another test's
        # totals. It was, until this cleanup.
        with db.get_session() as session:
            user = auth.find_user_by_email(session, "cancel-button@customer.test")
            if user is not None:
                session.delete(user)
                session.commit()
        client.cookies.clear()


def test_the_support_page_no_longer_sends_people_hunting_in_the_portal(client):
    # The old wording sent people into the portal to find the way out.
    support = flat(client.get("/support").text)
    assert "Cancel subscription" in support
    assert 'click "Manage subscription". You can update payment details, change plan, or cancel' \
        not in support
