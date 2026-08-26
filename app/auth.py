"""Password hashing (stdlib PBKDF2, no extra dependency) and the
current-user lookup from the signed session cookie set up by
Starlette's SessionMiddleware in main.py.
"""
import datetime
import hashlib
from datetime import datetime, timezone
import hmac
import os
from typing import Optional

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import get_session, is_configured
from app.models import PremiumUnlock, User

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


# How many full Premium reports a new account gets before the paywall.
# Usage-based rather than a time-limited trial, deliberately: buying a
# house is episodic. Someone signs up, browses, then views nothing for
# three weeks - a 14-day clock would expire before they ever used it on
# a real decision, and they would never have seen what they were paying
# for. An unlock survives that gap.
#
# Cut from three to one on 28 Aug 2026. Of 23 accounts, exactly one had
# ever reached the paywall: almost nobody researching a house opens
# three different properties in full, so the paid tier was never really
# being offered. One report still shows the whole product on a property
# someone actually cares about.
#
# Lowering this never takes anything away: claim_unlock returns early
# for a property already unlocked, so anything opened under a more
# generous allowance stays open for good, which is what the paywall has
# always promised.
FREE_PREMIUM_UNLOCKS = 1


def property_key(postcode: str, house_number: str = "") -> tuple[str, str]:
    """Canonical (postcode, house_number) an unlock is recorded against.
    Matches the watchlist's key so the same property means the same
    thing everywhere."""
    return (postcode or "").strip().upper(), (house_number or "").strip()


def unlocks_used(db, user_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(PremiumUnlock).where(PremiumUnlock.user_id == user_id)
    ) or 0


def has_unlocked(db, user_id: int, postcode: str, house_number: str = "") -> bool:
    pc, hn = property_key(postcode, house_number)
    return db.scalar(
        select(PremiumUnlock.id).where(
            PremiumUnlock.user_id == user_id,
            PremiumUnlock.postcode == pc,
            PremiumUnlock.house_number == hn,
        )
    ) is not None


def claim_unlock(db, user_id: int, postcode: str, house_number: str = "") -> bool:
    """Spend a free unlock on this property, if one is needed and one is
    left. Returns whether the user should now see the full report.

    Returns True without spending anything when this property is already
    unlocked, which is the common case for a refresh or a return visit.
    The IntegrityError branch covers two tabs racing on the same new
    property: the unique constraint rejects the second write, and the
    user still gets access because the first one succeeded."""
    pc, hn = property_key(postcode, house_number)
    if has_unlocked(db, user_id, pc, hn):
        return True
    if unlocks_used(db, user_id) >= FREE_PREMIUM_UNLOCKS:
        return False

    db.add(PremiumUnlock(user_id=user_id, postcode=pc, house_number=hn))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return True


def has_active_premium(user) -> bool:
    """is_premium, minus a buying pass that has run out. The pass is
    downgraded lazily on read rather than by a background job - the
    first page view after expiry simply sees it as lapsed."""
    if not user.is_premium:
        return False
    if user.plan == "pass" and user.pass_expires_at is not None:
        expires = user.pass_expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires > datetime.now(timezone.utc)
    return True


def premium_state(user, db=None) -> dict:
    """Effective Premium access for a user row.

    Two independent sources: a paid or trialing Stripe subscription
    (is_premium, owned by the webhook) and the free unlock allowance.
    They are kept apart rather than folded into is_premium because the
    webhook overwrites that field and would silently revoke a free
    user's remaining unlocks.

    is_premium here means "subscribed", not "can see this property" -
    per-property access needs the postcode, so it is decided by
    claim_unlock at the point of viewing."""
    subscribed = has_active_premium(user)
    used = unlocks_used(db, user.id) if db is not None else 0
    return {
        "is_premium": subscribed,
        "subscribed": subscribed,
        "free_unlocks_total": FREE_PREMIUM_UNLOCKS,
        "free_unlocks_used": used,
        "free_unlocks_left": max(0, FREE_PREMIUM_UNLOCKS - used) if not subscribed else 0,
    }


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
            return {
                "id": user.id, "email": user.email,
                "weekly_digest": bool(user.weekly_digest),
                **premium_state(user, db),
            }
    except Exception:
        # base_context() calls this on every page load, including the
        # error pages themselves - a transient DB hiccup shouldn't take
        # down the whole site, just quietly log the visitor out.
        return None


def find_user_by_email(db, email: str) -> Optional[User]:
    return db.scalar(select(User).where(User.email == email))
