"""Facebook and LinkedIn sign-in, same contract as google_oauth:
is_configured() / authorization_url() / fetch_verified_email(). Both are
plain OAuth 2.0 authorization-code flows over httpx, and both are scoped
to the email address only - the email IS the account model here.

Sign in with Apple is deliberately absent: registering it requires a
paid Apple Developer membership ($99/year) and a signed-JWT client
secret, so it's a separate decision, not a config value.

Setup, once per provider (put the IDs in .env and Render's env vars;
each button only renders when its provider is configured):

Facebook - developers.facebook.com:
  1. Create App -> type "Consumer" (or "Other/None"), no business needed.
  2. Add product "Facebook Login" -> Web. Site URL:
     https://ukpropertyinsight.co.uk
  3. Facebook Login -> Settings -> Valid OAuth Redirect URIs, add BOTH:
       http://127.0.0.1:8010/auth/facebook/callback  (needs "Login with
       the JavaScript SDK" left off; localhost works while the app is in
       Development mode)
       https://ukpropertyinsight.co.uk/auth/facebook/callback
  4. App settings -> Basic: copy App ID / App secret into
     FACEBOOK_OAUTH_CLIENT_ID / FACEBOOK_OAUTH_CLIENT_SECRET.
  5. Switch the app from Development to Live mode (top bar). The "email"
     permission is Standard Access - no App Review needed.

LinkedIn - developer.linkedin.com:
  1. Create app (needs any LinkedIn Page to attach it to - a personal
     company page for UKPropertyInsight is fine).
  2. Products tab -> request "Sign In with LinkedIn using OpenID
     Connect" (self-serve, instant).
  3. Auth tab -> Authorized redirect URLs, add BOTH:
       http://127.0.0.1:8010/auth/linkedin/callback
       https://ukpropertyinsight.co.uk/auth/linkedin/callback
  4. Copy Client ID / Client Secret into
     LINKEDIN_OAUTH_CLIENT_ID / LINKEDIN_OAUTH_CLIENT_SECRET.
"""
import logging
import os
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx


@dataclass(frozen=True)
class _Provider:
    name: str
    auth_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    scope: str
    env_prefix: str
    # Facebook exchanges the code with GET + query params; everyone else
    # POSTs a form.
    token_via_get: bool = False


_FACEBOOK = _Provider(
    name="facebook",
    auth_endpoint="https://www.facebook.com/v19.0/dialog/oauth",
    token_endpoint="https://graph.facebook.com/v19.0/oauth/access_token",
    # Facebook only returns an email the account holder has confirmed,
    # so presence in this response is the verification signal.
    userinfo_endpoint="https://graph.facebook.com/v19.0/me?fields=email",
    scope="email",
    env_prefix="FACEBOOK_OAUTH",
    token_via_get=True,
)

_LINKEDIN = _Provider(
    name="linkedin",
    auth_endpoint="https://www.linkedin.com/oauth/v2/authorization",
    token_endpoint="https://www.linkedin.com/oauth/v2/accessToken",
    userinfo_endpoint="https://api.linkedin.com/v2/userinfo",
    scope="openid email",
    env_prefix="LINKEDIN_OAUTH",
)

_PROVIDERS = {p.name: p for p in (_FACEBOOK, _LINKEDIN)}


def _credentials(provider: _Provider) -> tuple[str, str]:
    return (
        os.environ.get(f"{provider.env_prefix}_CLIENT_ID", "").strip(),
        os.environ.get(f"{provider.env_prefix}_CLIENT_SECRET", "").strip(),
    )


def is_configured(name: str) -> bool:
    provider = _PROVIDERS.get(name)
    if provider is None:
        return False
    client_id, client_secret = _credentials(provider)
    return bool(client_id and client_secret)


def authorization_url(name: str, redirect_uri: str, state: str) -> str:
    provider = _PROVIDERS[name]
    client_id, _ = _credentials(provider)
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": provider.scope,
        "state": state,
    }
    return f"{provider.auth_endpoint}?{urlencode(params)}"


async def fetch_verified_email(name: str, code: str, redirect_uri: str) -> str | None:
    """Exchange the one-time code for a verified email, or None on any
    failure - same semantics as google_oauth.fetch_verified_email."""
    provider = _PROVIDERS[name]
    client_id, client_secret = _credentials(provider)
    token_params = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if provider.token_via_get:
                token_resp = await client.get(provider.token_endpoint, params=token_params)
            else:
                token_resp = await client.post(provider.token_endpoint, data=token_params)
            if token_resp.status_code != 200:
                logging.warning(
                    "%s token exchange failed (%s): %s",
                    provider.name, token_resp.status_code, token_resp.text[:300],
                )
                return None
            access_token = token_resp.json().get("access_token")
            if not access_token:
                logging.warning("%s token response had no access_token", provider.name)
                return None

            info_resp = await client.get(
                provider.userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if info_resp.status_code != 200:
                logging.warning(
                    "%s userinfo failed (%s): %s",
                    provider.name, info_resp.status_code, info_resp.text[:300],
                )
                return None
            info = info_resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logging.warning("%s sign-in request failed: %s", provider.name, exc)
        return None

    email = (info.get("email") or "").strip().lower()
    if not email:
        # A Facebook account registered by phone number has no email to
        # give us, and the email is the whole account model here.
        logging.warning("%s sign-in refused: no email in userinfo", provider.name)
        return None
    # LinkedIn's OIDC userinfo carries email_verified; Facebook's Graph
    # response doesn't (a returned email is already confirmed).
    if "email_verified" in info and not info.get("email_verified"):
        logging.warning("%s sign-in refused: email not verified", provider.name)
        return None
    return email
