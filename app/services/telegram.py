"""Telegram Bot API notifications - powers the daily admin summary
(see main.py's /internal/send-daily-summary). Same "returns False
rather than raising when unconfigured" pattern as email.py, so a
missing bot token/chat ID just means no message gets sent, nothing
else breaks.

Requires a bot created via @BotFather on Telegram (free, no card,
takes a couple of minutes) and the numeric chat ID of whoever should
receive messages - see .env.example for the one-time setup steps.
"""
import os

import httpx

TELEGRAM_API_BASE = "https://api.telegram.org"


def is_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


async def send_message(text: str) -> bool:
    if not is_configured():
        return False
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
        response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False
