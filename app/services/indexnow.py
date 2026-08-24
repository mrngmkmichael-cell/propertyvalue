"""IndexNow: tell Bing (and through it DuckDuckGo and Yahoo) about our
URLs the moment they change, instead of waiting for a crawl.

Protocol (www.indexnow.org): host a key file at /{key}.txt whose body is
the key, then POST {host, key, urlList} to the shared endpoint. Up to
10,000 URLs per submission, no registration, no auth beyond the hosted
key file. Google does not use IndexNow; this shortcuts the Bing family
only, which indexes in hours rather than weeks.

The key is derived from SESSION_SECRET rather than stored as its own
env var, so production needs no new configuration and the key stays
stable across deploys (rotating SESSION_SECRET rotates the key, which
IndexNow handles fine - the key file always matches by construction).
"""
import hashlib
import logging
import os

import httpx

log = logging.getLogger(__name__)

ENDPOINT = "https://api.indexnow.org/indexnow"


def key() -> str | None:
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        return None
    return hashlib.sha256((secret + ":indexnow-key").encode()).hexdigest()[:32]


async def submit(host: str, urls: list[str]) -> bool:
    """Submit URLs for host. Returns True on an accepted response.
    IndexNow answers 200 or 202 for accepted submissions."""
    k = key()
    if not k or not urls:
        return False
    payload = {"host": host, "key": k, "urlList": urls[:10000]}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(ENDPOINT, json=payload)
        accepted = response.status_code in (200, 202)
        if not accepted:
            log.warning("IndexNow rejected submission: %s %s", response.status_code, response.text[:200])
        return accepted
    except httpx.HTTPError as exc:
        log.warning("IndexNow submission failed: %s", exc)
        return False
