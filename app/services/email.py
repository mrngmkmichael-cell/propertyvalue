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


def is_configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY"))


async def send_email(to: str, subject: str, html: str) -> bool:
    if not is_configured():
        return False
    from_address = os.environ.get("ALERTS_FROM_EMAIL", "UKPropertyInsight <onboarding@resend.dev>")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                RESEND_API_URL,
                headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
                json={"from": from_address, "to": [to], "subject": subject, "html": html},
            )
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False
