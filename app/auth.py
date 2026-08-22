"""Password hashing (stdlib PBKDF2, no extra dependency) and the
current-user lookup from the signed session cookie set up by
Starlette's SessionMiddleware in main.py.
"""
import datetime
import hashlib
import hmac
import os
from typing import Optional

from fastapi import Request
from sqlalchemy import select

from app.db import get_session, is_configured
from app.models import User

_ITERATIONS = 260_000

# Stored in password_hash for accounts created through Google Sign-In,
# which have no password at all. The column is NOT NULL and adding a
# nullable one would mean migrating a live database, so a sentinel does
# the job instead: verify_password() splits the stored value on "$" and
# returns False when that fails, so this can never match any password.
GOOGLE_ACCOUNT_PLACEHOLDER = "google-oauth-no-password"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(digest_hex)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return hmac.compare_digest(actual, expected)


# Every new account gets this long with the Premium checks unlocked, no
# card required. Chosen over a card-first Stripe trial deliberately: the
# whole pitch is that searching is free, and asking for card details at
# signup would contradict that and collapse the signup rate. The cost of
# a trial user is a handful of extra API calls on data we already fetch.
SIGNUP_TRIAL_DAYS = 14


def premium_state(user) -> dict:
    """Effective Premium access for a user row.

    Access comes from either a paid/trialing Stripe subscription
    (is_premium, set by webhook) or an unexpired signup trial
    (trial_ends_at). They are deliberately kept as separate fields
    rather than just flipping is_premium at signup: is_premium is owned
    by the Stripe webhook and would be overwritten by it, and keeping
    them apart is what lets the UI say "3 days of your trial left"
    rather than "you are subscribed".

    A signup trial is identifiable as one because no Stripe subscription
    sits behind it - which is why this needs no extra column."""
    now = datetime.datetime.now(datetime.timezone.utc)
    ends = user.trial_ends_at
    if ends is not None and ends.tzinfo is None:
        # Postgres can hand back a naive datetime depending on driver
        # and column type; treat it as UTC rather than crashing on the
        # comparison below.
        ends = ends.replace(tzinfo=datetime.timezone.utc)

    trial_active = bool(ends and ends > now)
    on_signup_trial = trial_active and not user.stripe_subscription_id
    days_left = max(0, (ends - now).days + 1) if trial_active else 0

    return {
        "is_premium": bool(user.is_premium) or trial_active,
        "subscribed": bool(user.is_premium) and bool(user.stripe_subscription_id),
        "on_trial": on_signup_trial,
        "trial_days_left": days_left if on_signup_trial else 0,
    }


def start_signup_trial(user) -> None:
    """Give a brand-new account its free Premium window. Only ever called
    at account creation, and never overwrites an existing value, so a
    returning user cannot restart the clock by any route."""
    if user.trial_ends_at is None:
        user.trial_ends_at = (
            datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=SIGNUP_TRIAL_DAYS)
        )


def current_user(request: Request) -> Optional[dict]:
    if not is_configured():
        return None
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    try:
        with get_session() as db:
            user = db.get(User, user_id)
            if user is None:
                return None
            return {"id": user.id, "email": user.email, **premium_state(user)}
    except Exception:
        # base_context() calls this on every page load, including the
        # error pages themselves - a transient DB hiccup shouldn't take
        # down the whole site, just quietly log the visitor out.
        return None


def find_user_by_email(db, email: str) -> Optional[User]:
    return db.scalar(select(User).where(User.email == email))
