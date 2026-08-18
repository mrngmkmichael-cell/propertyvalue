"""Password hashing (stdlib PBKDF2, no extra dependency) and the
current-user lookup from the signed session cookie set up by
Starlette's SessionMiddleware in main.py.
"""
import hashlib
import hmac
import os
from typing import Optional

from fastapi import Request
from sqlalchemy import select

from app.db import get_session, is_configured
from app.models import User

_ITERATIONS = 260_000


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
            return {"id": user.id, "email": user.email, "is_premium": user.is_premium}
    except Exception:
        # base_context() calls this on every page load, including the
        # error pages themselves - a transient DB hiccup shouldn't take
        # down the whole site, just quietly log the visitor out.
        return None


def find_user_by_email(db, email: str) -> Optional[User]:
    return db.scalar(select(User).where(User.email == email))
