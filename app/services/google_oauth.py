"""Google Sign-In via the OAuth 2.0 authorization code flow.

Hand-rolled against Google's endpoints with httpx rather than pulling in
authlib: the flow is two server-to-server requests and httpx is already a
dependency everywhere else in this project.

We ask for the "openid email" scope only - not profile - because an email
address is the entire account model here (see the User model). No name, no
avatar, nothing else to store or explain in the privacy policy.

Setup, once, in the Google Cloud console (console.cloud.google.com):
  1. APIs & Services -> OAuth consent screen. External, publish it.
  2. APIs & Services -> Credentials -> Create OAuth client ID -> Web
     application.
  3. Under "Authorised redirect URIs" add BOTH:
       http://127.0.0.1:8010/auth/google/callback
       https://ukpropertyinsight.co.uk/auth/google/callback
     They must match byte for byte or Google refuses with redirect_uri_mismatch.
  4. Put the client ID and secret in .env locally, and in Render's
     environment variables for production.
  5. Audience -> Publish app, or only test users can sign in. No review
     is needed: "openid email" are non-sensitive scopes, so the 100-user
     cap shown on that page never applies either.

Do NOT upload a logo on the Branding page. It forces the app into
Google's manual verification queue - days to weeks, during which
sign-in stays restricted to test users. The consent screen shows the app
name and domain without one, which is enough.

The site works fine without any of this configured - the "Continue with
Google" button simply doesn't render, same pattern as the other optional
integrations.
"""
import logging
import os
from urllib.parse import urlencode

import httpx

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

SCOPE = "openid email"


def _client_id() -> str:
    return os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()


def _client_secret() -> str:
    return os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()


def is_configured() -> bool:
    return bool(_client_id() and _client_secret())


def authorization_url(redirect_uri: str, state: str) -> str:
    """Where to send the browser to start the flow."""
    params = {
        "client_id": _client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        # Without this, anyone already signed into Google is silently
        # authenticated as whichever account they happen to be in, which
        # is confusing for the many people with a personal and a work one.
        "prompt": "select_account",
    }
    return f"{AUTH_ENDPOINT}?{urlencode(params)}"


async def fetch_verified_email(code: str, redirect_uri: str) -> str | None:
    """Exchange the one-time code for the user's verified email address.

    Returns None on any failure - a bad code, a Google outage, or an
    account whose email Google itself hasn't verified. Callers should
    treat None as "sign-in failed, show the login page again".

    Both requests here are server-to-server over TLS directly to Google,
    so the response needs no signature checking of its own; that is why
    this reads the userinfo endpoint rather than decoding the id_token.
    """
    data = {
        "code": code,
        "client_id": _client_id(),
        "client_secret": _client_secret(),
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(TOKEN_ENDPOINT, data=data)
            if token_resp.status_code != 200:
                # Google puts the useful part in the body - "invalid_grant"
                # for a reused code, "redirect_uri_mismatch" for a console
                # config that doesn't match what we sent.
                logging.warning(
                    "Google token exchange failed (%s): %s",
                    token_resp.status_code, token_resp.text[:300],
                )
                return None
            access_token = token_resp.json().get("access_token")
            if not access_token:
                logging.warning("Google token response had no access_token")
                return None

            info_resp = await client.get(
                USERINFO_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if info_resp.status_code != 200:
                logging.warning(
                    "Google userinfo failed (%s): %s",
                    info_resp.status_code, info_resp.text[:300],
                )
                return None
            info = info_resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logging.warning("Google sign-in request failed: %s", exc)
        return None

    email = (info.get("email") or "").strip().lower()
    if not email:
        return None
    # An unverified email would let someone claim an address they don't own,
    # and since we match accounts by email that would hand them any existing
    # account with that address.
    if not info.get("email_verified"):
        logging.warning("Google sign-in refused: email not verified")
        return None
    return email
