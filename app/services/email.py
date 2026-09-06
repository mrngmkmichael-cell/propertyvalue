"""Transactional email via Resend's API (free tier: 3,000 emails/month,
100/day) - requires a self-registered API key (see .env.example), same
pattern as every other optional integration in this app (routing.py,
rail_journey.py): returns False rather than raising when unconfigured,
so a missing key means "no emails sent," not a broken feature elsewhere.

ALERTS_FROM_EMAIL defaults to Resend's own shared testing address
(onboarding@resend.dev), which works with zero setup - switching to a
branded ukpropertyinsight.co.uk address requires verifying that domain
in the Resend dashboard first (DNS records), so this is left as an
explicit opt-in via env var rather than assumed.
"""
import os

import httpx

RESEND_API_URL = "https://api.resend.com/emails"


DEFAULT_FROM_ADDRESS = "UKPropertyInsight <onboarding@resend.dev>"


def is_configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY"))


def from_address() -> str:
    return os.environ.get("ALERTS_FROM_EMAIL") or DEFAULT_FROM_ADDRESS


def can_verify() -> bool:
    """Whether asking people to confirm their address is worth doing.

    Resend's shared test sender only delivers to the account owner's own
    inbox, so a confirmation sent from it never reaches a customer, and a
    site that asks for confirmations it cannot deliver just loses people.
    True only once a sending domain the site owns is verified in Resend
    and named in ALERTS_FROM_EMAIL. Until then verification is dark:
    no banner, no sends, no gating."""
    return is_configured() and bool(os.environ.get("ALERTS_FROM_EMAIL")) and "resend.dev" not in from_address()


async def send_email(to: str, subject: str, html: str) -> bool:
    if not is_configured():
        return False
    from_address_value = from_address()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                RESEND_API_URL,
                headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
                json={"from": from_address_value, "to": [to], "subject": subject, "html": html},
            )
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False
