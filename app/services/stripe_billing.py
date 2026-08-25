"""Premium subscription billing via Stripe - Checkout Session creation
and webhook event verification/handling.

Talks to Stripe's REST API directly via httpx rather than the official
`stripe` Python SDK, to stay consistent with this project's existing
dependency discipline (one small library per real need, not a heavy
SDK for what's a handful of API calls). Webhook signature verification
is hand-rolled with stdlib hmac/hashlib per Stripe's documented scheme
- the same "hand-roll standard crypto with stdlib rather than add a
dependency" choice auth.py already makes for password hashing.

Each plan has its own free trial length (longer for the quarterly plan
- a bigger up-front commitment gets more time to try it first), applied
at Checkout Session creation time via subscription_data rather than
relying on a trial configured on the Price itself, so it works the
same way regardless of how the Price was set up in the Stripe
dashboard.
"""
import hashlib
import hmac
import os
import time

import httpx

API_BASE = "https://api.stripe.com/v1"
WEBHOOK_TOLERANCE_S = 300  # Stripe's own recommended freshness window

# key -> (env var holding the Stripe Price ID, display label, trial days)
# No free trial on either plan - billing starts immediately at checkout.
PLANS = {
    "monthly": ("STRIPE_PRICE_ID_MONTHLY", "£9.99/month", 0),
    "quarterly": ("STRIPE_PRICE_ID_QUARTERLY", "£24.99/3 months", 0),
}

# One-off purchase, not a subscription: one payment buys PASS_MONTHS of
# Premium, then it simply ends - nothing to cancel. Checkout runs in
# payment mode and the webhook grants time-boxed access.
PASS_PLAN_KEY = "pass"
PASS_PRICE_ENV = "STRIPE_PRICE_ID_PASS"
PASS_LABEL = "£19 once"
PASS_MONTHS = 6

# Saving against paying monthly, shown on the pricing card. £24.99 for
# three months is £8.33 a month against £9.99: 17%. Stated as a number
# because "Best value" on its own asks the reader to do the division.
PLAN_SAVING_PCT = {"quarterly": 17}

# Subscription statuses that should count as "has Premium access" -
# trialing and active both grant access; past_due/canceled/unpaid/
# incomplete/incomplete_expired do not.
_ACTIVE_STATUSES = {"trialing", "active"}


def is_configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def plan_for_price_id(price_id: str | None) -> str | None:
    """Reverse lookup: which PLANS key (if any) a Stripe Price ID
    belongs to - used to record which plan a subscription webhook
    event is actually for, since Stripe's own payload only carries the
    price ID, not our own "monthly"/"quarterly" label."""
    if not price_id:
        return None
    for key, (price_env, _, _) in PLANS.items():
        if os.environ.get(price_env) == price_id:
            return key
    return None


def pass_available() -> bool:
    return bool(os.environ.get(PASS_PRICE_ENV))


def plan_choices() -> list[dict]:
    """[{"key": ..., "label": ..., "trial_days": ..., "available": bool}, ...]
    - available is False if that plan's Price ID env var isn't set, so
    the template can hide a plan that hasn't been configured yet
    rather than offering a button that will fail."""
    return [
        {"key": key, "label": label, "trial_days": trial_days, "available": bool(os.environ.get(price_env)),
         "saving_pct": PLAN_SAVING_PCT.get(key)}
        for key, (price_env, label, trial_days) in PLANS.items()
    ]


async def create_checkout_session(
    plan: str, user_id: int, user_email: str, success_url: str, cancel_url: str
) -> str | None:
    """Returns the Stripe-hosted Checkout URL to redirect the user to,
    or None if not configured, the plan is invalid, or the request to
    Stripe failed."""
    if not is_configured() or (plan not in PLANS and plan != PASS_PLAN_KEY):
        return None
    if plan == PASS_PLAN_KEY:
        price_env, trial_days = PASS_PRICE_ENV, 0
    else:
        price_env, _, trial_days = PLANS[plan]
    price_id = os.environ.get(price_env)
    if not price_id:
        return None

    data = {
        "mode": "payment" if plan == PASS_PLAN_KEY else "subscription",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": 1,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "customer_email": user_email,
        "client_reference_id": str(user_id),
        # customer_creation is only valid in payment mode - in
        # subscription mode Stripe always creates (or reuses) a
        # Customer automatically, no explicit param needed or allowed.
    }
    if trial_days:
        data["subscription_data[trial_period_days]"] = trial_days
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{API_BASE}/checkout/sessions", data=data, auth=(os.environ["STRIPE_SECRET_KEY"], "")
            )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return response.json().get("url")


async def create_billing_portal_session(stripe_customer_id: str, return_url: str) -> str | None:
    """Returns a URL to Stripe's hosted billing portal, where a user
    can update payment details, change plan, or cancel - or None if
    not configured or the request failed."""
    if not is_configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                f"{API_BASE}/billing_portal/sessions",
                data={"customer": stripe_customer_id, "return_url": return_url},
                auth=(os.environ["STRIPE_SECRET_KEY"], ""),
            )
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return response.json().get("url")


def verify_webhook_signature(payload: bytes, sig_header: str | None) -> bool:
    """Confirms a webhook request genuinely came from Stripe, per
    their documented signing scheme - never trust/process a webhook
    body without this passing first, or anyone could POST a fake
    "subscription active" event and grant themselves Premium for
    free."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret or not sig_header:
        return False

    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    timestamp, signature = parts.get("t"), parts.get("v1")
    if not timestamp or not signature:
        return False

    signed_payload = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return False

    try:
        if abs(time.time() - int(timestamp)) > WEBHOOK_TOLERANCE_S:
            return False
    except ValueError:
        return False
    return True


def grants_access(subscription_status: str | None) -> bool:
    return subscription_status in _ACTIVE_STATUSES
