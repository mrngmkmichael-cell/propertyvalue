import asyncio
import collections
import contextvars
import hashlib
import datetime
import hmac
import json
import logging
import math
import os
import re
import secrets
import statistics
import time
from urllib.parse import quote, urlencode
from xml.sax.saxutils import escape

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from markupsafe import Markup
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import func, select

from app import auth, db, school_shortlist, watchlist
from app.services import _cache
from app.models import FigureReport, PageCache, PageView, PremiumUnlock, ShareLink, User
from app.services import (
    air_quality, amenities, area_stats, boe_rate, broadband, catchment, census_stats, clay_risk, coal_mining,
    cqc_ratings, crime, demographics, designations, email as email_service, epc, flood, flood_zones,
    food_hygiene, google_oauth, google_places, heritage, historic_landfill, hpi, mobile_coverage, noise, orientation,
    oauth_providers, overview_score, pdf_export, place_search, radon, rental, reviews, routing, schools_db, sewage_discharge,
    og_image,
    stripe_billing, surface_water_risk, telegram, valuation,
    solicitor_questions, indexnow, council_tax,
)
from app.services.land_registry import sold_prices_for_postcode, sold_prices_for_postcodes
from app.services import postcodes
from app.services.postcodes import any_postcode_in_outcode, lookup_postcode, nearby_postcodes, outcode_centroid

load_dotenv()

# Render sets this automatically on every deployed service - used to tell a
# production run from a local one, since only production is reachable over
# HTTPS (the session cookie's Secure flag would otherwise break local dev).
IS_PRODUCTION = bool(os.environ.get("RENDER"))

SESSION_SECRET = os.environ.get("SESSION_SECRET")
if not SESSION_SECRET:
    # A hardcoded fallback would let anyone forge a signed session/extension
    # token for this app. A random one still lets the process run (sessions
    # just won't survive a restart) without being guessable.
    SESSION_SECRET = secrets.token_hex(32)
    logging.critical(
        "SESSION_SECRET is not set - using a random secret for this process only. "
        "Sessions and extension logins will not survive a restart. Set SESSION_SECRET "
        "in the environment to fix this."
    )

app = FastAPI(title="UKPropertyInsight")
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    https_only=IS_PRODUCTION,
    same_site="lax",
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """The standard set that an audit expects and that cost nothing.

    Not here: Content-Security-Policy. This site inlines its stylesheet
    and several scripts deliberately (see inline_css), so a CSP would
    need per-request nonces threaded through every template before it
    could be anything stricter than 'unsafe-inline', which is not worth
    shipping. Clickjacking and MIME sniffing are covered below; CSP is
    the one to revisit if the inline scripts ever move to files.

    X-Frame-Options is safe to set: the /embed feature for agents is a
    link plus an image, not an iframe of this site."""
    response = await call_next(request)
    h = response.headers
    # Only meaningful over HTTPS, and only production is. One year,
    # subdomains included, no preload until the owner opts in.
    if IS_PRODUCTION:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    return response


@app.middleware("http")
async def support_head_requests(request: Request, call_next):
    """Plain @app.get(...) routes only ever register GET, so a HEAD request
    to any page (Google's sitemap fetcher and many crawlers/uptime checks
    HEAD a URL before GET-ing it) gets a 405 even though the same URL works
    fine over GET - this was silently breaking Search Console's ability to
    fetch /sitemap.xml. Routing this internally as GET and stripping the
    body afterwards fixes HEAD site-wide without touching every route.
    Runs after capture_pageview so that middleware still sees the real
    original method and its existing GET-only check keeps HEAD probes out
    of the pageview counts."""
    if request.method != "HEAD":
        return await call_next(request)
    request.scope["method"] = "GET"
    response = await call_next(request)
    # Uvicorn reads the method from this same scope when it sends the
    # response: left as GET, it expects a body matching Content-Length,
    # gets the stripped empty one, and kills the connection with
    # "Response content shorter than Content-Length". Restoring HEAD
    # before the response goes out tells it an empty body is correct.
    request.scope["method"] = "HEAD"
    return Response(status_code=response.status_code, headers=dict(response.headers), media_type=response.media_type)


REFERRAL_COOKIE = "pv_ref"
REFERRAL_COOKIE_MAX_AGE_S = 60 * 60 * 24 * 30  # 30 days between clicking a partner link and actually signing up is generous but not unreasonable
_SAFE_REF_RE = re.compile(r"[^A-Za-z0-9_-]")


@app.middleware("http")
async def capture_referral(request: Request, call_next):
    """A ?ref=CODE on any URL (not just /signup) gets remembered in a
    cookie, so a partner's link can point at a normal property/search
    page - not force everyone through /signup first - and still get
    credit if that visit later turns into a real signup. First-touch
    attribution: an existing cookie is never overwritten by a later ref,
    so whoever actually brought the person here keeps the credit."""
    ref = request.query_params.get("ref")
    response = await call_next(request)
    if ref and REFERRAL_COOKIE not in request.cookies:
        safe_ref = _SAFE_REF_RE.sub("", ref)[:64]
        if safe_ref:
            response.set_cookie(
                REFERRAL_COOKIE, safe_ref, max_age=REFERRAL_COOKIE_MAX_AGE_S, httponly=True, samesite="lax"
            )
    return response


# Any prefix here never counts as a "page" - static assets, the JSON
# API the extension uses, internal cron endpoints, and the Stripe
# webhook are all real traffic but not what "how many people viewed
# the site" is asking about.
_PAGEVIEW_EXCLUDE_PREFIXES = ("/static/", "/api/", "/internal/", "/webhooks/")
_PAGEVIEW_EXCLUDE_PATHS = {"/robots.txt", "/sitemap.xml", "/favicon.ico"}
if indexnow.key():
    _PAGEVIEW_EXCLUDE_PATHS.add(f"/{indexnow.key()}.txt")

# Synthetic pageview path written when a logged-in account with no free
# reports left opens a property it has not unlocked. Not a real URL;
# it exists so the admin funnel can count "hit the wall".
PAYWALL_PATH = "/paywall"

# Search-engine and monitoring crawlers aren't people viewing the site,
# and with 378 URLs in the sitemap Googlebot alone would otherwise show
# up as hundreds of "visitors" a day on /admin. The user-agent is only
# tested here, never stored - the PageView row stays as minimal as
# models.py promises. Matched case-insensitively as substrings; the
# list is the well-known self-identifying crawlers, not an attempt at
# fingerprinting anything that isn't a browser.
# Set on the owner's browsers by the button on /admin. Pageviews from any
# browser carrying it are skipped, logged in or not - the logged-in
# check alone missed every logged-out visit the owner made.
PAGEVIEW_EXCLUDE_COOKIE = "pv_exclude"

# Counts before this are known to include the owner's logged-out visits
# and the assistant's verification browser. The dashboard reports from
# here as the honest baseline and labels everything earlier.
PAGEVIEW_CLEAN_FROM = datetime.datetime(2026, 8, 23, tzinfo=datetime.timezone.utc)

_CRAWLER_UA_MARKERS = (
    "googlebot", "bingbot", "slurp", "duckduckbot", "baiduspider", "yandexbot",
    "applebot", "facebookexternalhit", "twitterbot", "linkedinbot", "whatsapp",
    "telegrambot", "discordbot", "slackbot", "pinterestbot", "petalbot",
    "ahrefsbot", "semrushbot", "mj12bot", "dotbot", "seznambot", "gptbot",
    "claudebot", "ccbot", "bytespider", "uptimerobot", "pingdom", "site24x7",
    "render/", "curl/", "wget/", "python-requests", "python-httpx", "go-http-client",
    "headlesschrome", "lighthouse", "crawler", "spider", "bot/", "bot;",
    # The browser built into Claude Code, which the owner's assistant uses
    # to verify changes. A real Chrome, so it was counted as a visitor
    # until 23 Aug 2026; it identifies itself and is now excluded.
    "claude/", "electron/", "testclient",
)


def _is_crawler(user_agent: str | None) -> bool:
    if not user_agent:
        return True  # real browsers always send one
    ua = user_agent.lower()
    return any(marker in ua for marker in _CRAWLER_UA_MARKERS)


def _is_excluded_viewer(request: Request) -> bool:
    """Crawlers, scripts and any browser the owner has marked as theirs.
    Shared by the pageview middleware and the paywall event."""
    return _is_crawler(request.headers.get("user-agent")) or request.cookies.get(PAGEVIEW_EXCLUDE_COOKIE) == "1"


@app.middleware("http")
async def capture_pageview(request: Request, call_next):
    """A first-party, cookie-less pageview count for the /admin
    dashboard (see models.py's PageView for why this is deliberately
    minimal - no IP, no user-agent, nothing that could re-identify an
    anonymous visitor). Logged after the response so a DB hiccup here
    can never be the reason a real page fails to load.

    Skips logging entirely when the visitor is logged in as the admin
    account - otherwise every time the site owner checks their own
    dashboard or clicks around while logged in, it inflates the very
    traffic numbers that dashboard is supposed to report. This only
    catches logged-in browsing (no way to distinguish the owner from
    a stranger while logged out without IP/fingerprinting, which the
    privacy policy explicitly promises not to do)."""
    response = await call_next(request)
    path = request.url.path
    if (
        request.method == "GET"
        and response.status_code == 200
        and path not in _PAGEVIEW_EXCLUDE_PATHS
        and not path.startswith(_PAGEVIEW_EXCLUDE_PREFIXES)
        and not _is_excluded_viewer(request)
        and db.is_configured()
    ):
        try:
            user = auth.current_user(request)
            if _is_admin(user):
                return response
            with db.get_session() as session:
                session.add(PageView(path=path, user_id=user["id"] if user else None))
                session.commit()
        except Exception:
            pass
    return response


class _CachedStaticFiles(StaticFiles):
    """Static assets with a one-year cache header. Everything here is
    immutable-by-filename in practice: the fonts and images only ever
    change by being replaced with a new file, and the stylesheet is
    inlined into every page so browsers never fetch it by URL."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


app.mount("/static", _CachedStaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

STYLESHEET_PATH = "app/static/css/style.css"
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_css_cache: tuple[float, Markup] | None = None


def inline_css() -> Markup:
    """The whole stylesheet, for embedding in <head> as a <style> block.

    Returns Markup so Jinja doesn't HTML-escape it. Without that the
    quotes in `font-family: 'Inter', ...` become &#39; and every child
    combinator becomes &gt;, which silently drops the whole page back to
    Times New Roman - it renders, so it is easy to miss.

    It used to be a <link>, which PageSpeed flagged as ~290ms of
    render-blocking time: one extra round trip after the HTML before the
    browser could paint anything. Only 13% of these rules are needed
    above the fold, so extracting a "critical" subset was the obvious
    alternative - but a hand-maintained critical extract silently rots
    every time a style changes, and the failure mode is unstyled content
    flashing on a live page. Inlining all of it has no such trap.

    The cost is ~12.7 KiB gzipped on every HTML response, and losing the
    browser cache between page views. That trade favours this site:
    most visits arrive from search, read one report and leave, and even
    a three-page visit saves more in round trips than it spends in
    repeated bytes.

    Comments are stripped from the served copy - this file carries a lot
    of prose worth keeping in the source but not worth shipping. Cached
    against the file's mtime so editing CSS in dev shows up on the next
    request without a restart.
    """
    global _css_cache
    try:
        mtime = os.path.getmtime(STYLESHEET_PATH)
    except OSError:
        return Markup("")
    if _css_cache is not None and _css_cache[0] == mtime:
        return _css_cache[1]

    with open(STYLESHEET_PATH, encoding="utf-8") as fh:
        css = fh.read()
    css = _CSS_COMMENT.sub("", css)
    # Strip the indentation and fold onto one line. Still not a real
    # minifier - nothing here rewrites a declaration, which is the way to
    # break a stylesheet with a regex. It only removes whitespace between
    # lines, which CSS does not care about.
    #
    # The join is a single space, NOT an empty string. A descendant
    # selector split across two lines ("h1,\n  .x" is fine, but
    # ".foo\n  .bar" is one selector) would otherwise be welded into a
    # compound selector and silently mean something else.
    #
    # Worth doing because this sheet is inlined into every response, so
    # its size sits on the critical path for first paint, and its length
    # on the parse time of a cheap phone. Measured: 164,896 -> 150,058
    # bytes raw (9%), 25,883 -> 24,459 gzipped (5.5%), with the brace
    # count and the whitespace-normalised text identical either way.
    css = " ".join(
        line.strip() for line in css.split("\n") if line.strip()
    )

    safe = Markup(css)
    _css_cache = (mtime, safe)
    return safe


templates.env.globals["inline_css"] = inline_css


def _format_gbp(value) -> str:
    try:
        return f"£{int(float(value)):,}"
    except (TypeError, ValueError):
        return str(value)


def _safe_next(next_url: str) -> str:
    # `next` comes from a query/form param an attacker fully controls, and
    # gets used as a post-login redirect target - without this check a
    # crafted link (e.g. "/login?next=https://evil.example") would send a
    # logged-in user off-site straight after they authenticate.
    if not next_url or not next_url.startswith("/") or next_url.startswith("//"):
        return "/"
    return next_url


def _average_amount(transactions: list[dict]) -> float | None:
    amounts = []
    for tx in transactions:
        try:
            amounts.append(float(tx["amount"]))
        except (TypeError, ValueError, KeyError):
            continue
    return sum(amounts) / len(amounts) if amounts else None


def _median(sorted_values: list[float]) -> float | None:
    n = len(sorted_values)
    if not n:
        return None
    mid = n // 2
    return sorted_values[mid] if n % 2 else (sorted_values[mid - 1] + sorted_values[mid]) / 2


def _filter_by_address(records: list[dict], query: str) -> list[dict]:
    if not query:
        return records
    q = query.strip().lower()
    return [r for r in records if q in r["address"].lower()]


def _leading_token(address: str) -> str:
    match = re.match(r"\s*(\w+)", address)
    return match.group(1).lower() if match else ""


def _likely_pre_1970(year_built: str) -> bool | None:
    """Best-effort read of the EPC year_built string (an exact year for
    new-builds, or an RdSAP age-band range/label like "1950-1966" or
    "Before 1900" for existing ones) - checks whether any part of it
    predates 1970, the rough era UK regulations phased out lead water
    supply pipes. Uses the earliest year in a range so a band that
    straddles 1970 (e.g. "1967-1975") still gets flagged, since part
    of it genuinely could be pre-1970. Returns None rather than
    guessing if the string can't be parsed."""
    if not year_built:
        return None
    if year_built == "Before 1900":
        return True
    years = re.findall(r"\d{4}", year_built)
    if not years:
        return None
    return int(years[0]) < 1970


async def _epc_flow(
    canonical: str, house_number: str, configured: bool
) -> tuple[list[dict], dict | None, dict | None]:
    """Certificates + the extra-detail fetch for the first matching
    one, chained together as a single coroutine so the detail call
    (which depends on the search results) runs concurrently with
    everything else in the main gather, instead of strictly after it
    - it was previously awaited as its own serial step once the whole
    gather had already finished, adding a full extra EPC API round
    trip to every page load that had certificates.

    When a house number narrows the search to one specific address
    with more than one certificate on file, also fetches detail for
    all of them (bounded to that one address's own history, typically
    2-4 certificates) to check for a floor-area jump suggesting a
    probable extension - see epc.detect_extension."""
    if not configured:
        return [], None, None
    certs = await epc.certificates_for_postcode(canonical)
    filtered = _filter_by_address(certs, house_number)
    detail = None
    extension_signal = None
    if filtered:
        try:
            detail = await epc.certificate_detail(filtered[0]["certificate_number"])
        except httpx.HTTPError:
            detail = None
        if house_number:
            # The general substring filter above is deliberately loose
            # (good for a human-reviewed table, where "6" matching "16"
            # is a harmless extra row) - but this feeds an automated
            # floor-area comparison, so it needs a stricter same-address
            # match first, or it could silently compare two different
            # properties that happen to share a digit.
            target_token = _leading_token(house_number)
            same_address = [c for c in filtered if _leading_token(c["address"]) == target_token]
            if len(same_address) >= 2:
                details = await asyncio.gather(
                    *(epc.certificate_detail(c["certificate_number"]) for c in same_address),
                    return_exceptions=True,
                )
                history = [
                    {"date": cert["date"], "total_floor_area": d["total_floor_area"]}
                    for cert, d in zip(same_address, details)
                    if not isinstance(d, Exception) and d
                ]
                extension_signal = epc.detect_extension(history)
    return certs, detail, extension_signal


VALUATION_EPC_LOOKUP_CAP = 20  # bounds worst-case added EPC calls regardless of how many recent sales exist
PROPERTY_SEARCH_CACHE_TTL_S = 3600  # how long a full report is reused for repeat views of the same address
NEW_BUILD_STAT_YEARS = 3  # wide enough to catch a development finishing mid-window, unlike the 1-year valuation comp window
NEW_BUILD_STAT_MIN_SAMPLE = 5  # below this, a "new-build share" percentage is noise, not a signal


def _new_build_stat(comparables: list[dict]) -> dict | None:
    """Share of nearby sales in the last NEW_BUILD_STAT_YEARS that were
    new-build, from the `new_build` flag Land Registry already publishes
    on every transaction (distinct from `category`, which is their
    Standard/Additional Price Paid split, not a new-build indicator).
    A rising share nearby is a proxy for active local development -
    useful supply/character context a buyer wouldn't otherwise see
    without reading through the whole sold-price table themselves."""
    cutoff = (datetime.date.today() - datetime.timedelta(days=365 * NEW_BUILD_STAT_YEARS)).isoformat()
    recent = [tx for tx in comparables if (tx.get("date") or "") >= cutoff]
    if len(recent) < NEW_BUILD_STAT_MIN_SAMPLE:
        return None
    new_build_count = sum(1 for tx in recent if tx.get("new_build"))
    return {
        "pct": round(100 * new_build_count / len(recent)),
        "count": new_build_count,
        "total": len(recent),
        "years": NEW_BUILD_STAT_YEARS,
    }


async def _nearby_comparables(lat: float, lon: float) -> list[dict]:
    """Same nearby-postcodes-then-batch-query chain the Comparables tab
    uses, reused here to power the Valuation estimate on the Summary
    tab. Kept as a single coroutine so it can sit alongside everything
    else in the main asyncio.gather despite its two-step dependency.

    Also looks up floor_area (via EPC) for the subset of comparables
    within valuation.RECENT_YEARS, so the estimate can be narrowed to
    similar-sized properties rather than just "sold nearby recently" -
    a flat and a detached house a few doors apart tell you very
    different things about value. This can't use the subject
    property's own floor area to pre-filter (that comes from a
    different concurrent branch of the same gather, not available
    yet here) - the ±5% comparison happens afterwards, once both are
    ready. Bounded to VALUATION_EPC_LOOKUP_CAP nearest recent sales so
    a busy postcode can't balloon this into dozens of extra EPC calls."""
    nearby = await nearby_postcodes(lat, lon)
    distance_by_postcode = {p["postcode"]: p["distance_m"] for p in nearby}
    transactions = await sold_prices_for_postcodes([p["postcode"] for p in nearby])
    for tx in transactions:
        tx["distance_m"] = distance_by_postcode.get(tx["postcode"])

    cutoff = (datetime.date.today() - datetime.timedelta(days=365 * valuation.RECENT_YEARS)).isoformat()
    recent = sorted(
        (tx for tx in transactions if (tx.get("date") or "") >= cutoff),
        key=lambda tx: tx["distance_m"] if tx["distance_m"] is not None else float("inf"),
    )[:VALUATION_EPC_LOOKUP_CAP]
    if not recent:
        return transactions

    postcodes_needed = {tx["postcode"] for tx in recent}
    certs_by_postcode = dict(zip(
        postcodes_needed,
        await asyncio.gather(
            *(epc.certificates_for_postcode(pc) for pc in postcodes_needed), return_exceptions=True
        ),
    ))

    detail_targets = []
    for tx in recent:
        certs = certs_by_postcode.get(tx["postcode"])
        if isinstance(certs, Exception) or not certs:
            continue
        token = _leading_token(tx["address"])
        matches = [c for c in certs if _leading_token(c["address"]) == token]
        if matches:
            detail_targets.append((tx, matches[0]["certificate_number"]))  # certs are newest-first already

    if detail_targets:
        details = await asyncio.gather(
            *(epc.certificate_detail(cert_no) for _, cert_no in detail_targets), return_exceptions=True
        )
        for (tx, _), detail in zip(detail_targets, details):
            if not isinstance(detail, Exception) and detail:
                tx["floor_area"] = detail.get("total_floor_area")

    return transactions


async def _comparison_summary(postcode: str, house_number: str) -> dict:
    """A lighter-weight version of property_search's big gather, for
    the watchlist compare view - fetches only the handful of fields
    shown in the comparison table, for however many properties the
    user selected, rather than the full ~28-way gather per property
    (which would multiply badly across several properties at once).

    Was previously uncached, unlike property_search's own gather -
    every /watchlist page view re-ran this live 6-way gather for
    every single saved item, on every visit, which is what made the
    page slow to load with more than a couple of items saved. These
    are all slow-moving data sources (sold prices, EPC, flood zone,
    crime, deprivation, HPI) that don't meaningfully change within an
    hour, so this now reuses the same cache/TTL pattern as
    property_search's own gather."""
    location = await lookup_postcode(postcode)
    if location is None:
        return {"postcode": postcode, "house_number": house_number, "not_found": True}

    canonical = location["postcode"]
    cache_key = ("comparison_summary", canonical, house_number)
    cached = _cache.get(cache_key, PROPERTY_SEARCH_CACHE_TTL_S)
    if cached is not None:
        return cached

    lat, lon = location["latitude"], location["longitude"]
    codes = location.get("codes", {})
    epc_configured = epc.is_configured()

    tx_result, epc_flow_result, flood_zone_result, crime_result, deprivation_result, hpi_result = (
        await asyncio.gather(
            sold_prices_for_postcode(canonical),
            _epc_flow(canonical, house_number, epc_configured),
            flood_zones.zone_for(lat, lon),
            crime.summary_near(lat, lon),
            asyncio.to_thread(area_stats.deprivation_for_lsoa, codes.get("lsoa", "")),
            hpi.area_comparison(location["admin_district"], location["region"], location.get("country", "")),
            return_exceptions=True,
        )
    )

    summary = {
        "postcode": canonical, "house_number": house_number,
        "admin_district": location["admin_district"], "region": location["region"],
    }

    if not isinstance(tx_result, Exception):
        filtered_tx = _filter_by_address(tx_result, house_number)
        summary["avg_price"] = _average_amount(filtered_tx)
        summary["tx_count"] = len(filtered_tx)

    if not isinstance(epc_flow_result, Exception) and epc_configured:
        _, property_detail, _ = epc_flow_result
        if property_detail:
            summary["dwelling_type"] = property_detail.get("dwelling_type")
            summary["floor_area"] = property_detail.get("total_floor_area")
            summary["year_built"] = property_detail.get("year_built")
            summary["energy_band"] = property_detail.get("current_band")
            summary["heating_cost"] = property_detail.get("heating_cost_current")
            summary["epc_date"] = property_detail.get("inspection_date")

    if not isinstance(flood_zone_result, Exception) and flood_zone_result:
        summary["flood_zone"] = flood_zone_result["label"]

    if not isinstance(crime_result, Exception) and crime_result:
        summary["crime_total"] = crime_result.get("total")
        summary["crime_unpublished"] = bool(crime_result.get("unpublished"))

    if not isinstance(deprivation_result, Exception) and deprivation_result:
        summary["imd_decile"] = deprivation_result.get("imd_decile")

    if not isinstance(hpi_result, Exception) and hpi_result:
        growth_area = hpi_result.get("local_authority") or hpi_result.get("region")
        if growth_area:
            summary["price_growth_pct"] = growth_area.get("annual_change_pct")
            summary["price_growth_area"] = growth_area.get("name")

    _cache.set(cache_key, summary)
    return summary


def _snapshot_changes(old: dict, new: dict) -> list[str]:
    """Human-readable differences between two _comparison_summary
    snapshots of the same address, for the watchlist's "what's
    changed since you last looked" - deliberately only flags
    meaningfully-sized moves, not every minor fluctuation."""
    changes = []

    old_price, new_price = old.get("avg_price"), new.get("avg_price")
    if old_price and new_price and old_price != new_price:
        changes.append(f"Average sold price changed from {_format_gbp(old_price)} to {_format_gbp(new_price)}")

    old_zone, new_zone = old.get("flood_zone"), new.get("flood_zone")
    if old_zone and new_zone and old_zone != new_zone:
        changes.append(f"Flood zone changed from {old_zone} to {new_zone}")

    old_crime, new_crime = old.get("crime_total"), new.get("crime_total")
    if old_crime is not None and new_crime is not None and old_crime != new_crime:
        diff = new_crime - old_crime
        if abs(diff) >= 5:
            changes.append(f"Recorded crime nearby {'up' if diff > 0 else 'down'} by {abs(diff)} since last checked")

    old_tx, new_tx = old.get("tx_count"), new.get("tx_count")
    if old_tx is not None and new_tx is not None and new_tx > old_tx:
        added = new_tx - old_tx
        changes.append(f"{added} new sold price{'s' if added != 1 else ''} recorded here since you last looked")

    old_epc, new_epc = old.get("epc_date"), new.get("epc_date")
    if old_epc and new_epc and new_epc > old_epc:
        changes.append("A new energy certificate was lodged, often a sign the property is being prepared for sale")

    old_growth, new_growth = old.get("price_growth_pct"), new.get("price_growth_pct")
    if old_growth is not None and new_growth is not None:
        # A small deadband around zero avoids flagging a "flip" that's really
        # just rounding/measurement noise on a value hovering near 0% YoY -
        # only count it once each side is clearly past the band.
        deadband = 0.3
        was_up, now_up = old_growth >= deadband, new_growth >= deadband
        was_down, now_down = old_growth <= -deadband, new_growth <= -deadband
        if (was_up and now_down) or (was_down and now_up):
            direction = "growth turned negative" if new_growth < 0 else "prices are growing again"
            changes.append(f"Area house-price trend flipped: {direction} ({new_growth:+.1f}% YoY)")

    return changes


def _imd_label(decile: int | None) -> str | None:
    if decile is None:
        return None
    if decile <= 2:
        return "Among the most deprived areas in England"
    if decile <= 4:
        return "More deprived than average"
    if decile <= 6:
        return "Around the national average"
    if decile <= 8:
        return "Less deprived than average"
    return "Among the least deprived areas in England"


def _crime_comparison(local: dict, district: dict) -> list[dict]:
    local_counts = {c["category"]: c["count"] for c in local["by_category"]}
    district_counts = {c["category"]: c["count"] for c in district["by_category"]}
    categories = sorted(
        set(local_counts) | set(district_counts),
        key=lambda cat: -local_counts.get(cat, 0),
    )
    rows = []
    for cat in categories:
        here = local_counts.get(cat, 0)
        area = district_counts.get(cat, 0)
        if area == 0:
            trend = "higher" if here > 0 else "same"
        else:
            ratio = here / area
            trend = "higher" if ratio > 1.15 else ("lower" if ratio < 0.85 else "same")
        rows.append({"category": cat, "here": here, "area": area, "trend": trend})
    return rows


def _price_position(reference_price: float | None, area_average: float | None) -> float | None:
    """Where the reference price sits on a 0-100 bar centred on the
    area average. Not a true percentile (we don't have the full local
    sales distribution) - just a fixed 0.4x-2.2x-of-average window,
    clamped at the edges."""
    if not reference_price or not area_average:
        return None
    ratio = reference_price / area_average
    low, high = 0.4, 2.2
    position = (ratio - low) / (high - low) * 100
    return max(0, min(100, position))


_TREND_CHART_W, _TREND_CHART_H = 640, 220
_TREND_PAD_L, _TREND_PAD_R, _TREND_PAD_T, _TREND_PAD_B = 64, 84, 16, 28


def _price_trend_chart(trend: dict) -> dict:
    """Precompute SVG geometry for the price-trend line chart - point
    scaling/path-building is much cleaner done here in Python than
    inside Jinja, which has no real arithmetic-heavy loop support."""
    series = trend["series"]
    projections = trend["projections"]
    n = len(series)
    max_months_ahead = max(p["months_ahead"] for p in projections)
    total_span = (n - 1) + max_months_ahead

    values = [p["average_price"] for p in series] + [p["price"] for p in projections]
    min_val, max_val = min(values), max(values)
    val_pad = (max_val - min_val) * 0.08 or max_val * 0.05
    min_val, max_val = min_val - val_pad, max_val + val_pad

    plot_w = _TREND_CHART_W - _TREND_PAD_L - _TREND_PAD_R
    plot_h = _TREND_CHART_H - _TREND_PAD_T - _TREND_PAD_B

    def x_for(index: float) -> float:
        return _TREND_PAD_L + (index / total_span) * plot_w

    def y_for(value: float) -> float:
        return _TREND_PAD_T + plot_h - ((value - min_val) / (max_val - min_val)) * plot_h

    actual_pts = [(x_for(i), y_for(p["average_price"])) for i, p in enumerate(series)]
    actual_path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in actual_pts)

    projected_pts = [actual_pts[-1]] + [
        (x_for(n - 1 + p["months_ahead"]), y_for(p["price"])) for p in projections
    ]
    projected_path = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in projected_pts)

    gridlines = [
        {"y": y_for(v), "label": _format_gbp(v)}
        for v in (min_val + val_pad, (min_val + max_val) / 2, max_val - val_pad)
    ]

    x_labels = []
    for i, p in enumerate(series):
        if i % 12 == 0 or i == n - 1:
            x_labels.append({"x": x_for(i), "label": p["period"][:4]})

    end_point = {"x": actual_pts[-1][0], "y": actual_pts[-1][1], "label": _format_gbp(series[-1]["average_price"])}
    projection_points = [
        {
            "x": x_for(n - 1 + p["months_ahead"]),
            "y": y_for(p["price"]),
            "label": f"{_format_gbp(p['price'])} in {p['months_ahead'] // 12}y",
        }
        for p in projections
    ]

    return {
        "width": _TREND_CHART_W,
        "height": _TREND_CHART_H,
        "pad_l": _TREND_PAD_L,
        "pad_r": _TREND_PAD_R,
        "plot_right": _TREND_CHART_W - _TREND_PAD_R,
        "x_axis_y": _TREND_PAD_T + plot_h,
        "actual_path": actual_path,
        "projected_path": projected_path,
        "gridlines": gridlines,
        "x_labels": x_labels,
        "end_point": end_point,
        "projection_points": projection_points,
    }


def _format_distance(value) -> str:
    try:
        m = float(value)
    except (TypeError, ValueError):
        return str(value)
    miles = m / 1609.344
    if miles < 0.1:
        yards = m / 0.9144
        return f"{int(round(yards))} yd"
    return f"{miles:.1f} mi"


templates.env.filters["gbp"] = _format_gbp
templates.env.filters["distance"] = _format_distance


if indexnow.key():
    @app.get(f"/{indexnow.key()}.txt", include_in_schema=False)
    def indexnow_key_file():
        """IndexNow key verification file (see services/indexnow.py)."""
        return Response(content=indexnow.key(), media_type="text/plain")


async def _indexnow_ping_if_changed():
    """Submit the whole sitemap to IndexNow once per content change.

    Runs on startup, i.e. once per deploy. The URL list is hashed and
    compared against the persistent cache, so a deploy that doesn't
    change the sitemap (most of them) submits nothing, and the Bing
    family hears about new pages within the deploy that adds them."""
    base = os.environ.get("SITE_URL", "https://ukpropertyinsight.co.uk").rstrip("/")
    urls = [u for u, _ in _sitemap_entries(base)]
    digest = hashlib.sha256("\n".join(sorted(urls)).encode()).hexdigest()
    already = _cache.get_persistent("indexnow_submitted_hash", 10 ** 9)
    if already == digest:
        return
    host = base.split("//", 1)[-1]
    if await indexnow.submit(host, urls):
        _cache.set_persistent("indexnow_submitted_hash", digest)
        logging.getLogger(__name__).info("IndexNow: submitted %d URLs", len(urls))


# The postcodes a launch-day visitor predictably types first: the four
# the hero demo cycles through, plus the report-preview example. Warmed
# at startup so the first wave hits a hot cache instead of racing 38
# upstream APIs. One postcode at a time, ordinary cache path, so a
# deploy costs upstreams no more than five ordinary page views.
_PREWARM_POSTCODES = ["SW1A 1AA", "M1 1AE", "LS1 4DY", "B1 1BD"]


async def _prewarm_reports():
    for pc in _PREWARM_POSTCODES:
        try:
            location = await lookup_postcode(pc)
            if location:
                await _full_property_gather(location, "", premium_unlocked=False)
        except Exception:
            continue


@app.on_event("startup")
async def on_startup():
    db.init_db()
    if IS_PRODUCTION:
        asyncio.create_task(_indexnow_ping_if_changed())
        asyncio.create_task(_prewarm_reports())


def base_context(request: Request) -> dict:
    return {
        "current_user": auth.current_user(request),
        "accounts_configured": db.is_configured(),
        # Set here rather than on the two auth routes so the button
        # survives a re-render after a form error, which is exactly when
        # someone is most likely to reach for it.
        "google_oauth_configured": google_oauth.is_configured(),
        "oauth_providers_configured": [
            p for p in ("google", "facebook", "linkedin")
            if (google_oauth.is_configured() if p == "google" else oauth_providers.is_configured(p))
        ],
        "google_maps_api_key": os.environ.get("GOOGLE_MAPS_API_KEY", ""),
        # Default canonical is the current path with no query string - so a
        # link carrying ?ref=... or ?utm_source=... for tracking doesn't get
        # indexed as a separate page from the clean URL. Routes whose actual
        # content varies by query param (e.g. /property?postcode=...) set
        # their own normalized canonical_url after resolving that param.
        "canonical_url": f"{_public_base_url(request)}{request.url.path}",
    }


@app.exception_handler(StarletteHTTPException)
async def not_found_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return templates.TemplateResponse(
            request, "404.html", base_context(request), status_code=404
        )
    # Any other HTTP exception (405 Method Not Allowed, etc.) - defer to
    # Starlette's own default handling rather than re-raising, which
    # doesn't route back through the middleware chain correctly and
    # crashes to an unhandled 500 instead of the proper status code.
    return await default_http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def server_error_handler(request: Request, exc: Exception):
    # Owner alert: a launch-day burst of 500s must not stay invisible
    # (Render's logs are impractical to watch). Rate-limited to one
    # Telegram message per exception type per 5 minutes so an error
    # storm sends a handful of pings, not thousands. Fire-and-forget:
    # alerting must never delay or break the error page itself.
    if IS_PRODUCTION and telegram.is_configured():
        err_key = ("err_alert", type(exc).__name__)
        if _cache.get(err_key, 300) is None:
            _cache.set(err_key, True)
            asyncio.create_task(telegram.send_message(
                "⚠️ 500 on " + request.url.path + "\n"
                + type(exc).__name__ + ": " + str(exc)[:300]
            ))
    return templates.TemplateResponse(
        request, "500.html", base_context(request), status_code=500
    )


DATA_SOURCE_GROUPS = [
    ("Queried live, per postcode", [
        {"name": "HM Land Registry Price Paid", "powers": "Sold price history, comparables, valuation inputs", "freshness": "Live; the Registry updates monthly", "url": "https://www.gov.uk/government/organisations/land-registry"},
        {"name": "UK House Price Index", "powers": "Area averages, trends and forecasts", "freshness": "Live; published monthly", "url": "https://www.gov.uk/government/collections/uk-house-price-index-reports"},
        {"name": "EPC Register", "powers": "Energy ratings, floor area, extension detection, heating costs", "freshness": "Live; certificates appear as lodged", "url": "https://epc.opendatacommunities.org"},
        {"name": "Environment Agency", "powers": "Flood zones, live flood warnings, surface water risk", "freshness": "Live; warnings update continuously", "url": "https://environment.data.gov.uk"},
        {"name": "Police.uk", "powers": "Recorded crime by category near the address", "freshness": "Live; forces publish monthly, England and Wales", "url": "https://www.police.uk"},
        {"name": "British Geological Survey", "powers": "Radon potential, clay subsidence risk", "freshness": "Live against BGS's current atlases", "url": "https://www.bgs.ac.uk"},
        {"name": "Coal Authority", "powers": "Coal mining reporting areas", "freshness": "Live", "url": "https://www.gov.uk/government/organisations/the-coal-authority"},
        {"name": "planning.data.gov.uk & council GIS", "powers": "Conservation areas, green belt, listed buildings, designations", "freshness": "Live; national planning data platform", "url": "https://www.planning.data.gov.uk"},
        {"name": "Food Standards Agency", "powers": "Food hygiene ratings nearby", "freshness": "Live", "url": "https://www.food.gov.uk"},
        {"name": "OpenStreetMap (Overpass)", "powers": "Shops, GPs, pubs, stations nearby", "freshness": "Live; community-maintained", "url": "https://www.openstreetmap.org"},
        {"name": "postcodes.io (ONS/OS data)", "powers": "Postcode lookup, coordinates, area codes, autocomplete", "freshness": "Live; rebuilt on ONS postcode releases", "url": "https://postcodes.io"},
    ]),
    ("Imported in bulk, refreshed on the publisher's cycle", [
        {"name": "DfE schools & Ofsted", "powers": "Schools nearby, ratings, exam results, school landscape", "freshness": "Imported; refreshed when DfE/Ofsted publish", "url": "https://www.gov.uk/government/organisations/department-for-education"},
        {"name": "Council catchment data", "powers": "Published admission distances and boundary shapes", "freshness": "Imported per council academic year, labelled real vs estimated", "url": "https://www.gov.uk/school-admissions"},
        {"name": "Ofcom Connected Nations", "powers": "Broadband speeds and mobile coverage", "freshness": "Imported; Ofcom publishes twice a year", "url": "https://www.ofcom.org.uk"},
        {"name": "ONS Census 2021 & area statistics", "powers": "Demographics, occupations, qualifications, wellbeing, income, deprivation", "freshness": "Imported; Census 2021 plus current ONS small-area series", "url": "https://www.ons.gov.uk"},
        {"name": "ONS Price Index of Private Rents", "powers": "Typical rents by area", "freshness": "Imported; ONS publishes monthly", "url": "https://www.ons.gov.uk"},
        {"name": "Defra strategic noise mapping", "powers": "Road, rail and air noise levels", "freshness": "Imported; Defra round-based", "url": "https://www.gov.uk/government/organisations/department-for-environment-food-rural-affairs"},
        {"name": "Defra/AURN air quality", "powers": "Pollutants against WHO guidelines", "freshness": "Imported; annual modelled background maps", "url": "https://uk-air.defra.gov.uk"},
        {"name": "EA historic landfill & sewage returns", "powers": "Former landfill sites, storm overflow spill counts", "freshness": "Imported; EA annual returns", "url": "https://environment.data.gov.uk"},
        {"name": "MHCLG, Scottish & Welsh Government council tax levels", "powers": "Band charges for all 350 GB billing authorities", "freshness": "Imported; 2026-27 releases, annual", "url": "https://www.gov.uk/government/collections/council-tax-statistics"},
        {"name": "Bank of England", "powers": "Base rate and its history on the buying guide", "freshness": "Live; updates on MPC decisions", "url": "https://www.bankofengland.co.uk"},
    ]),
]


@app.get("/data")
def data_page(request: Request):
    context = base_context(request)
    context["source_groups"] = DATA_SOURCE_GROUPS
    return templates.TemplateResponse(request, "data_page.html", context)


def _landing_accuracy_counts() -> dict | None:
    """Total/fixed counts for the landing page's accuracy strip.
    Cached, so the homepage never pays a query per view."""
    if not db.is_configured():
        return None
    cached = _cache.get("landing_accuracy_counts", 600)
    if cached is not None:
        return cached
    try:
        with db.get_session() as session:
            total = session.scalar(select(func.count()).select_from(FigureReport)) or 0
            fixed = session.scalar(
                select(func.count()).select_from(FigureReport).where(FigureReport.status == "confirmed")
            ) or 0
        counts = {"total": total, "fixed": fixed}
    except Exception:
        counts = None
    _cache.set("landing_accuracy_counts", counts)
    return counts


# The postcodes the hero strip rotates through. The template renders
# this list, so there is one source of truth and the seeds below can
# never drift out of step with what the page asks for.
STRIP_POSTCODES = ["M1 1AE", "LS1 4DY", "SW1A 1AA", "B1 1BD", "E14 9PR"]


@app.get("/")
def index(request: Request):
    context = base_context(request)
    context["accuracy_counts"] = _landing_accuracy_counts()

    # Hand the hero strip whatever is already cached, so it paints with
    # real figures immediately instead of waiting on round trips.
    # /api/lookup caches its finished payload for an hour and this reads
    # those same entries - cache only, never a fetch. Anything missing is
    # simply absent and the script asks for it, so this can only ever
    # make the page faster.
    context["strip_postcodes"] = STRIP_POSTCODES
    context["strip_seeds"] = [
        payload for payload in (
            _cache.get(("api_lookup", pc), API_LOOKUP_CACHE_TTL_S)
            for pc in STRIP_POSTCODES
        ) if payload
    ]
    return templates.TemplateResponse(request, "index.html", context)


# A starting seed of major UK city/town postcode districts, not an
# exhaustive list (there are ~11,000 outcodes nationally) - /area/{outcode}
# works for any valid one regardless of sitemap inclusion, this just
# gives crawlers a fast, curated starting point. Worth growing over
# time (e.g. from real search/watchlist activity) rather than trying
# to enumerate the whole country in one go.
# Every real UK postcode district, validated against postcodes.io by
# scripts/build_outcode_list.py. Drives the sitemap and the /areas index.
# 2,943 entries; the hand-picked seed list below is kept only as the
# fallback if the file is ever missing.
try:
    with open("app/data/outcodes.json", encoding="utf-8") as _fh:
        ALL_OUTCODES: list[dict] = json.load(_fh)
except OSError:
    ALL_OUTCODES = []

# Membership test for "is this a real postcode district?", used to decide
# whether a page gets its own canonical URL. A set so the check is O(1) on
# a hot path rather than a 2,943-entry scan.
KNOWN_OUTCODES: frozenset[str] = frozenset(o["outcode"] for o in ALL_OUTCODES)

AREA_GUIDE_SEED_OUTCODES = [
    # Every entry here has been verified against postcodes.io's real
    # /outcodes/{outcode} endpoint (see scripts/validate_outcodes.py) -
    # nothing hand-guessed, so the sitemap never links to a broken
    # /area/{outcode} page. London.
    "EC1A", "EC2A", "EC3A", "EC4A", "W1A", "WC1A", "WC2A",
    "SW1A", "SW1P", "SW1V", "SW1W", "SW1X", "SW1Y",
    "SW2", "SW3", "SW4", "SW5", "SW6", "SW7", "SW8", "SW9", "SW10", "SW11", "SW12", "SW13", "SW14", "SW15", "SW16", "SW17", "SW18", "SW19", "SW20",
    "W2", "W3", "W4", "W5", "W6", "W7", "W8", "W9", "W10", "W11", "W12", "W13", "W14",
    "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9", "N10", "N11", "N12", "N13", "N14", "N15", "N16", "N17", "N18", "N19", "N20", "N21", "N22",
    "NW1", "NW2", "NW3", "NW4", "NW5", "NW6", "NW7", "NW8", "NW9", "NW10", "NW11",
    "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10", "E11", "E12", "E13", "E14", "E15", "E16", "E17", "E18", "E20",
    "SE1", "SE2", "SE3", "SE4", "SE5", "SE6", "SE7", "SE8", "SE9", "SE10", "SE11", "SE12", "SE13", "SE14", "SE15", "SE16", "SE17", "SE18", "SE19", "SE20", "SE21", "SE22", "SE23", "SE24", "SE25", "SE26", "SE27", "SE28",
    # Manchester
    "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M11", "M12", "M13", "M14", "M15", "M16", "M17", "M18", "M19", "M20", "M21", "M22", "M23", "M25", "M30", "M40", "M50",
    # Birmingham
    "B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11", "B12", "B13", "B14", "B15", "B16", "B17", "B18", "B19", "B20", "B21", "B23", "B24", "B25", "B26", "B27", "B28", "B29", "B30", "B31", "B32", "B33", "B34", "B35", "B36",
    # Leeds
    "LS1", "LS2", "LS3", "LS4", "LS5", "LS6", "LS7", "LS8", "LS9", "LS10", "LS11", "LS12", "LS13", "LS14", "LS15", "LS16", "LS17",
    # Liverpool
    "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9", "L11", "L12", "L13", "L14", "L15", "L17", "L18", "L19", "L25",
    # Bristol
    "BS1", "BS2", "BS3", "BS4", "BS5", "BS6", "BS7", "BS8", "BS9", "BS10", "BS13", "BS14", "BS15", "BS16",
    # Glasgow
    "G1", "G2", "G3", "G4", "G5", "G11", "G12", "G13", "G14", "G20", "G21", "G31", "G41", "G42", "G43", "G44", "G51", "G52",
    # Edinburgh
    "EH1", "EH2", "EH3", "EH4", "EH5", "EH6", "EH7", "EH8", "EH9", "EH10", "EH11", "EH12", "EH13", "EH14", "EH15", "EH16", "EH17",
    # Cardiff
    "CF10", "CF11", "CF14", "CF15", "CF23", "CF24",
    # Sheffield
    "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11", "S12",
    # Newcastle
    "NE1", "NE2", "NE3", "NE4", "NE5", "NE6", "NE7",
    # Nottingham
    "NG1", "NG2", "NG3", "NG5", "NG7", "NG8", "NG9",
    # Leicester
    "LE1", "LE2", "LE3", "LE4", "LE5",
    # Southampton
    "SO14", "SO15", "SO16", "SO17", "SO18", "SO19",
    # Brighton
    "BN1", "BN2", "BN3",
    # Oxford
    "OX1", "OX2", "OX3", "OX4",
    # Cambridge
    "CB1", "CB2", "CB3", "CB4",
    # Bath
    "BA1", "BA2",
    # York
    "YO1", "YO10", "YO24", "YO26",
    # Reading
    "RG1", "RG2", "RG6",
    # Milton Keynes
    "MK1", "MK2", "MK9",
    # Coventry
    "CV1", "CV2", "CV3", "CV4", "CV5", "CV6",
    # Aberdeen
    "AB10", "AB11", "AB24",
    # Dundee
    "DD1", "DD2",
    # Belfast
    "BT1", "BT9",
    # Portsmouth
    "PO1", "PO5",
    # Plymouth
    "PL1", "PL4",
    # Exeter
    "EX1", "EX4",
    # Derby
    "DE1", "DE22",
    # Norwich
    "NR1", "NR2",
    # Swindon
    "SN1", "SN3",
    # Gloucester
    "GL1", "GL50",
    # Canterbury
    "CT1", "CT2",
    # Medway
    "ME1", "ME4",
]


def _sitemap_entries(base: str) -> list[tuple[str, str]]:
    """(url, priority) for every page the sitemap advertises. Shared by
    the sitemap route and the IndexNow pinger so they can never drift.

    Submits the 367 curated districts, not all 2,943. Search Console on
    26 Aug 2026 reported 21 pages indexed against 2,956 submitted, with
    105 "Crawled - currently not indexed": asking a domain this new to
    take the whole country at once spends its crawl on the long tail
    before the cities anyone searches for. The rest stay live, linked
    from /areas and fully crawlable - they are just not queue-jumped to
    the front. Grow this list as districts earn traffic.
    """
    static_paths = ["/", "/areas", "/methodology", "/premium", "/schools/guide", "/privacy", "/terms",
                    "/support", "/market-report", "/buying-guide", "/browser-extension", "/embed", "/data",
                    "/compare", "/tools/stamp-duty-calculator", "/tools/mortgage-calculator",
                    "/market/district-prices"]
    outcodes = [o for o in AREA_GUIDE_SEED_OUTCODES if o in KNOWN_OUTCODES] or AREA_GUIDE_SEED_OUTCODES
    entries = [(f"{base}{p}", "0.8" if p in ("/", "/areas") else "0.5") for p in static_paths]
    entries += [(f"{base}/area/{o}", "0.7") for o in outcodes]
    # The per-district school guides, now that each one canonicals to
    # itself and is allowed to rank. These are the deepest pages on the
    # site (30,000-40,000 words of Ofsted and catchment detail against
    # ~600 for an area guide), so they go in at the area guides' priority.
    entries += [(f"{base}/schools/guide?q={o}", "0.7") for o in outcodes]
    # One page per school with a real published admission distance,
    # limited to the same curated districts as everything else above.
    # Each is genuinely distinct (its own school, its own distance, its
    # own results), which is what the area guides were not, but the
    # crawl-budget argument still applies: there are ~3,200 of these and
    # a new domain should not be handed all of them at once. The rest
    # stay reachable from their district's guide.
    entries += [
        (f"{base}/school/{s['urn']}/{s['slug']}", "0.6")
        for s in _sitemap_school_pages(set(outcodes))
    ]
    # The "M20 or M21" comparison pages are deliberately NOT submitted
    # yet. They are linked from every area guide and fully crawlable,
    # but there are ~800 of them and this domain is still getting 21
    # pages indexed out of what it already offers. They graduate into
    # the sitemap once the indexed count is climbing, on the same
    # reasoning that keeps 2,576 districts out of it today.
    return entries


def _sitemap_school_pages(outcodes: set[str]) -> list[dict]:
    try:
        return [s for s in schools_db.admission_pages_in_outcodes(outcodes)]
    except Exception:  # noqa: BLE001 - the sitemap must render without the DB
        return []


@app.get("/sitemap.xml")
def sitemap(request: Request):
    base = _public_base_url(request)
    # lastmod is the deploy's own timestamp: a guide's data changes on
    # the cadence of the imports behind it, and every deploy re-reads
    # those, so this is honest without tracking per-page dates.
    lastmod = datetime.date.today().isoformat()
    entries = _sitemap_entries(base)
    # Every URL in the sitemap now carries a query string, and a bare "&"
    # in <loc> is malformed XML that makes Google reject the whole file.
    # One param today, so nothing to escape yet; escaping here means a
    # second one never silently breaks the sitemap.
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{escape(u)}</loc><lastmod>{lastmod}</lastmod><priority>{pr}</priority></url>\n"
                  for u, pr in entries)
        + "</urlset>"
    )
    return Response(content=body, media_type="application/xml")


def _area_index() -> list[tuple[str, list[tuple[str, list[str]]]]]:
    """Every postcode district grouped by region, then council district,
    regions in reading order and districts A-Z. Shared by /areas and the
    school guide's landing state, so the two indexes can never drift."""
    grouped: dict[str, dict[str, list[str]]] = {}
    for o in ALL_OUTCODES:
        region = o.get("region") or o.get("country") or "Other"
        district = o.get("district") or "Other"
        grouped.setdefault(region, {}).setdefault(district, []).append(o["outcode"])
    order = ["London", "South East", "South West", "East of England", "East Midlands",
             "West Midlands", "Yorkshire and The Humber", "North West", "North East",
             "Wales", "Scotland", "Northern Ireland"]
    regions = sorted(grouped, key=lambda r: (order.index(r) if r in order else 99, r))
    return [(r, sorted(grouped[r].items())) for r in regions]


@app.get("/areas")
def areas_index(request: Request):
    """Every area guide, grouped by region then district. Exists so the
    2,943 guides are reachable by a crawler (and a person) through real
    links rather than only via the sitemap - orphaned pages rank poorly
    however good they are."""
    context = base_context(request)
    context["regions"] = _area_index()
    context["total"] = len(ALL_OUTCODES)
    return templates.TemplateResponse(request, "areas.html", context)


@app.get("/robots.txt")
def robots(request: Request):
    body = f"User-agent: *\nAllow: /\nDisallow: /watchlist\nDisallow: /internal/\nSitemap: {_public_base_url(request)}/sitemap.xml\n"
    return Response(content=body, media_type="text/plain")


@app.get("/methodology")
def methodology(request: Request):
    return templates.TemplateResponse(request, "methodology.html", base_context(request))


@app.get("/privacy")
def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", base_context(request))


@app.get("/terms")
def terms(request: Request):
    return templates.TemplateResponse(request, "terms.html", base_context(request))


@app.get("/support")
def support(request: Request):
    return templates.TemplateResponse(request, "support.html", base_context(request))


# Set once the Chrome Web Store listing is approved and live - None shows
# a "pending review" state on /browser-extension instead of a dead link.
EXTENSION_STORE_URL = None


@app.get("/browser-extension")
def browser_extension_page(request: Request):
    context = base_context(request)
    context["store_url"] = EXTENSION_STORE_URL
    return templates.TemplateResponse(request, "browser_extension.html", context)


@app.get("/embed")
def embed_generator(request: Request, postcode: str = "", ref: str = ""):
    """Self-serve badge generator for estate agents/partners - no
    partner-management UI or approval step exists yet, so this is
    intentionally open to anyone: the value is in agents linking back
    to a free report (and, if they use their own ref= code, getting
    attributed via the same cookie capture_referral() sets everywhere
    else), not in gatekeeping who can embed a badge."""
    context = base_context(request)
    postcode = postcode.strip().upper()
    ref = _SAFE_REF_RE.sub("", ref)[:64]
    context["postcode"] = postcode
    context["ref"] = ref
    if postcode:
        base = _public_base_url(request)
        link = f"{base}/property?postcode={quote(postcode)}"
        if ref:
            link += f"&ref={quote(ref)}"
        context["embed_link"] = link
        context["embed_snippet"] = (
            f'<a href="{link}" target="_blank" rel="noopener">'
            f'<img src="{base}/static/badge.svg" alt="View free UK property report on UKPropertyInsight" width="220" height="40"></a>'
        )
    return templates.TemplateResponse(request, "embed.html", context)


MARKET_REPORT_AREAS = [
    "Westminster", "Manchester", "Birmingham", "Leeds", "Liverpool", "Sheffield", "Bristol",
    "Newcastle upon Tyne", "Nottingham", "Leicester", "Brighton and Hove", "Oxford", "Cambridge",
    "Cardiff", "Edinburgh", "Glasgow", "Aberdeen", "Belfast",
]
MARKET_REPORT_CACHE_TTL_S = 86400  # HPI itself only updates monthly - a day's staleness costs nothing real
# Every area here resolves individually (confirmed live), but firing all
# 18 through the shared Land Registry SPARQL endpoint at once (each one
# is itself 3 concurrent sub-queries - 54 total) started silently
# failing most of them, presumably the endpoint rate-limiting a burst
# from one client. A cap of 4 concurrent area_comparison() calls fixed
# it in testing without making the page noticeably slower (still fully
# parallel, just not ALL 18 at literally the same instant).
_MARKET_REPORT_CONCURRENCY = asyncio.Semaphore(4)


async def _market_report_area(name: str) -> dict | None:
    async with _MARKET_REPORT_CONCURRENCY:
        return await hpi.area_comparison(name, "", "")


@app.get("/market-report")
async def market_report(request: Request):
    """A live, always-current "state of the market" page built from
    the same HPI service every property report already uses, rather
    than a one-off hand-written article that goes stale the day it's
    published. Areas that don't resolve are silently dropped, not
    shown as an error - this is presented as a ranked snapshot of
    however many areas resolved, not a promise that all N will."""
    context = base_context(request)

    cached = _cache.get(("market_report",), MARKET_REPORT_CACHE_TTL_S)
    if cached is not None:
        context.update(cached)
        return templates.TemplateResponse(request, "market_report.html", context)

    results = await asyncio.gather(
        *(_market_report_area(name) for name in MARKET_REPORT_AREAS),
        return_exceptions=True,
    )
    areas = [
        r["local_authority"] for r in results
        if not isinstance(r, Exception) and r and r.get("local_authority")
    ]
    areas.sort(key=lambda a: a["annual_change_pct"], reverse=True)

    page_data = {
        "areas": areas,
        "generated_date": datetime.date.today().strftime("%d %B %Y"),
    }
    _cache.set(("market_report",), page_data)
    context.update(page_data)
    return templates.TemplateResponse(request, "market_report.html", context)


def _rounded_polyline(verts: list[tuple[float, float]], radius: float) -> str:
    """SVG path for an axis-aligned polyline with rounded corners.

    Each interior vertex becomes a quadratic curve that enters and
    leaves `radius` away from the corner (clamped to half the shorter
    adjacent segment, so short segments never overshoot)."""
    import math
    if len(verts) < 2:
        return ""
    d = [f"M{verts[0][0]:.1f},{verts[0][1]:.1f}"]
    for i in range(1, len(verts) - 1):
        (px, py), (cx, cy), (nx, ny) = verts[i - 1], verts[i], verts[i + 1]
        in_len = math.hypot(cx - px, cy - py)
        out_len = math.hypot(nx - cx, ny - cy)
        r = min(radius, in_len / 2, out_len / 2)
        if r <= 0.1 or in_len == 0 or out_len == 0:
            d.append(f"L{cx:.1f},{cy:.1f}")
            continue
        ex = cx - (cx - px) / in_len * r
        ey = cy - (cy - py) / in_len * r
        sx = cx + (nx - cx) / out_len * r
        sy = cy + (ny - cy) / out_len * r
        d.append(f"L{ex:.1f},{ey:.1f} Q{cx:.1f},{cy:.1f} {sx:.1f},{sy:.1f}")
    d.append(f"L{verts[-1][0]:.1f},{verts[-1][1]:.1f}")
    return " ".join(d)


def _boe_chart(history: list[dict]) -> dict | None:
    """Geometry for the base-rate step chart on the buying guide.

    Done here rather than in the template because Jinja is a poor place
    for coordinate maths. Returns a viewBox-relative path plus tick
    positions; the template draws it as inline SVG, which scales to any
    width, needs no library, and inherits the site's colours.

    A step chart, not a line: the rate is constant between Monetary
    Policy Committee decisions, and a sloped line between two points
    would invent a gradual change that never happened."""
    if not history or len(history) < 2:
        return None
    W, H = 720.0, 240.0
    L, R, T, B = 44.0, 16.0, 18.0, 30.0   # plot margins: y labels left, x labels below

    def ts(d: str) -> float:
        y, m, dd = (int(x) for x in d.split("-"))
        return y + (m - 1) / 12 + (dd - 1) / 365

    xs = [ts(h["date"]) for h in history]
    today = datetime.date.today()
    x_end = today.year + (today.month - 1) / 12 + (today.day - 1) / 365
    x0, x1 = xs[0], x_end
    ymax = max(6.0, max(h["rate"] for h in history) + 0.5)

    def X(x): return L + (x - x0) / (x1 - x0) * (W - L - R)
    def Y(y): return T + (1 - y / ymax) * (H - T - B)

    # Step path: hold each rate flat until the next change. Corners are
    # rounded with small quadratic curves so the steps read as a drawn
    # line rather than pixel stairs - the radius is clamped to half of
    # each segment, so the rapid-hike section of 2022 (many short
    # steps) rounds gently instead of collapsing into a curve that
    # would misrepresent when the rate actually moved.
    verts: list[tuple[float, float]] = []
    for i, h in enumerate(history):
        x_from = X(xs[i])
        x_to = X(xs[i + 1]) if i + 1 < len(history) else X(x1)
        y = Y(h["rate"])
        if not verts:
            verts.append((x_from, y))
        else:
            verts.append((x_from, y))       # bottom/top of the vertical jump
        verts.append((x_to, y))             # end of the flat hold
    path = _rounded_polyline(verts, radius=5.0)

    # Hit zones for hover, one per holding period.
    steps = []
    for i, h in enumerate(history):
        x_from = X(xs[i])
        x_to = X(xs[i + 1]) if i + 1 < len(history) else X(x1)
        steps.append({
            "x": round(x_from, 1), "w": round(max(x_to - x_from, 2.0), 1),
            "y": round(Y(h["rate"]), 1), "rate": h["rate"], "date": h["date"],
        })

    y_ticks = [{"v": v, "y": round(Y(v), 1)} for v in range(0, int(ymax) + 1)]
    first_year = int(x0) + 1
    x_ticks = [{"label": str(yr), "x": round(X(float(yr)), 1)}
               for yr in range(first_year, today.year + 1)
               if X(float(yr)) > L + 14 and X(float(yr)) < W - R - 14]
    last = history[-1]
    return {
        "w": W, "h": H, "path": path, "steps": steps,
        "y_ticks": y_ticks, "x_ticks": x_ticks,
        "plot": {"l": L, "r": W - R, "t": T, "b": H - B},
        "current": {"x": round(X(x1), 1), "y": round(Y(last["rate"]), 1), "rate": last["rate"]},
    }


@app.get("/buying-guide")
async def buying_guide(request: Request):
    context = base_context(request)
    context["boe"] = await boe_rate.current_rate()
    context["boe_chart"] = _boe_chart(context["boe"]["history"]) if context["boe"] else None
    return templates.TemplateResponse(request, "buying_guide.html", context)


ANON_PAGE_CACHE_TTL_S = 600


def _anon_cacheable(request: Request) -> bool:
    """A report view whose HTML is identical for every viewer: no
    account (nothing personal on the page), no share token, and no
    extra query params (report=thanks etc. change the notices)."""
    if set(request.query_params.keys()) - {"postcode", "house_number"}:
        return False
    return auth.current_user(request) is None


@app.get("/property")
async def property_search(request: Request, postcode: str = "", house_number: str = ""):
    # Launch-day fast path: anonymous views of the same address reuse
    # the finished HTML instead of re-rendering the 2,000-line template
    # (about 0.6s of CPU per view on one worker). Logged-in views are
    # personalised and always render fresh.
    key = ("anon_property_page", *auth.property_key(postcode, house_number))

    cacheable = _anon_cacheable(request)
    if cacheable:
        cached_body = _cache.get(key, ANON_PAGE_CACHE_TTL_S)
        if cached_body is not None:
            return HTMLResponse(cached_body)

    # A cold postcode means 10+ seconds of blank page while 38 sources
    # are queried. Real browsers instead get an instant "building your
    # report" page that polls /api/report-ready and reloads when the
    # gather (kicked off here in the background) has landed in the
    # cache. Crawlers and test clients keep the blocking render so SEO
    # and the test suites see the finished page. Status 202 keeps the
    # interim page out of the pageview count and the page cache.
    if postcode.strip() and not _is_crawler(request.headers.get("user-agent")):
        try:
            building_location = await lookup_postcode(postcode.strip())
        except httpx.HTTPError:
            building_location = None
        if building_location is not None:
            b_canonical = building_location["postcode"]
            b_hn = house_number.strip()
            if _cache.get(("property_search_gather", b_canonical, b_hn), PROPERTY_SEARCH_CACHE_TTL_S) is None:
                _spawn_gather(building_location, b_hn)
                ctx = base_context(request)
                ctx["building_postcode"] = b_canonical
                ctx["building_house_number"] = b_hn
                ctx["build_sources"] = GATHER_SOURCE_ORDER
                return templates.TemplateResponse(request, "report_building.html", ctx, status_code=202)

    response = await _render_property(request, postcode, house_number)
    if cacheable and getattr(response, "status_code", None) == 200 and getattr(response, "body", None):
        _cache.set(key, response.body.decode("utf-8"))
    return response


async def _render_property(request: Request, postcode: str, house_number: str, _share=None):
    postcode = postcode.strip()
    house_number = house_number.strip()
    context = base_context(request)
    context["query"] = postcode
    context["house_number"] = house_number
    # Set when rendering a share link: the full report, read-only, no
    # unlock spent, with a banner saying where it came from.
    context["shared"] = _share

    if not postcode:
        return templates.TemplateResponse(request, "property.html", context)

    try:
        location = await lookup_postcode(postcode)
    except httpx.HTTPError:
        context["error"] = "lookup_error"
        return templates.TemplateResponse(request, "property.html", context)

    if location is None:
        context["error"] = "not_found"
        # A postcode that does not exist is a 404, not a 200 with an
        # apology on it - crawlers and uptime checks read the status.
        return templates.TemplateResponse(request, "property.html", context, status_code=404)

    # Several of this report's sources are England & Wales-only
    # infrastructure (Land Registry, the E&W EPC register, Ofsted, the
    # Environment Agency's flood maps) with no Scottish equivalent
    # wired up - and two of them (flood zone, crime) don't just come
    # back empty, they silently default to a falsely-reassuring reading
    # (Zone 1 "low probability" when no EA polygon covers the point at
    # all; a near-zero crime count from data.police.uk, which only
    # carries British Transport Police records for Scotland). Flagged
    # in the UI rather than left implicit.
    context["is_scotland"] = location.get("country") == "Scotland"

    # Overrides base_context's path-only default: postcode is a query
    # param here, not part of the path, and the content genuinely varies
    # by it - but it varies by the *normalized* postcode (location's, not
    # whatever spacing/case the visitor typed), so "sw1a1aa", "SW1A 1AA"
    # and "SW1A1AA" all canonicalize to the one URL instead of splitting
    # ranking signal across three.
    canonical_qs = f"postcode={quote(location['postcode'])}"
    if house_number:
        canonical_qs += f"&house_number={quote(house_number)}"
    context["canonical_url"] = f"{_public_base_url(request)}/property?{canonical_qs}"

    context["active_tab"] = "summary"
    canonical = location["postcode"]

    # Per-property access. A subscriber sees everything; a free account
    # spends one of its unlocks the first time it opens a given
    # property, and never pays again for that same one. Decided here
    # rather than in base_context because it needs the postcode, and it
    # must run before the gather so the gather knows what to fetch.
    current = context["current_user"]
    premium_unlocked = bool(current and current.get("subscribed")) or _share is not None
    context["spent_unlock_now"] = False
    if current and not premium_unlocked:
        with db.get_session() as unlock_session:
            already = auth.has_unlocked(unlock_session, current["id"], canonical, house_number)
            premium_unlocked = auth.claim_unlock(unlock_session, current["id"], canonical, house_number)
            context["spent_unlock_now"] = premium_unlocked and not already
            state = auth.premium_state(
                unlock_session.get(User, current["id"]), unlock_session
            )
            if not premium_unlocked and not _is_excluded_viewer(request):
                # The paywall moment: an account with no free reports
                # left opened a property it has not unlocked. Stored as
                # a pageview with a synthetic path so the funnel can
                # count it without a new table or any extra identifier.
                unlock_session.add(PageView(path=PAYWALL_PATH, user_id=current["id"]))
                unlock_session.commit()
        context["current_user"] = {**current, **state}

    # The templates must gate on THIS, not on current_user.is_premium:
    # access here is per-property, and is_premium only means "has a
    # subscription". Getting that wrong locks cards on a report the user
    # has just spent one of their free unlocks opening.
    context["premium_unlocked"] = premium_unlocked

    context.update(await _full_property_gather(location, house_number, premium_unlocked))

    if context["current_user"]:
        context["watchlist_item"] = watchlist.get_item(
            context["current_user"]["id"], canonical, house_number
        )
        context["shortlisted_urns"] = {
            item["urn"] for item in school_shortlist.list_items(context["current_user"]["id"])
        }

    if context["accounts_configured"]:
        context["area_reviews"] = reviews.summary_for("property", canonical)
        if context["current_user"]:
            context["my_area_review"] = reviews.user_review(context["current_user"]["id"], "property", canonical)
    else:
        context["area_reviews"] = {"average": None, "count": 0, "reviews": []}

    # Questions to ask before you buy - rules over this report's own
    # findings (see services/solicitor_questions.py). Premium content;
    # the template shows only the trigger list when locked.
    context["buyer_questions"] = solicitor_questions.grouped(solicitor_questions.build(context))
    context["buyer_questions_count"] = sum(len(qs) for _, qs in context["buyer_questions"])

    # JSON-safe school points for the map layers: only the fields the
    # pins need, so no date objects reach | tojson.
    context["map_schools"] = [
        {"name": sch["name"], "group": group_name,
         "latitude": sch.get("latitude"), "longitude": sch.get("longitude"),
         "rating": sch.get("ofsted_rating_label"), "distance_m": sch.get("distance_m")}
        for group_name, group_schools in (context.get("schools") or {}).items()
        for sch in group_schools
        if sch.get("latitude") is not None
    ]

    context["report_outcome"] = request.query_params.get("report", "")
    # Share control: only for a viewer who can see the full report on
    # their own account (a share page never offers to re-share).
    context["share_link"] = None
    if _share is None and premium_unlocked and context["current_user"]:
        with db.get_session() as share_session:
            existing = share_session.scalar(
                select(ShareLink).where(
                    ShareLink.user_id == context["current_user"]["id"],
                    ShareLink.postcode == canonical,
                    ShareLink.house_number == house_number,
                )
            )
            if existing is not None:
                context["share_link"] = f"{_public_base_url(request)}/s/{existing.token}"
    response = templates.TemplateResponse(request, "property.html", context)
    timing = _server_timing_header()
    if timing:
        response.headers["Server-Timing"] = timing
    return response


# Per-service timings from the most recent cold gather, surfaced on the
# property response as a standard Server-Timing header. Browsers show it
# in DevTools' Network panel and it costs nothing; it exists because the
# report took 8-13s cold on Render while every service measured under
# 2s from a dev machine, and there was no other way to see which
# upstream was slow from the hosting network specifically.
_last_gather_timings: dict[str, float] = {}


# Which reader-facing source each gather member belongs to, for the
# "building your report" page. Only the members named here are counted:
# the point is to show real progress against the sources the page
# lists, not to expose every internal call. A member with no entry is
# simply not part of the tally.
# Exactly one gather member stands for each source, chosen because it
# is the call that actually fetches that source's headline figure and
# because it runs on every gather. One member per source rather than
# several deliberately: aggregating "all three flood calls" needed a
# running count, and a source whose members are skipped for a given
# address (several are England-only) then never completed at all.
# A member named here that no longer exists would silently stop its
# source ever ticking, so a test asserts every one of these is still
# wired into the gather.
GATHER_SOURCE_LABELS = {
    "sold-prices-for-postcode": "HM Land Registry",
    # Its own line rather than folded into Land Registry: at 6-7 s cold
    # it is the longest call in the gather, and a bar that hit 100% while
    # this was still running left the reader staring at a full bar.
    "-nearby-comparables": "Nearby sold comparables",
    "catchment-catchments-for": "Council admissions data",
    "-epc-flow": "EPC Register",
    "flood-zones-zone-for": "Environment Agency flood data",
    "crime-summary-near": "Police.uk crime data",
    "schools-db-school-landscape, lat, lon)": "Department for Education & Ofsted",
    "area-stats-deprivation-for-lsoa, codes-get": "ONS demographics",
    "noise-noise-near": "Noise & air quality models",
    "radon-risk-near": "British Geological Survey",
    "coal-mining-check-near": "Coal Authority",
    "historic-landfill-check-near": "Historic landfill records",
    "sewage-discharge-nearby-outfalls": "Sewage discharge records",
    "broadband-coverage-for-postcode, canonical)": "Ofcom broadband & mobile",
    "designations-check-all": "Planning designations",
}
# In the order the building page lists them.
GATHER_SOURCE_ORDER = list(GATHER_SOURCE_LABELS.values())

# The live gather a building page is watching. Keyed by address, holds
# the set of sources finished so far. Bounded and short-lived: an entry
# is created when a gather starts and dropped when the page it feeds
# stops polling (see _progress_prune).
_PROGRESS_TTL_S = 300
# No source has reported in this long: treat the gather as dead and let
# the next poll start a fresh one. Comfortably longer than the slowest
# real member (Overpass amenities, 7-10 s cold).
STALLED_GATHER_S = 30
_gather_progress: dict[tuple[str, str], dict] = {}
_progress_sink: contextvars.ContextVar = contextvars.ContextVar("gather_progress_sink", default=None)


# asyncio keeps only a weak reference to a running task, so a bare
# create_task() can be collected mid-flight and the gather silently
# stops. Holding a reference until it finishes is the documented fix.
_background_tasks: set = set()


def _spawn_gather(location: dict, house_number: str) -> None:
    task = asyncio.create_task(_full_property_gather(location, house_number, premium_unlocked=False))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _progress_prune() -> None:
    cutoff = time.time() - _PROGRESS_TTL_S
    for key in [k for k, v in _gather_progress.items() if v["started"] < cutoff]:
        _gather_progress.pop(key, None)


async def _timed(name: str, coro):
    """Run one gather member and record how long it took. Exceptions are
    returned rather than raised so the surrounding gather's
    return_exceptions=True semantics are unchanged."""
    t0 = time.perf_counter()
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 - mirrors return_exceptions=True
        return exc
    finally:
        _last_gather_timings[name] = time.perf_counter() - t0
        # A source ticks when its representative call comes back. A
        # failure ticks too: the source has been tried, and the card it
        # feeds will say the data was unavailable.
        sink = _progress_sink.get()
        label = GATHER_SOURCE_LABELS.get(name)
        if sink is not None and label is not None:
            sink["touched"] = time.time()
            if label not in sink["done"]:
                sink["done"].append(label)


def _server_timing_header() -> str:
    if not _last_gather_timings:
        return ""
    top = sorted(_last_gather_timings.items(), key=lambda kv: -kv[1])[:12]
    return ", ".join(f"{k};dur={v * 1000:.0f}" for k, v in top)


def _apply_amenities(context: dict, result: dict) -> None:
    """The context keys the amenities cards and modals read, from one
    nearby_amenities_and_station result. Shared by the main gather and
    the follow-up /api/property/amenities fetch."""
    context["amenities"] = result["categories"]
    context["stations"] = result["stations"]
    context["stations_list"] = result["stations_list"]
    context["nearest_transport"] = min(
        result["stations"].values(), key=lambda s: s["distance_m"], default=None
    )


@app.get("/api/property/amenities")
async def property_amenities(request: Request, postcode: str = "", house_number: str = ""):
    """The slow half of the property report, fetched by the page after it
    has rendered (see _full_property_gather). Returns the four amenities
    fragments as rendered HTML so the page swaps them in without a
    second copy of the template logic in JavaScript."""
    postcode = postcode.strip()
    if not postcode:
        return JSONResponse({"error": "postcode_required"}, status_code=400)
    try:
        location = await lookup_postcode(postcode)
    except httpx.HTTPError:
        return JSONResponse({"error": "lookup_error"}, status_code=503)
    if location is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    context: dict = {"amenities_pending": False, "amenities_error": False}
    try:
        result = await amenities.nearby_amenities_and_station(location["latitude"], location["longitude"])
        _apply_amenities(context, result)
    except Exception:  # noqa: BLE001 - the cards show "unavailable", never a broken page
        logging.warning("amenities fetch failed for %s", location["postcode"], exc_info=True)
        context["amenities_error"] = True

    # Same per-property lock decision as property_search, minus spending
    # an unlock: this request only follows a page that already did.
    current = auth.current_user(request)
    premium_unlocked = bool(current and current.get("subscribed"))
    if current and not premium_unlocked and db.is_configured():
        with db.get_session() as session:
            premium_unlocked = auth.has_unlocked(session, current["id"], location["postcode"], house_number.strip())
    lock_label = "Sign up: 1 free full report" if not current else "Upgrade to Premium to unlock"
    lock_redirect = "/signup?next=" + quote("/premium") if not current else "/premium"

    amen = templates.get_template("_amenities.html").module
    ctx = {"amenities": None, "stations": {}, "stations_list": {}, "nearest_transport": None, **context}
    return JSONResponse({
        "essentials_card": str(amen.essentials_card(ctx["amenities"], ctx["amenities_error"], False)),
        "transport_card": str(amen.transport_card(
            ctx["stations"], ctx["nearest_transport"], ctx["amenities_error"], False,
            premium_unlocked, lock_label, lock_redirect,
        )),
        "essentials_body": str(amen.essentials_body(ctx["amenities"], ctx["amenities_error"], False)),
        "transport_body": str(amen.transport_body(ctx["stations"], ctx["stations_list"], ctx["amenities_error"], False)),
        "stations_list": ctx["stations_list"],
        "stations_list": ctx["stations_list"],
        "stations_list": ctx["stations_list"],
    })


_inflight_locks: dict = {}


async def _deduped(cache_key, ttl_s: float, factory):
    """Cache read with stampede protection: when many requests miss the
    same key at once (a launch-day burst on one postcode), only the
    first runs the expensive factory; the rest wait on a per-key lock
    and then read the freshly cached value. Locks are dropped after
    use so the dict cannot grow with the key space."""
    value = _cache.get(cache_key, ttl_s)
    if value is not None:
        return value
    lock = _inflight_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        value = _cache.get(cache_key, ttl_s)
        if value is None:
            value = await factory()
            _cache.set(cache_key, value)
    _inflight_locks.pop(cache_key, None)
    return value


async def _full_property_gather(
    location: dict, house_number: str, premium_unlocked: bool, wait_for_amenities: bool = False
) -> dict:
    """The full ~28-service data gather behind /property and its full
    PDF export (/property/pdf) - every dashboard card's worth of data
    for one address. Deliberately excludes anything user-specific
    (watchlist status, shortlist, area reviews) - those stay in
    property_search itself, since a PDF export has no session-relative
    "your watchlist" concept, only the address's own data.

    Amenities (Overpass) measured 7.5-10 s cold from Render against under
    2 s for everything else, so by default this only takes them from the
    cache: on a miss the page renders with the two amenities cards in a
    "finding what's nearby" state and fetches them afterwards through
    /api/property/amenities. wait_for_amenities=True restores the old
    blocking behaviour for callers that need the full result in one go
    (the PDF export)."""
    context: dict = {"location": location}
    canonical = location["postcode"]
    lat, lon = location["latitude"], location["longitude"]
    codes = location.get("codes", {})

    # Publish live progress for any "building your report" page waiting
    # on this address. The sink rides a ContextVar so the individual
    # _timed members find it without every call site passing it down;
    # asyncio.gather's tasks inherit the context, and they all share
    # this one dict object.
    _progress_prune()
    sink = {
        "done": [],
        "total": len(GATHER_SOURCE_ORDER),
        "started": time.time(),
        "touched": time.time(),
    }
    _gather_progress[(canonical, house_number)] = sink
    _progress_sink.set(sink)
    # Local JSON lookup, England only; None elsewhere and the card says so.
    context["council_tax"] = council_tax.for_district(codes.get("admin_district"), location.get("admin_district"))
    context["epc_configured"] = epc.is_configured()
    context["amenities_pending"] = False

    async def _amenities():
        cached = amenities.cached_nearby(lat, lon)
        if cached is not None or wait_for_amenities:
            return cached if cached is not None else await amenities.nearby_amenities_and_station(lat, lon)
        context["amenities_pending"] = True
        return None

    # Independent external API calls AND our own DB lookups, fetched
    # concurrently rather than one at a time. The DB lookups
    # (schools, deprivation, income, occupation, qualification,
    # broadband) are synchronous SQLAlchemy calls - each one is a
    # separate network round-trip to Neon, so running six of them
    # back-to-back after the external APIs had already finished was
    # adding real, measurable latency. asyncio.to_thread lets them
    # run on worker threads in parallel with everything else instead.
    # The whole batch below is cached together, keyed on the exact
    # postcode + house number searched - every field in it is derived
    # purely from that (no per-user data), and repeat views of the
    # same address are extremely common (someone re-checking a
    # property, or several visitors looking at the same listing) but
    # were re-running all ~28 upstream API/DB calls from scratch every
    # single time, at 8-13s a page load. An hour of staleness is a
    # reasonable trade for that - none of this data moves fast enough
    # for a user to notice.
    gather_cache_key = ("property_search_gather", canonical, house_number)

    async def _run_gather():
        _last_gather_timings.clear()
        gather_results_inner = await asyncio.gather(
            _timed("sold-prices-for-postcode", sold_prices_for_postcode(canonical)),
            _timed("-epc-flow", _epc_flow(canonical, house_number, context["epc_configured"])),
            # Belt and braces over flood.py's own budget: a live report
            # once spent 301 seconds inside this call. No single check
            # is worth holding the whole page for, and the card copes
            # with the data being absent.
            _timed("flood-warnings-near", _bounded(flood.warnings_near(lat, lon), 12.0)),
            _timed("crime-summary-near", crime.summary_near(lat, lon)),
            _timed("crime-summary-for-outcode", crime.summary_for_outcode(location["outcode"])),
            _timed("amenities-nearby-amenities-and-station", _amenities()),
            _timed("hpi-area-comparison", hpi.area_comparison(location["admin_district"], location["region"], location.get("country", ""))),
            _timed("noise-noise-near", noise.noise_near(lat, lon)),
            _timed("schools-db-nearby-schools, lat, lon)", asyncio.to_thread(schools_db.nearby_schools, lat, lon)),
            _timed("area-stats-deprivation-for-lsoa, codes-get", asyncio.to_thread(area_stats.deprivation_for_lsoa, codes.get("lsoa", ""))),
            _timed("area-stats-income-for-msoa, codes-get", asyncio.to_thread(area_stats.income_for_msoa, codes.get("msoa", ""))),
            _timed("census-stats-occupation-for-lsoa, codes-get", asyncio.to_thread(census_stats.occupation_for_lsoa, codes.get("lsoa", ""))),
            _timed("census-stats-qualification-for-lsoa, codes-get", asyncio.to_thread(census_stats.qualification_for_lsoa, codes.get("lsoa", ""))),
            _timed("broadband-coverage-for-postcode, canonical)", asyncio.to_thread(broadband.coverage_for_postcode, canonical)),
            _timed("mobile-coverage-coverage-for-laua, codes-get", asyncio.to_thread(mobile_coverage.coverage_for_laua, codes.get("admin_district", ""))),
            _timed("radon-risk-near", radon.risk_near(lat, lon)),
            _timed("heritage-nearby-listed-buildings", heritage.nearby_listed_buildings(lat, lon)),
            _timed("-nearby-comparables", _nearby_comparables(lat, lon)),
            _timed("demographics-age-profile-for-lsoa, codes-get", asyncio.to_thread(demographics.age_profile_for_lsoa, codes.get("lsoa", ""))),
            _timed("demographics-housing-for-lsoa, codes-get", asyncio.to_thread(demographics.housing_for_lsoa, codes.get("lsoa", ""))),
            _timed("demographics-background-for-lsoa, codes-get", asyncio.to_thread(demographics.background_for_lsoa, codes.get("lsoa", ""))),
            _timed("demographics-wellbeing-for-lsoa, codes-get", asyncio.to_thread(demographics.wellbeing_for_lsoa, codes.get("lsoa", ""))),
            _timed("rental-rental-for-laua, codes-get", asyncio.to_thread(rental.rental_for_laua, codes.get("admin_district", ""))),
            _timed("designations-check-all", designations.check_all(lat, lon)),
            _timed("food-hygiene-nearby-ratings", food_hygiene.nearby_ratings(lat, lon)),
            _timed("flood-zones-zone-for", flood_zones.zone_for(lat, lon)),
            _timed("google-places-nearby-food-ratings", google_places.nearby_food_ratings(lat, lon)),
            _timed("orientation-orientation-for", orientation.orientation_for(lat, lon)),
            _timed("air-quality-for-location, location-get", asyncio.to_thread(air_quality.for_location, location.get("eastings"), location.get("northings"))),
            _timed("historic-landfill-check-near", historic_landfill.check_near(lat, lon)),
            _timed("catchment-catchments-for", catchment.catchments_for(lat, lon)),
            _timed("schools-db-school-landscape, lat, lon)", asyncio.to_thread(schools_db.school_landscape, lat, lon)),
            _timed("hpi-price-trend", hpi.price_trend(location["admin_district"])),
            _timed("clay-risk-risk-near", clay_risk.risk_near(lat, lon)),
            _timed("sewage-discharge-nearby-outfalls", sewage_discharge.nearby_outfalls(lat, lon)),
            _timed("coal-mining-check-near", coal_mining.check_near(lat, lon)),
            _timed("surface-water-risk-risk-for", surface_water_risk.risk_for(lat, lon)),
            _timed("cqc-ratings-nearby-ratings", cqc_ratings.nearby_ratings(lat, lon, canonical)),
            return_exceptions=True,
        )
        return gather_results_inner

    gather_results = await _deduped(gather_cache_key, PROPERTY_SEARCH_CACHE_TTL_S, _run_gather)

    (
        tx_result, epc_flow_result, flood_result, crime_result, district_crime_result,
        amenities_result, hpi_result, noise_result,
        schools_result, deprivation_result, income_result,
        occupation_result, qualification_result, broadband_result, mobile_result,
        radon_result, heritage_result, comparables_result,
        age_profile_result, housing_result, background_result, wellbeing_result, rental_result,
        designations_result, food_hygiene_result, flood_zone_result, google_ratings_result,
        orientation_result, air_quality_result, historic_landfill_result, catchment_result,
        school_landscape_result, price_trend_result, clay_risk_result, sewage_result,
        coal_mining_result, surface_water_result, cqc_result,
    ) = gather_results

    if isinstance(tx_result, Exception):
        context["tx_error"] = True
    else:
        context["avg_price"] = _average_amount(tx_result)
        context["transactions"] = _filter_by_address(tx_result, house_number)
        context["postcode_has_transactions"] = bool(tx_result)

    if context["epc_configured"]:
        if isinstance(epc_flow_result, Exception):
            context["epc_error"] = True
        else:
            epc_result, property_detail, extension_signal = epc_flow_result
            context["certificates"] = _filter_by_address(epc_result, house_number)
            context["postcode_has_certificates"] = bool(epc_result)
            if property_detail:
                context["property_detail"] = property_detail
            if extension_signal:
                context["extension_signal"] = extension_signal

    if isinstance(flood_result, Exception):
        context["flood_error"] = True
    else:
        context["flood_warnings"] = flood_result

    if isinstance(flood_zone_result, Exception) or flood_zone_result is None:
        context["flood_zone_error"] = True
    else:
        context["flood_zone"] = flood_zone_result

    if isinstance(noise_result, Exception):
        context["noise_error"] = True
    elif any(noise_result.get(k) is not None for k in ("road_db", "rail_db", "airport_db")):
        context["noise"] = noise_result

    if isinstance(crime_result, Exception):
        context["crime_error"] = True
    else:
        context["crime"] = crime_result
        if not isinstance(district_crime_result, Exception) and district_crime_result:
            context["district_crime"] = district_crime_result
            if crime_result.get("by_category") or district_crime_result.get("by_category"):
                context["crime_comparison"] = _crime_comparison(crime_result, district_crime_result)

    if isinstance(amenities_result, Exception):
        context["amenities_error"] = True
    elif amenities_result is None:
        # Not fetched in this gather - either a cold run, or a replay of
        # one from the gather cache. The follow-up fetch may have filled
        # the amenities cache since, in which case use it now.
        cached_now = amenities.cached_nearby(lat, lon)
        if cached_now is not None:
            _apply_amenities(context, cached_now)
        else:
            context["amenities_pending"] = True
    else:
        _apply_amenities(context, amenities_result)

    if not isinstance(hpi_result, Exception):
        context["hpi"] = hpi_result
        area = hpi_result.get("local_authority") or hpi_result.get("region")
        if area:
            reference_price = None
            if context.get("transactions"):
                try:
                    reference_price = float(context["transactions"][0]["amount"])
                except (TypeError, ValueError, KeyError, IndexError):
                    reference_price = None
            reference_price = reference_price or context.get("avg_price")
            position = _price_position(reference_price, area["average_price"])
            if position is not None:
                context["price_position"] = position
                context["price_position_reference"] = reference_price
                context["price_position_area"] = area

    if isinstance(price_trend_result, Exception):
        context["price_trend_error"] = True
    elif price_trend_result:
        context["price_trend"] = price_trend_result
        context["price_trend_chart"] = _price_trend_chart(price_trend_result)

    if isinstance(schools_result, Exception):
        context["schools_error"] = True
    else:
        context["schools"] = schools_result
        context["schools_total"] = sum(len(v) for v in schools_result.values())

    if not isinstance(school_landscape_result, Exception) and school_landscape_result:
        context["school_landscape"] = school_landscape_result

    if isinstance(deprivation_result, Exception):
        context["deprivation_error"] = True
    else:
        context["deprivation"] = deprivation_result
        if deprivation_result:
            context["imd_label"] = _imd_label(deprivation_result["imd_decile"])

    if isinstance(income_result, Exception):
        context["household_income_error"] = True
    else:
        context["household_income"] = income_result

    if isinstance(occupation_result, Exception):
        context["occupation_error"] = True
    else:
        context["occupation"] = occupation_result

    if isinstance(qualification_result, Exception):
        context["qualification_error"] = True
    else:
        context["qualification"] = qualification_result

    if isinstance(broadband_result, Exception):
        context["broadband_error"] = True
    else:
        context["broadband"] = broadband_result

    if isinstance(mobile_result, Exception):
        context["mobile_error"] = True
    else:
        context["mobile"] = mobile_result

    if isinstance(radon_result, Exception):
        context["radon_error"] = True
    else:
        context["radon"] = radon_result

    if isinstance(clay_risk_result, Exception):
        context["clay_risk_error"] = True
    elif clay_risk_result:
        context["clay_risk"] = clay_risk_result

    if isinstance(sewage_result, Exception):
        context["sewage_error"] = True
    else:
        context["sewage_outfalls"] = sewage_result

    if isinstance(coal_mining_result, Exception) or coal_mining_result is None:
        context["coal_mining_error"] = True
    else:
        context["coal_mining"] = coal_mining_result

    if isinstance(surface_water_result, Exception) or surface_water_result is None:
        context["surface_water_error"] = True
    else:
        context["surface_water"] = surface_water_result

    if not isinstance(cqc_result, Exception) and cqc_result:
        context["cqc_ratings"] = cqc_result

    if isinstance(heritage_result, Exception):
        context["heritage_error"] = True
    else:
        context["heritage"] = heritage_result

    if isinstance(comparables_result, Exception):
        context["valuation_error"] = True
    else:
        subject_floor_area = (context.get("property_detail") or {}).get("total_floor_area")
        context["valuation_floor_area_known"] = bool(subject_floor_area)
        growth_area = (context.get("hpi") or {}).get("local_authority") or (context.get("hpi") or {}).get("region")
        context["valuation"] = valuation.estimate_value(
            comparables_result, subject_floor_area, growth_area["annual_change_pct"] if growth_area else None
        )
        context["new_build_stat"] = _new_build_stat(comparables_result)
        # For the "Keep exploring" tile at the foot of the report. The
        # Comparables tab is free and lists every one of these sales, so
        # a count and the most recent one give nothing away that the
        # next click wouldn't.
        context["nearby_sales_count"] = len(comparables_result)
        dated = [t for t in comparables_result if t.get("date") and t.get("amount")]
        context["nearby_latest_sale"] = max(dated, key=lambda t: t["date"]) if dated else None

    if isinstance(age_profile_result, Exception):
        context["age_profile_error"] = True
    else:
        context["age_profile"] = age_profile_result

    if isinstance(housing_result, Exception):
        context["housing_error"] = True
    else:
        context["housing"] = housing_result

    if isinstance(background_result, Exception):
        context["background_error"] = True
    else:
        context["background"] = background_result

    if isinstance(wellbeing_result, Exception):
        context["wellbeing_error"] = True
    else:
        context["wellbeing"] = wellbeing_result

    if isinstance(rental_result, Exception):
        context["rental_error"] = True
    else:
        context["rental"] = rental_result

    if isinstance(designations_result, Exception):
        context["designations_error"] = True
    else:
        context["designations"] = designations_result
        # Being in a "built-up area" is completely ordinary for most
        # searches (most UK homes are), unlike the other planning
        # designations here - excluded from the attn-triggering count
        # so the card isn't flagging half of urban England amber.
        context["planning_flags"] = [
            d for k, d in designations_result.items()
            if d["group"] == "planning" and d.get("present") and k != "built_up_area"
        ]
        context["environmental_flags"] = [
            d for d in designations_result.values() if d["group"] == "environmental" and d.get("present")
        ]

    if isinstance(food_hygiene_result, Exception):
        context["food_hygiene_error"] = True
    else:
        context["food_hygiene"] = food_hygiene_result

    context["google_ratings_configured"] = google_places.is_configured()
    context["routing_configured"] = routing.is_configured()
    if isinstance(google_ratings_result, Exception):
        context["google_ratings_error"] = True
    else:
        context["google_ratings"] = google_ratings_result

    if isinstance(orientation_result, Exception):
        context["orientation_error"] = True
    else:
        context["orientation"] = orientation_result

    if isinstance(air_quality_result, Exception):
        context["air_quality_error"] = True
    else:
        context["air_quality"] = air_quality_result

    if isinstance(historic_landfill_result, Exception):
        context["historic_landfill_error"] = True
    else:
        context["historic_landfill"] = historic_landfill_result

    if isinstance(catchment_result, Exception):
        context["catchment_error"] = True
    else:
        context["catchment"] = catchment_result
        if not catchment_result:
            context["catchment_covered_authorities"] = catchment.covered_authorities()

    # MEES compliance + lead-plumbing era, both computed from EPC data
    # already fetched above - no extra API calls needed.
    if context.get("certificates"):
        rating = context["certificates"][0].get("rating", "")
        context["mees_compliant"] = (rating not in ("F", "G")) if rating else None
    if context.get("property_detail", {}).get("year_built"):
        context["lead_plumbing_era"] = _likely_pre_1970(context["property_detail"]["year_built"])

    context["overview"] = overview_score.compute(context, premium_unlocked=premium_unlocked)

    # Catchment polygon shapes are the visual equivalent of the
    # locked "School Catchment Areas" card - stripping them for
    # non-premium users keeps the map consistent with that card
    # rather than drawing the paywalled boundary anyway.
    if not premium_unlocked and context.get("catchment"):
        context["catchment"] = [{**c, "rings": None} for c in context["catchment"]]

    # Same reasoning as the catchment polygons above - the admission-
    # distance circle (real or modelled-estimate) is the map-drawn
    # equivalent of a Premium-gated finding, so the full per-school
    # list (with lat/lon) is omitted entirely (not just hidden by CSS)
    # for non-premium users. Built as its own small plain-dict list
    # (not filtered from all_schools directly) since those entries
    # also carry non-JSON-serializable ORM objects (e.g. "detail")
    # that would break tojson() in the map script. Covers every school
    # with *either* a real published distance or a modelled fallback,
    # so "School Catchment Areas" isn't a dead end everywhere outside
    # the handful of real-polygon councils.
    _distance_schools = []
    if context.get("school_landscape"):
        for s in context["school_landscape"].get("all_schools", []):
            radius_miles, is_real, academic_year, source_authority = None, None, None, None
            if s.get("admission_radius"):
                radius_miles = s["admission_radius"]["last_distance_miles"]
                is_real = True
                academic_year = s["admission_radius"]["academic_year"]
                source_authority = s["admission_radius"]["source_authority"]
            elif s.get("catchment_estimate"):
                radius_miles = s["catchment_estimate"]["radius_miles"]
                is_real = False
            else:
                continue

            property_distance_miles = s["distance_m"] / 1609.34
            _distance_schools.append({
                "name": s["name"], "latitude": s["latitude"], "longitude": s["longitude"],
                "phase_group": s.get("phase_group") or "Other",
                "ofsted_rating": s.get("ofsted_rating"),
                "ofsted_rating_label": s.get("ofsted_rating_label"),
                "radius_miles": radius_miles,
                "is_real": is_real,
                "academic_year": academic_year,
                "source_authority": source_authority,
                "property_distance_miles": round(property_distance_miles, 2),
                "within_catchment": property_distance_miles <= radius_miles,
            })

    context["catchment_distance_schools"] = _distance_schools if premium_unlocked else []
    # Ungated teaser counts for the dashboard card, matching how other
    # Premium cards show a summary number before the paywall.
    context["catchment_distance_count"] = len(_distance_schools)
    context["catchment_distance_any_real"] = any(s["is_real"] for s in _distance_schools)

    # Everything this gather was going to do is done. A source whose
    # members were skipped this run (premium-only paths, cached
    # amenities) would otherwise sit un-ticked forever.
    sink["done"] = list(GATHER_SOURCE_ORDER)
    _progress_sink.set(None)

    # A few bytes for the share card. The gather's own cache entry holds
    # the raw results list rather than this assembled context, and an
    # image request is not allowed to re-run the gather, so the finished
    # figures are published here where the card can reach them.
    _cache.set(("og_payload", canonical, house_number), _og_payload(context))

    return context


# --- Lightweight public JSON API (browser extension) ---

_EXTENSION_CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}


@app.get("/api/lookup")
async def api_lookup(postcode: str = ""):
    """A fast, small subset of property_search's data, for the browser
    extension overlay - deliberately NOT the full ~28-way gather (that's
    fine at one request per page view on our own site, but this can be
    hit from any Rightmove/Zoopla/OnTheMarket listing page, so it only
    fetches the handful of signals the overlay actually shows). No
    auth/cookies read or required - this is public read-only data, same
    as the property page shows to a logged-out visitor.
    """
    postcode = postcode.strip()
    if not postcode:
        return JSONResponse({"error": "postcode_required"}, status_code=400, headers=_EXTENSION_CORS_HEADERS)

    try:
        location = await lookup_postcode(postcode)
    except httpx.HTTPError:
        return JSONResponse({"error": "lookup_failed"}, status_code=502, headers=_EXTENSION_CORS_HEADERS)
    if location is None:
        return JSONResponse({"error": "not_found"}, status_code=404, headers=_EXTENSION_CORS_HEADERS)

    canonical = location["postcode"]

    # One entry for the finished payload. The services underneath each
    # cache already, but the fan-out and the scoring ran on every hit,
    # and the landing page asks for five postcodes per view. An hour
    # matches the other public read-only caches here.
    payload_key = ("api_lookup", canonical)
    cached_payload = _cache.get(payload_key, API_LOOKUP_CACHE_TTL_S)
    if cached_payload is not None:
        return JSONResponse(cached_payload, headers=_EXTENSION_CORS_HEADERS)

    lat, lon = location["latitude"], location["longitude"]

    tx_result, flood_zone_result, crime_result, landscape_result, hpi_result = await asyncio.gather(
        sold_prices_for_postcode(canonical),
        flood_zones.zone_for(lat, lon),
        crime.summary_near(lat, lon),
        asyncio.to_thread(schools_db.school_landscape, lat, lon),
        hpi.area_comparison(location["admin_district"], location["region"], location.get("country", "")),
        return_exceptions=True,
    )

    payload = {
        "postcode": canonical, "admin_district": location["admin_district"], "region": location["region"],
    }
    if not isinstance(tx_result, Exception):
        payload["avg_price"] = _average_amount(tx_result)
    if not isinstance(flood_zone_result, Exception) and flood_zone_result:
        payload["flood_zone"] = flood_zone_result["label"]
    if not isinstance(crime_result, Exception) and crime_result:
        payload["crime_total"] = crime_result.get("total")
    if not isinstance(landscape_result, Exception) and landscape_result:
        payload["schools_good_pct"] = landscape_result.get("good_or_better_pct")
        payload["schools_total"] = landscape_result.get("total_schools")

    mini_context = {
        "hpi": hpi_result if not isinstance(hpi_result, Exception) else None,
        "flood_zone": flood_zone_result if not isinstance(flood_zone_result, Exception) else None,
        "school_landscape": landscape_result if not isinstance(landscape_result, Exception) else None,
    }
    payload["overview"] = overview_score.compute(mini_context, premium_unlocked=False)
    payload["report_url"] = f"/property?postcode={canonical.replace(' ', '+')}"

    _cache.set(payload_key, payload)
    return JSONResponse(payload, headers=_EXTENSION_CORS_HEADERS)


API_LOOKUP_CACHE_TTL_S = 3600
EXTENSION_REPORT_CACHE_TTL_S = 3600
EXTENSION_SCHOOLS_LIMIT = 8
EXTENSION_MARKET_HISTORY_LIMIT = 10
EXTENSION_COMPARABLES_LIMIT = 12
EXTENSION_FREE_ROW_LIMIT = 1  # how many rows of a gated list a free/logged-out user sees, as a teaser
EXTENSION_TOKEN_MAX_AGE_S = 60 * 60 * 24 * 30  # 30 days


def _extension_token_serializer():
    # Separate salt from the session cookie signer (main.py's
    # SessionMiddleware) so a leaked/expired token from one system
    # can't be replayed against the other, even though both derive
    # from the same SESSION_SECRET.
    return URLSafeTimedSerializer(SESSION_SECRET, salt="extension-auth")


def _user_from_extension_token(token: str) -> User | None:
    try:
        data = _extension_token_serializer().loads(token, max_age=EXTENSION_TOKEN_MAX_AGE_S)
    except (BadSignature, SignatureExpired):
        return None
    with db.get_session() as session:
        return session.get(User, data.get("user_id"))


@app.options("/api/extension-login")
async def api_extension_login_options():
    return JSONResponse({}, headers={
        **_EXTENSION_CORS_HEADERS,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    })


@app.options("/api/extension-report")
async def api_extension_report_options():
    # A GET carrying a custom "Authorization" header is a non-simple
    # CORS request, so the browser sends a preflight OPTIONS here
    # first - without this handler it 405s and the real GET never
    # fires, which is exactly the failure mode a plain "Couldn't load"
    # error in the widget would hide (caught this via a real login
    # test, not by reasoning about it in advance).
    return JSONResponse({}, headers={
        **_EXTENSION_CORS_HEADERS,
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization",
    })


@app.options("/api/extension-premium-report")
async def api_extension_premium_report_options():
    return JSONResponse({}, headers={
        **_EXTENSION_CORS_HEADERS,
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Authorization",
    })


@app.post("/api/extension-login")
async def api_extension_login(request: Request):
    """Issues a long-lived signed token (not a session cookie - a
    content script's fetch calls aren't reliably credentialed
    cross-site) for the extension to send back as
    "Authorization: Bearer <token>" on /api/extension-report, so a
    Premium user can unlock the same gated detail in the extension
    that they'd see logged in on the main site."""
    if not db.is_configured():
        return JSONResponse({"error": "not_configured"}, status_code=503, headers=_EXTENSION_CORS_HEADERS)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return JSONResponse({"error": "invalid_request"}, status_code=400, headers=_EXTENSION_CORS_HEADERS)

    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not email or not password:
        return JSONResponse({"error": "missing_credentials"}, status_code=400, headers=_EXTENSION_CORS_HEADERS)

    with db.get_session() as session:
        user = auth.find_user_by_email(session, email)
        if user is None or not auth.verify_password(password, user.password_hash):
            return JSONResponse({"error": "invalid_credentials"}, status_code=401, headers=_EXTENSION_CORS_HEADERS)
        token = _extension_token_serializer().dumps({"user_id": user.id})
        payload = {"token": token, "email": user.email, "is_premium": user.is_premium}

    return JSONResponse(payload, headers=_EXTENSION_CORS_HEADERS)


_FULL_POSTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}$", re.I)


async def _immediate(value):
    return value


def _usable_location(location: dict | None) -> dict | None:
    """A location is only usable once it has actually geocoded.

    postcodes.io returns terminated postcodes with null latitude and
    longitude, and one of those reaching an area guide took the page
    down with "unsupported operand type(s) for -: float and NoneType":
    every distance calculation downstream does arithmetic on those two
    fields. Caught in production on /area/IV51 (28 Aug 2026).
    """
    if not location:
        return None
    if location.get("latitude") is None or location.get("longitude") is None:
        return None
    return location


async def _resolve_extension_location(postcode: str) -> tuple[dict | None, bool]:
    """Resolve a postcode the extension detected on a listing page,
    which is routinely only the outward code (e.g. "BR5") - Rightmove/
    Zoopla/OnTheMarket deliberately never publish a listed property's
    full postcode, to stop buyers bypassing the agent. A full postcode
    geocodes to its exact point as normal; an outward-only one geocodes
    to its district centroid via a real nearby postcode instead, so the
    rest of the pipeline gets the same location dict shape either way.
    The caller gets told which happened so it can avoid presenting
    address-specific data (sold price, EPC) as if it belonged to this
    property, when it actually belongs to a geographic neighbour."""
    if _FULL_POSTCODE_RE.match(postcode):
        return _usable_location(await lookup_postcode(postcode)), False
    centroid = await outcode_centroid(postcode)
    if not centroid:
        return None, True
    # 800 m suits towns; a rural district's centroid is often open
    # country with nothing that close, so widen to postcodes.io's 2 km
    # ceiling before giving up on proximity. 97 of the 2,943 area
    # guides in the sitemap were 404ing on exactly this - Highland,
    # island and Welsh-hill districts.
    for radius_m in (800, 2000):
        nearby = await nearby_postcodes(centroid["latitude"], centroid["longitude"], radius_m=radius_m, limit=1)
        if nearby:
            located = _usable_location(await lookup_postcode(nearby[0]["postcode"]))
            if located:
                return located, True
    # Nothing within 2 km of the centre at all (HS2, Isle of Lewis):
    # take any real postcode in the district. Less central, still the
    # right district, and the area-level data is what this page shows.
    fallback = await any_postcode_in_outcode(postcode)
    if fallback:
        located = _usable_location(await lookup_postcode(fallback))
        if located:
            return located, True

    # Every postcode we can find in this district is terminated, and
    # postcodes.io returns those with null coordinates (IV51, Skye).
    # The district's own centroid still has them, and area-level data
    # is all this page shows anyway. codes stays empty on purpose: we
    # do not know the LSOA, and guessing one would put a real census
    # figure against the wrong neighbourhood.
    return {
        "postcode": postcode.upper(),
        "outcode": postcode.upper(),
        "latitude": centroid["latitude"],
        "longitude": centroid["longitude"],
        "admin_district": centroid.get("admin_district") or "",
        "region": centroid.get("region") or "",
        "country": centroid.get("country") or "",
        "codes": {},
    }, True


async def _comparables_for_extension(lat: float, lon: float) -> list[dict]:
    """Same idea as /property/comparables, trimmed down - no
    percentile/reference-price maths, just a distance-sorted list of
    nearby sold transactions for the Comparables tab."""
    nearby = await nearby_postcodes(lat, lon)
    distance_by_postcode = {p["postcode"]: p["distance_m"] for p in nearby}
    transactions = await sold_prices_for_postcodes([p["postcode"] for p in nearby])
    for tx in transactions:
        tx["distance_m"] = distance_by_postcode.get(tx["postcode"])
    transactions.sort(key=lambda t: (t["distance_m"] is None, t["distance_m"]))
    return transactions


def _gate_extension_list(payload: dict, key: str, premium_unlocked: bool, subkey: str | None = None) -> None:
    """Trims a list in `payload` (in place, on a dict the caller
    already knows is a per-request copy - never the cached original)
    down to a short teaser for a non-Premium caller, recording the
    true full count alongside it so the UI can say "N more - log in
    to see them" rather than just silently showing fewer rows."""
    container = payload[key] if subkey is None else payload[key][subkey]
    full_count = len(container)
    if not premium_unlocked:
        container = container[:EXTENSION_FREE_ROW_LIMIT]
    if subkey is None:
        payload[key] = container
    else:
        payload[key] = dict(payload[key])
        payload[key][subkey] = container
    payload[key + "_full_count"] = full_count


@app.get("/api/extension-report")
async def api_extension_report(request: Request, postcode: str = ""):
    """The full data set behind the browser extension's tabbed overlay
    (Summary/Market History/Comparables/Schools/EPC/Demographics/
    Crime/Maps) - richer than /api/lookup's small summary-card subset,
    but still short of property_search's full ~28-way gather: no
    premium-gated signals like valuation estimate or risk designations.

    Market History, Comparables and Schools are capped to a 1-row
    teaser unless the request carries a valid "Authorization: Bearer
    <token>" for a Premium user (see /api/extension-login) - the same
    "headline free, full list Premium" split those sections use on the
    main site. The underlying (full-depth) data is what's cached, for
    an hour per postcode; the free/Premium split is applied fresh to a
    copy of that cached payload on every request, never baked into the
    cached object itself, so a free lookup can never leak into or
    corrupt what a Premium caller sees for the same postcode (or vice
    versa) via the shared cache.
    """
    postcode = postcode.strip()
    if not postcode:
        return JSONResponse({"error": "postcode_required"}, status_code=400, headers=_EXTENSION_CORS_HEADERS)

    premium_unlocked = False
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        token_user = _user_from_extension_token(auth_header[7:])
        if token_user:
            with db.get_session() as unlock_session:
                premium_unlocked = bool(token_user.is_premium) or auth.claim_unlock(
                    unlock_session, token_user.id, postcode, ""
                )

    try:
        location, area_level = await _resolve_extension_location(postcode)
    except httpx.HTTPError:
        return JSONResponse({"error": "lookup_failed"}, status_code=502, headers=_EXTENSION_CORS_HEADERS)
    if location is None:
        return JSONResponse({"error": "not_found"}, status_code=404, headers=_EXTENSION_CORS_HEADERS)

    canonical = location["postcode"]
    lat, lon = location["latitude"], location["longitude"]
    codes = location.get("codes", {})

    cache_key = ("extension_report", canonical, area_level)
    cached = _cache.get(cache_key, EXTENSION_REPORT_CACHE_TTL_S)
    if cached is not None:
        payload = dict(cached)  # shallow copy - _gate_extension_list must never mutate the cached original
        payload["premium_unlocked"] = premium_unlocked
        _gate_extension_list(payload, "market_history", premium_unlocked)
        _gate_extension_list(payload, "comparables", premium_unlocked, subkey="transactions")
        _gate_extension_list(payload, "schools", premium_unlocked)
        return JSONResponse(payload, headers=_EXTENSION_CORS_HEADERS)

    (
        tx_result, comparables_result, flood_zone_result, crime_result, crime_outcode_result, landscape_result,
        hpi_result, certs_result, deprivation_result, income_result, occupation_result,
    ) = await asyncio.gather(
        # In area-level mode `canonical` is a geographic neighbour's
        # postcode, not this property's - querying its sold prices/EPC
        # would show that neighbour's real records mislabelled as this
        # property's, which is worse than showing nothing, so these two
        # are skipped entirely rather than fetched and discarded.
        _immediate([]) if area_level else sold_prices_for_postcode(canonical),
        _comparables_for_extension(lat, lon),
        flood_zones.zone_for(lat, lon),
        crime.summary_near(lat, lon),
        crime.summary_for_outcode(location["outcode"]),
        asyncio.to_thread(schools_db.school_landscape, lat, lon),
        hpi.area_comparison(location["admin_district"], location["region"], location.get("country", "")),
        _immediate([]) if area_level else epc.certificates_for_postcode(canonical),
        asyncio.to_thread(area_stats.deprivation_for_lsoa, codes.get("lsoa", "")),
        asyncio.to_thread(area_stats.income_for_msoa, codes.get("msoa", "")),
        asyncio.to_thread(census_stats.occupation_for_lsoa, codes.get("lsoa", "")),
        return_exceptions=True,
    )

    def ok(result):
        return result if not isinstance(result, Exception) else None

    tx_error = isinstance(tx_result, Exception)
    tx_result = ok(tx_result) or []
    comparables_result = ok(comparables_result) or []
    landscape_result = ok(landscape_result)
    certs_result = ok(certs_result) or []
    crime_comparison_rows = (
        _crime_comparison(ok(crime_result), ok(crime_outcode_result))
        if ok(crime_result) and ok(crime_outcode_result) else []
    )

    payload = {
        "postcode": canonical,
        "admin_district": location["admin_district"],
        "region": location["region"],
        "latitude": lat,
        "longitude": lon,
        # Always a valid link, area-level or not - /property?postcode=X
        # renders the same area-level report the extension itself is
        # showing, rather than dead-ending at the bare homepage.
        "report_url": f"/property?postcode={canonical.replace(' ', '+')}",
        # Distinguishes "the Land Registry lookup failed" from "it
        # succeeded and genuinely found no sales" - without this the
        # extension can't tell the two apart and always shows the same
        # "no recorded sales" text, which is misleading when it was
        # actually a transient SPARQL error.
        "market_history_error": tx_error,
        # True when the postcode came from an outward-code-only guess
        # (see _resolve_extension_location) rather than an exact match -
        # the extension uses this to label crime/flood/schools/HPI as
        # genuinely area-level info while telling the user to get the
        # house number from the agent for anything address-specific.
        "area_level": area_level,
        "district": postcode.strip().upper() if area_level else None,
    }

    # Everything below is data this endpoint already fetches for its
    # own free-tier cards - feeding it all into the score means a free
    # extension user's score reflects the same signals a free (not
    # logged-in) visitor to the site itself would see. It's still not
    # the SITE'S full score - the ~10 Premium-only risk signals
    # (surface water, noise, radon, planning, etc.) require the
    # heavier gather /api/extension-premium-report does, and only run
    # for a paying, logged-in request - see that endpoint, which
    # recomputes this once its own data lands.
    score_context = {
        "hpi": ok(hpi_result),
        "flood_zone": ok(flood_zone_result),
        "school_landscape": landscape_result,
        "certificates": certs_result,
        "deprivation": ok(deprivation_result),
        "crime_comparison": crime_comparison_rows,
    }
    payload["overview"] = overview_score.compute(score_context, premium_unlocked=False)

    payload["summary"] = {
        "avg_price": _average_amount(tx_result),
        "flood_zone": ok(flood_zone_result)["label"] if ok(flood_zone_result) else None,
        "crime_total": ok(crime_result)["total"] if ok(crime_result) else None,
        "schools_good_pct": landscape_result.get("good_or_better_pct") if landscape_result else None,
        "epc_rating": certs_result[0]["rating"] if certs_result else None,
    }

    payload["market_history"] = [
        {"address": t["address"], "date": t["date"], "amount": t["amount"], "tenure": t.get("tenure")}
        for t in tx_result[:EXTENSION_MARKET_HISTORY_LIMIT]
    ]

    comparable_amounts = sorted(float(t["amount"]) for t in comparables_result if t.get("amount"))
    payload["comparables"] = {
        "count": len(comparables_result),
        "median": _median(comparable_amounts),
        "transactions": [
            {
                "address": t["address"], "postcode": t["postcode"], "date": t["date"], "amount": t["amount"],
                "distance_m": t.get("distance_m"),
            }
            for t in comparables_result[:EXTENSION_COMPARABLES_LIMIT]
        ],
    }

    schools_payload = []
    if landscape_result:
        for s in sorted(landscape_result.get("all_schools", []), key=lambda s: s["distance_m"])[:EXTENSION_SCHOOLS_LIMIT]:
            schools_payload.append({
                "name": s["name"],
                "distance_m": s["distance_m"],
                "phase": s.get("phase_group"),
                "ofsted_rating_label": s.get("ofsted_rating_label"),
            })
    payload["schools"] = schools_payload

    payload["epc"] = (
        {"rating": certs_result[0]["rating"], "date": certs_result[0]["date"]} if certs_result else None
    )

    deprivation = ok(deprivation_result)
    income = ok(income_result)
    occupation = ok(occupation_result)
    payload["demographics"] = {
        "imd_decile": deprivation["imd_decile"] if deprivation else None,
        "imd_label": _imd_label(deprivation["imd_decile"]) if deprivation else None,
        "household_income": income["here"] if income else None,
        "professional_pct": occupation["professional_pct"] if occupation else None,
    }

    payload["crime"] = {
        "total": ok(crime_result)["total"] if ok(crime_result) else None,
        "month": ok(crime_result)["month"] if ok(crime_result) else None,
        "by_category": ok(crime_result)["by_category"] if ok(crime_result) else [],
        "outcode": location.get("outcode"),
        "district_total": ok(crime_outcode_result)["total"] if ok(crime_outcode_result) else None,
        # Same category-by-category "here vs the wider postcode area"
        # comparison the main site's own Crime modal shows - reuses
        # that exact function rather than a simplified copy, so the
        # extension's numbers can never quietly drift from the site's.
        "comparison": crime_comparison_rows,
    }

    _cache.set(cache_key, payload)

    payload = dict(payload)
    payload["premium_unlocked"] = premium_unlocked
    _gate_extension_list(payload, "market_history", premium_unlocked)
    _gate_extension_list(payload, "comparables", premium_unlocked, subkey="transactions")
    _gate_extension_list(payload, "schools", premium_unlocked)
    return JSONResponse(payload, headers=_EXTENSION_CORS_HEADERS)


EXTENSION_PREMIUM_CACHE_TTL_S = 3600


@app.get("/api/extension-premium-report")
async def api_extension_premium_report(request: Request, postcode: str = ""):
    """The full dashboard-card set for a logged-in Premium extension
    user - same category groupings as the property page's own
    dashboard grid (Value & Market / Property & Condition / Risk &
    Safety / Planning & Heritage / Location & Connectivity / Area &
    Community), built from the same underlying services.

    Deliberately a SEPARATE endpoint from /api/extension-report rather
    than a shared refactor of property_search's own gather: this way a
    bug here can't touch the main property page, and the free/anonymous
    extension view (the one that can be hit from any listing page a
    shopper's browsing) never pays for this much heavier ~20-service
    gather - only an authenticated Premium request does, which is a
    smaller, deliberate action, not something that happens on every
    listing page load.
    """
    postcode = postcode.strip()
    if not postcode:
        return JSONResponse({"error": "postcode_required"}, status_code=400, headers=_EXTENSION_CORS_HEADERS)

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse({"error": "login_required"}, status_code=401, headers=_EXTENSION_CORS_HEADERS)
    token_user = _user_from_extension_token(auth_header[7:])
    if not token_user:
        return JSONResponse({"error": "login_required"}, status_code=401, headers=_EXTENSION_CORS_HEADERS)
    if not token_user.is_premium:
        with db.get_session() as unlock_session:
            if not auth.claim_unlock(unlock_session, token_user.id, postcode, ""):
                return JSONResponse({"error": "premium_required"}, status_code=403,
                                    headers=_EXTENSION_CORS_HEADERS)

    try:
        location, area_level = await _resolve_extension_location(postcode)
    except httpx.HTTPError:
        return JSONResponse({"error": "lookup_failed"}, status_code=502, headers=_EXTENSION_CORS_HEADERS)
    if location is None:
        return JSONResponse({"error": "not_found"}, status_code=404, headers=_EXTENSION_CORS_HEADERS)

    canonical = location["postcode"]
    lat, lon = location["latitude"], location["longitude"]
    codes = location.get("codes", {})
    laua = codes.get("admin_district", "")

    cache_key = ("extension_premium_report", canonical, area_level)
    cached = _cache.get(cache_key, EXTENSION_PREMIUM_CACHE_TTL_S)
    if cached is not None:
        return JSONResponse(cached, headers=_EXTENSION_CORS_HEADERS)

    (
        hpi_area_result, hpi_trend_result, rental_result, orientation_result,
        surface_water_result, sewage_result, noise_result, radon_result, clay_result,
        air_quality_result, landfill_result, coal_result,
        designations_result, heritage_result,
        broadband_result, mobile_result,
        flood_zone_result, landscape_result, certs_result, crime_result, crime_outcode_result, deprivation_result,
        tx_result, comparables_result, nearby_schools_result, catchment_result, amenities_result,
        income_result, occupation_result, qualification_result, age_profile_result, housing_result,
        background_result, wellbeing_result, flood_warnings_result,
    ) = await asyncio.gather(
        hpi.area_comparison(location["admin_district"], location["region"], location.get("country", "")),
        hpi.price_trend(location["admin_district"]),
        asyncio.to_thread(rental.rental_for_laua, laua),
        # Orientation reads THIS building's own footprint - in
        # area-level mode `lat, lon` is a geographic neighbour's, so
        # skip it rather than show a different building's aspect
        # mislabelled as this property's.
        _immediate(None) if area_level else orientation.orientation_for(lat, lon),
        surface_water_risk.risk_for(lat, lon),
        sewage_discharge.nearby_outfalls(lat, lon),
        noise.noise_near(lat, lon),
        radon.risk_near(lat, lon),
        clay_risk.risk_near(lat, lon),
        asyncio.to_thread(air_quality.for_location, location.get("eastings"), location.get("northings")),
        historic_landfill.check_near(lat, lon),
        coal_mining.check_near(lat, lon),
        designations.check_all(lat, lon),
        heritage.nearby_listed_buildings(lat, lon),
        asyncio.to_thread(broadband.coverage_for_postcode, canonical),
        asyncio.to_thread(mobile_coverage.coverage_for_laua, laua),
        # Fetched here (not just in /api/extension-report) so this
        # endpoint can recompute the Overview Score from the SAME full
        # signal set property_search uses for a logged-in user, instead
        # of leaving it at the lighter free-tier score even after a
        # Premium user has paid for the full gather.
        flood_zones.zone_for(lat, lon),
        asyncio.to_thread(schools_db.school_landscape, lat, lon),
        _immediate([]) if area_level else epc.certificates_for_postcode(canonical),
        crime.summary_near(lat, lon),
        crime.summary_for_outcode(location["outcode"]),
        asyncio.to_thread(area_stats.deprivation_for_lsoa, codes.get("lsoa", "")),
        # Everything below powers the 20 dashboard cards this endpoint
        # was still missing (Local Market, Valuation Estimate, Flood
        # Risk, Crime & Safety, Schools Nearby, School Catchment
        # Areas, Nearby Essentials, Getting Around, and the 6 Area &
        # Community census cards) - same services property_search uses
        # for these, all keyed by lat/lon or LSOA/MSOA so none of them
        # need a house number the extension doesn't have.
        _immediate([]) if area_level else sold_prices_for_postcode(canonical),
        _nearby_comparables(lat, lon),
        asyncio.to_thread(schools_db.nearby_schools, lat, lon),
        catchment.catchments_for(lat, lon),
        # lite=True: this endpoint only ever displays 5 of the 12
        # categories nearby_amenities_and_station can fetch (see
        # essentials_detail below) - skipping the other 7 (including
        # the 5km-radius wind turbine search) cuts the Overpass query
        # this extension gather was consistently slowest on, without
        # dropping anything the extension actually shows.
        amenities.nearby_amenities_and_station(lat, lon, lite=True),
        asyncio.to_thread(area_stats.income_for_msoa, codes.get("msoa", "")),
        asyncio.to_thread(census_stats.occupation_for_lsoa, codes.get("lsoa", "")),
        asyncio.to_thread(census_stats.qualification_for_lsoa, codes.get("lsoa", "")),
        asyncio.to_thread(demographics.age_profile_for_lsoa, codes.get("lsoa", "")),
        asyncio.to_thread(demographics.housing_for_lsoa, codes.get("lsoa", "")),
        asyncio.to_thread(demographics.background_for_lsoa, codes.get("lsoa", "")),
        asyncio.to_thread(demographics.wellbeing_for_lsoa, codes.get("lsoa", "")),
        flood.warnings_near(lat, lon),
        return_exceptions=True,
    )

    def ok(result):
        return result if not isinstance(result, Exception) else None

    hpi_area = ok(hpi_area_result)
    prosperity_area = (hpi_area.get("local_authority") or hpi_area.get("region")) if hpi_area else None
    hpi_trend = ok(hpi_trend_result)
    rental_data = ok(rental_result)
    orientation_data = ok(orientation_result)
    surface_water = ok(surface_water_result)
    sewage_outfalls = ok(sewage_result) or []
    noise_data = ok(noise_result)
    radon_data = ok(radon_result)
    clay_data = ok(clay_result)
    aq_data = ok(air_quality_result)
    landfill = ok(landfill_result)
    coal = ok(coal_result)
    designations_data = ok(designations_result) or {}
    listed_buildings = ok(heritage_result) or []
    broadband_data = ok(broadband_result)
    mobile_data = ok(mobile_result)
    transactions = ok(tx_result) or []
    comparables_list = ok(comparables_result) or []
    nearby_schools_grouped = ok(nearby_schools_result) or {}
    nearby_schools_total = sum(len(v) for v in nearby_schools_grouped.values())
    catchment_areas = ok(catchment_result) or []
    amenities_data = ok(amenities_result)
    income_data = ok(income_result)
    occupation_data = ok(occupation_result)
    qualification_data = ok(qualification_result)
    age_profile_data = ok(age_profile_result)
    housing_data = ok(housing_result)
    background_data = ok(background_result)
    wellbeing_data = ok(wellbeing_result)
    certs_list = ok(certs_result) or []
    flood_warnings = ok(flood_warnings_result) or []

    # Same fallback property_search uses when there's no house number
    # to narrow the estimate by floor area - still a genuine area-based
    # valuation, not a placeholder.
    growth_area = (hpi_area.get("local_authority") or hpi_area.get("region")) if hpi_area else None
    valuation_estimate = (
        None if isinstance(comparables_result, Exception)
        else valuation.estimate_value(comparables_list, None, growth_area["annual_change_pct"] if growth_area else None)
    )

    if amenities_data:
        nearest_transport = min(amenities_data["stations"].values(), key=lambda s: s["distance_m"], default=None)
        essentials_count = sum(
            len(amenities_data["categories"].get(cat, []))
            for cat in ("restaurant", "supermarket", "pharmacy", "pub", "hospital")
        )
    else:
        nearest_transport = None
        essentials_count = 0

    # Mirrors property_search's own catchment_distance_schools list -
    # every school with either a real published admission radius or a
    # modelled estimate, so "School Catchment Areas" isn't a dead end
    # outside the handful of councils with real polygons.
    landscape_data = ok(landscape_result)
    catchment_distance_schools = []
    for s in (landscape_data or {}).get("all_schools", []):
        radius_miles, is_real = None, None
        if s.get("admission_radius"):
            radius_miles, is_real = s["admission_radius"]["last_distance_miles"], True
        elif s.get("catchment_estimate"):
            radius_miles, is_real = s["catchment_estimate"]["radius_miles"], False
        else:
            continue
        property_distance_miles = round(s["distance_m"] / 1609.34, 2)
        catchment_distance_schools.append({
            "name": s["name"], "radius_miles": radius_miles, "is_real": is_real,
            "property_distance_miles": property_distance_miles,
        })
    catchment_distance_count = len(catchment_distance_schools)
    catchment_distance_any_real = any(s["is_real"] for s in catchment_distance_schools)

    # Same "built_up_area" exclusion property_search applies (see its
    # own planning_flags comment) - being in a built-up area is
    # completely ordinary for most searches, not a real constraint, so
    # it shouldn't count as a "Check this" here either. Missing this
    # was a real, confirmed discrepancy: the site showed "None found"
    # for S70 1SH's Planning Constraints while this endpoint showed
    # "1 found" for the exact same postcode.
    planning_flags = [
        d for k, d in designations_data.items()
        if d.get("group") == "planning" and d.get("present") and k != "built_up_area"
    ]
    environmental_flags = [d for d in designations_data.values() if d.get("group") == "environmental" and d.get("present")]
    aq_worst = max((p["times_guideline"] for p in aq_data["pollutants"]), default=None) if aq_data and aq_data.get("pollutants") else None
    noise_max = max((noise_data.get("road_db") or 0, noise_data.get("rail_db") or 0, noise_data.get("airport_db") or 0)) if noise_data else None

    # Synchronous (matches property_search's own un-awaited call to
    # this) and, in area-level mode, actively skipped rather than run -
    # reviews are keyed to `canonical`, which in area-level mode is a
    # geographic neighbour's postcode, and unlike census/crime stats a
    # review is one specific person's opinion about one specific house,
    # not a genuine area-level signal to show as if it were about this
    # property.
    area_reviews = None if area_level else reviews.summary_for("property", canonical)

    def card(title, value, status="ok", sub=None, detail=None):
        return {"title": title, "value": value, "status": status, "sub": sub, "detail": detail}

    def table_detail(columns, rows):
        return {"type": "table", "columns": columns, "rows": rows}

    def list_detail(items):
        return {"type": "list", "items": items}

    # Status thresholds mirror property.html's own {% set %}_status blocks
    # exactly, so a card flagged "Check this" here matches what the same
    # signal would show on the main site's dashboard.
    prosperity_status = "muted" if not prosperity_area else ("ok" if prosperity_area["annual_change_pct"] >= 0 else "attn")
    surface_water_status = "muted" if not surface_water else ("attn" if surface_water["label"] == "High risk" else "ok")
    noise_status = "muted" if noise_max is None else ("attn" if noise_max >= 65 else "ok")
    radon_status = "muted" if not radon_data else ("attn" if int(radon_data["class"]) >= 4 else "ok")
    clay_risk_status = "muted" if not clay_data else ("attn" if clay_data["class_2030"] == "Probable" else "ok")
    sewage_status = "muted" if isinstance(sewage_result, Exception) else ("attn" if sewage_outfalls and (sewage_outfalls[0].get("spill_count") or 0) >= 20 else "ok")
    aq_status = "muted" if not aq_data else ("attn" if aq_worst is not None and aq_worst >= 3 else "ok")
    landfill_status = "muted" if isinstance(landfill_result, Exception) else ("attn" if landfill and landfill["status"] != "clear" else "ok")
    # coal_mining.check_near() catches its own HTTP errors and returns
    # None rather than raising, so an isinstance(..., Exception) check
    # alone never catches a failed fetch here - it always falls through
    # to "ok" even when the API call genuinely failed, mislabeling
    # "unknown" as "not at risk". property_search gets this right by
    # also checking the result isn't None; mirrored here.
    coal_mining_status = "muted" if isinstance(coal_result, Exception) or coal is None else ("attn" if coal.get("present") else "ok")
    planning_status = "muted" if isinstance(designations_result, Exception) else ("attn" if planning_flags else "ok")
    environmental_status = "muted" if isinstance(designations_result, Exception) else ("attn" if environmental_flags else "ok")
    broadband_status = "muted" if not broadband_data else ("attn" if broadband_data.get("below_uso_pct") and broadband_data["below_uso_pct"] >= 5 else "ok")
    mobile_status = "muted" if not mobile_data else ("attn" if mobile_data.get("no_4g_outdoor_pct") and mobile_data["no_4g_outdoor_pct"] >= 5 else "ok")
    flood_zone_data = ok(flood_zone_result)
    flood_status = (
        "muted" if (isinstance(flood_zone_result, Exception) and isinstance(flood_warnings_result, Exception))
        else ("attn" if (flood_warnings or (flood_zone_data and flood_zone_data.get("zone", 0) >= 3)) else "ok")
    )
    crime_data = ok(crime_result)
    crime_outcode_data = ok(crime_outcode_result)
    crime_status = "muted" if isinstance(crime_result, Exception) or not (crime_data and crime_data.get("total")) else "ok"
    deprivation_data = ok(deprivation_result)
    deprivation_status = "muted" if not deprivation_data else ("attn" if deprivation_data.get("imd_decile") and deprivation_data["imd_decile"] <= 3 else "ok")
    local_market_status = "muted" if (isinstance(tx_result, Exception) or not transactions) else "ok"
    valuation_status = "muted" if (isinstance(comparables_result, Exception) or not valuation_estimate) else "ok"
    catchment_status = (
        "muted" if isinstance(catchment_result, Exception)
        else ("ok" if (catchment_areas or catchment_distance_count) else "muted")
    )
    schools_catchment_status = "muted" if (isinstance(nearby_schools_result, Exception) or not nearby_schools_total) else "ok"
    essentials_status = "muted" if (isinstance(amenities_result, Exception) or not amenities_data) else "ok"
    transport_status = "muted" if (isinstance(amenities_result, Exception) or not nearest_transport) else "ok"

    # Structured supporting data for card popups - the same tables/
    # lists property.html's own modals show, built entirely from data
    # this endpoint already fetches above (no new service calls beyond
    # flood.warnings_near, added specifically for this). A generic
    # {type: table|list} shape keeps the extension's popup renderer
    # simple: one function handles every card's detail instead of one
    # bespoke renderer per card.
    # Same transaction table Local Market AND Area Prosperity both open
    # on the site (they share modal-sold-price-history there too) -
    # capped since a postcode's full history can run to hundreds of
    # rows and this is a compact popup, not the full report page.
    sold_price_detail = table_detail(
        ["Address", "Date", "Price", "Tenure"],
        [[t["address"], t["date"], _format_gbp(t["amount"]), t.get("tenure") or "—"] for t in transactions[:15]],
    ) if transactions else None
    if sold_price_detail:
        # Same line-chart data the site's own "Sold price history" modal
        # plots (oldest first, unlike the table above which stays
        # newest-first to match the site's transaction list ordering).
        try:
            sold_price_detail["chart"] = sorted(
                [{"date": t["date"], "amount": float(t["amount"])} for t in transactions if t.get("date") and t.get("amount")],
                key=lambda p: p["date"],
            )
        except (TypeError, ValueError):
            pass

    price_trend_detail = table_detail(
        ["", "Price"],
        [
            ["5 years ago", _format_gbp(hpi_trend["start_price"])],
            ["Now", _format_gbp(hpi_trend["current_price"])],
        ] + [
            [f"+{p['months_ahead'] // 12} yr projected", _format_gbp(round(p["price"]))]
            for p in (hpi_trend.get("projections") or [])
        ],
    ) if hpi_trend and hpi_trend.get("start_price") is not None else None

    noise_detail = table_detail(
        ["Source", "Level", "Band"],
        [
            row for row in [
                ["Road", f"{noise_data['road_db']} dB(A)", noise_data.get("road_label") or "—"] if noise_data.get("road_db") is not None else None,
                ["Rail", f"{noise_data['rail_db']} dB(A)", noise_data.get("rail_label") or "—"] if noise_data.get("rail_db") is not None else None,
                ["Aircraft", f"{noise_data['airport_db']} dB(A)", noise_data.get("airport_label") or "—"] if noise_data.get("airport_db") is not None else None,
            ] if row
        ],
    ) if noise_data else None

    sewage_detail = table_detail(
        ["Outfall", "Into", "Spills", "Hours", "Distance"],
        [
            [o["name"], o.get("receiving_water") or "—", o.get("spill_count") if o.get("spill_count") is not None else "—",
             f"{o['duration_hrs']:.1f}" if o.get("duration_hrs") is not None else "—", _format_distance(o.get("distance_m"))]
            for o in sewage_outfalls
        ],
    ) if sewage_outfalls else None

    flood_detail = table_detail(
        ["Area", "Severity", "Raised"],
        [[w["description"], w["severity"], w["date"]] for w in flood_warnings],
    ) if flood_warnings else None

    planning_detail = list_detail([f"{d['label']}: {', '.join(d.get('names') or [])}" for d in planning_flags]) if planning_flags else None
    environmental_detail = list_detail([f"{d['label']}: {', '.join(d.get('names') or [])}" for d in environmental_flags]) if environmental_flags else None

    heritage_detail = table_detail(
        ["Building", "Grade", "Distance"],
        [[b["name"], b.get("grade") or "—", _format_distance(b.get("distance_m"))] for b in listed_buildings],
    ) if listed_buildings else None

    broadband_detail = table_detail(
        ["Tier", "Coverage"],
        [
            ["Gigabit-capable", f"{broadband_data['gigabit_pct']}%"],
            ["Ultrafast (100Mbit/s+)", f"{broadband_data['ultrafast_pct']}%"],
            ["Superfast (30Mbit/s+)", f"{broadband_data['superfast_pct']}%"],
            ["Below Universal Service Obligation", f"{broadband_data['below_uso_pct']}%"],
        ],
    ) if broadband_data else None

    mobile_detail = table_detail(
        ["Signal", "Coverage"],
        [
            ["4G outdoor (all networks)", f"{mobile_data['coverage_4g_outdoor_all_pct']}%"],
            ["4G indoor (all networks)", f"{mobile_data['coverage_4g_indoor_all_pct']}%"],
            ["5G outdoor", f"{mobile_data['coverage_5g_outdoor_pct']}%"],
            ["No 4G outdoor at all", f"{mobile_data['no_4g_outdoor_pct']}%"],
        ],
    ) if mobile_data else None

    income_rows = [["This neighbourhood", _format_gbp(income_data["here"])]] if income_data else []
    if income_data and income_data.get("la_average"):
        income_rows.append([income_data.get("la_name") or "Local authority average", _format_gbp(income_data["la_average"])])
    if income_data and income_data.get("region_average"):
        income_rows.append([income_data.get("region_name") or "Region average", _format_gbp(income_data["region_average"])])
    income_detail = table_detail(["Area", "Household income"], income_rows) if income_rows else None

    deprivation_detail = table_detail(
        ["Domain", "Decile"],
        [[d["label"], f"{d['decile']} of 10"] for d in deprivation_data.get("domains", [])],
    ) if deprivation_data and deprivation_data.get("domains") else None

    def breakdown_detail(data_dict, key):
        rows = (data_dict or {}).get(key) or []
        return table_detail(["Group", "Share"], [[r["label"], f"{r['pct']}%"] for r in rows]) if rows else None

    occupation_detail = breakdown_detail(occupation_data, "breakdown")
    qualification_detail = breakdown_detail(qualification_data, "breakdown")
    age_profile_detail = breakdown_detail(age_profile_data, "breakdown")
    housing_detail = breakdown_detail(housing_data, "tenure_breakdown")
    background_detail = breakdown_detail(background_data, "ethnicity_breakdown")
    wellbeing_detail = breakdown_detail(wellbeing_data, "health_breakdown")

    reviews_detail = (
        list_detail([f"{'★' * round(r['rating'])}{'☆' * (5 - round(r['rating']))} — {r['body']}" for r in area_reviews["reviews"]])
        if area_reviews and area_reviews.get("reviews") else None
    )

    aspect_detail = table_detail(
        ["", ""],
        [
            ["Garden faces", orientation_data["rear_facing"]],
            ["Front faces", orientation_data["front_facing"]],
            ["Nearest road", orientation_data.get("nearest_road") or "—"],
        ],
    ) if orientation_data else None

    essentials_detail = list_detail([
        f"{cat.replace('_', ' ').title()}: " + ", ".join(p["name"] for p in amenities_data["categories"].get(cat, [])[:3])
        for cat in ("restaurant", "supermarket", "pharmacy", "pub", "hospital")
        if amenities_data["categories"].get(cat)
    ]) if amenities_data else None

    all_stations = [s for mode_list in (amenities_data.get("stations_list") or {}).values() for s in mode_list] if amenities_data else []
    all_stations.sort(key=lambda s: s.get("distance_m") or 0)
    stations_detail = table_detail(
        ["Station", "Type", "Distance"],
        [[s["name"], s["type"], _format_distance(s.get("distance_m"))] for s in all_stations[:3]],
    ) if all_stations else None

    catchment_schools_detail = table_detail(
        ["School", "Distance", "Catchment radius", "Source"],
        [
            [s["name"], f"{s['property_distance_miles']} mi", f"{s['radius_miles']} mi", "Published" if s["is_real"] else "Estimated"]
            for s in catchment_distance_schools[:15]
        ],
    ) if catchment_distance_schools else None

    sections = [
        {
            "heading": "Value & Market",
            "cards": [
                # Matches the site's own text exactly: most recent
                # sale amount, not the postcode average (that's the
                # separate "Avg sold price" card on the free Overview
                # tab) - skipped in area-level mode like Market
                # History, since it would otherwise show a geographic
                # neighbour's own last sale as if it were this
                # property's.
                card(
                    "Local Market",
                    "Ask agent for exact address" if area_level else (f"{_format_gbp(transactions[0]['amount'])} last sale" if transactions else "No recorded sales"),
                    "muted" if area_level else local_market_status,
                    detail=None if area_level else sold_price_detail,
                ),
                card(
                    "Valuation Estimate",
                    f"{_format_gbp(valuation_estimate['estimate'])}" if valuation_estimate else "Not enough nearby sales",
                    valuation_status,
                    detail=table_detail(["", ""], [["Low", _format_gbp(valuation_estimate["low"])], ["Estimate", _format_gbp(valuation_estimate["estimate"])], ["High", _format_gbp(valuation_estimate["high"])]]) if valuation_estimate else None,
                ),
                card(
                    "Costs & Affordability",
                    "Stamp duty, mortgage, yield",
                    detail={
                        "type": "calculator",
                        # Same fallback chain the site's own calculator seed
                        # uses: valuation.estimate -> postcode average sold
                        # price -> a flat default.
                        "price": (valuation_estimate["estimate"] if valuation_estimate else None) or _average_amount(transactions) or 300000,
                        "rent": (rental_data or {}).get("price_all") or 0,
                        "country": location.get("country") or "",
                    },
                ),
                card(
                    "Area Prosperity",
                    (f"{prosperity_area['annual_change_pct']:+.1f}% YoY ({prosperity_area['name']})" if prosperity_area else "No data"),
                    prosperity_status,
                    detail=None if area_level else sold_price_detail,
                ),
                card(
                    "Price Trend & Forecast",
                    (f"{hpi_trend['pct_change']:+.1f}% over 5 years" if hpi_trend and hpi_trend.get("pct_change") is not None else "No data"),
                    "ok" if hpi_trend and hpi_trend.get("pct_change") is not None else "muted",
                    detail=price_trend_detail,
                ),
                card("Rental Analysis", (f"£{rental_data['price_all']:,}/month typical" if rental_data else "No data"), "ok" if rental_data else "muted"),
            ],
        },
        {
            "heading": "Property & Condition",
            "cards": [
                card("Energy Efficiency", f"{len(certs_list)} certificate{'s' if len(certs_list) != 1 else ''} found" if certs_list else "No certificates found", "ok" if certs_list else "muted"),
                # Genuinely needs an exact address (compares a single
                # property's own EPC certificates across years) - the
                # extension never has a house number, on a full
                # postcode or an area-level one, so this always shows
                # the site's own "no house number" fallback text.
                card("Extended or Modified", "Search with a house number", "muted"),
                card(
                    "Aspect",
                    "Ask agent for exact address" if area_level else (f"Garden faces {orientation_data['rear_facing']}" if orientation_data else "No data"),
                    "muted" if area_level else "ok",
                    detail=None if area_level else aspect_detail,
                ),
            ],
        },
        {
            "heading": "Risk & Safety",
            "cards": [
                card("Flood Risk", flood_zone_data["label"] if flood_zone_data else "Zone 1 (low probability)", flood_status, detail=flood_detail),
                card(
                    "Crime & Safety",
                    f"{crime_data['total']} crimes recorded" if crime_data and crime_data.get("total") else "No data",
                    crime_status,
                    detail=table_detail(
                        ["Category", "Here", crime_outcode_data and location.get("outcode") or "Area", "Versus area"],
                        [[r["category"].title(), r["here"], r["area"], {"higher": "Higher", "lower": "Lower", "same": "About the same"}[r["trend"]]] for r in _crime_comparison(crime_data, crime_outcode_data)],
                    ) if crime_data and crime_outcode_data else None,
                ),
                card("Surface Water Risk", surface_water["label"] if surface_water else "No data", surface_water_status),
                # Matches property.html's own card text exactly - the
                # nearest outfall's own spill count for its most recent
                # reported year, not a count of how many outfalls are
                # nearby (a genuine mismatch this had before: "3
                # outfalls nearby" vs the site's "53 spills nearby in
                # 2025" for the same postcode - different metrics
                # entirely, not just different wording).
                card(
                    "Sewage Discharge",
                    "Data unavailable" if isinstance(sewage_result, Exception)
                    else (f"{sewage_outfalls[0]['spill_count']} spills nearby in {sewage_outfalls[0]['year']}" if sewage_outfalls else "No outfalls found nearby"),
                    sewage_status,
                    detail=sewage_detail,
                ),
                card("Noise", (noise_data.get("road_label") or "No data") if noise_data else "No data", noise_status, detail=noise_detail),
                card("Radon Gas", radon_data["label"] if radon_data else "No data", radon_status),
                card("Subsidence Risk", (f"{clay_data['label_2030']} by 2030" if clay_data else "No data"), clay_risk_status),
                card("Air Quality", (f"{aq_worst}× WHO guideline at worst" if aq_worst is not None else "No data"), aq_status),
                card("Historic Contamination", ({"on_site": "On a former landfill", "nearby": "Former landfill nearby", "clear": "None nearby"}.get(landfill["status"], "No data") if landfill else "No data"), landfill_status),
                card("Mining Risk", ("In a Coal Mining Reporting Area" if coal and coal.get("present") else ("Not in a reporting area" if coal else "No data")), coal_mining_status),
            ],
        },
        {
            "heading": "Planning & Heritage",
            "cards": [
                card("Planning Constraints", (f"{len(planning_flags)} found" if planning_flags else "None found"), planning_status, detail=planning_detail),
                card("Environmental Designations", (f"{len(environmental_flags)} found" if environmental_flags else "None found"), environmental_status, detail=environmental_detail),
                card("Listed Buildings", f"{len(listed_buildings)} nearby", "ok" if listed_buildings else "muted", detail=heritage_detail),
            ],
        },
        {
            "heading": "Location & Connectivity",
            "cards": [
                card("Schools Nearby", f"{nearby_schools_total} nearby" if nearby_schools_total else "None found nearby", schools_catchment_status),
                card(
                    "School Catchment Areas",
                    (
                        f"In {len(catchment_areas)} catchment{'s' if len(catchment_areas) != 1 else ''}" if catchment_areas
                        else (f"{catchment_distance_count} school{'s' if catchment_distance_count != 1 else ''}, {'real + estimated' if catchment_distance_any_real else 'estimated'}" if catchment_distance_count else "Not covered for this area")
                    ),
                    catchment_status,
                    detail=catchment_schools_detail,
                ),
                card("Nearby Essentials", f"{essentials_count} nearby" if amenities_data else "No data", essentials_status, detail=essentials_detail),
                card(
                    "Getting Around",
                    (
                        f"{amenities_data['stations']['rail']['city_journeys'][0]['minutes']} min train to {amenities_data['stations']['rail']['city_journeys'][0]['city']}"
                        if amenities_data and (amenities_data["stations"].get("rail") or {}).get("city_journeys")
                        else (f"{nearest_transport['name']} · {_format_distance(nearest_transport.get('walking_distance_m') or nearest_transport['distance_m'])}" if nearest_transport else "Nothing nearby")
                    ),
                    transport_status,
                    detail=stations_detail,
                ),
                card("Broadband", broadband_data["label"] if broadband_data else "No data", broadband_status, detail=broadband_detail),
                card("Mobile Signal", (f"{mobile_data['coverage_4g_outdoor_all_pct']}% 4G outdoor" if mobile_data else "No data"), mobile_status, detail=mobile_detail),
            ],
        },
        {
            "heading": "Area & Community",
            "cards": [
                card("Household Income", f"{_format_gbp(income_data['here'])} p/a" if income_data else "No data", "ok" if income_data else "muted", detail=income_detail),
                card("Deprivation", f"Decile {deprivation_data['imd_decile']} of 10" if deprivation_data else "No data", deprivation_status, detail=deprivation_detail),
                card("Occupation", f"{occupation_data['professional_pct']}% managerial/professional" if occupation_data else "No data", "ok" if occupation_data else "muted", detail=occupation_detail),
                card("Qualification", f"{qualification_data['degree_pct']}% degree-educated" if qualification_data else "No data", "ok" if qualification_data else "muted", detail=qualification_detail),
                card("Age Profile", f"{age_profile_data['under_25_pct']}% under 25" if age_profile_data else "No data", "ok" if age_profile_data else "muted", detail=age_profile_detail),
                card("Housing Types & Tenure", f"{housing_data['owned_pct']}% owner-occupied" if housing_data and housing_data.get("owned_pct") is not None else "No data", "ok" if housing_data else "muted", detail=housing_detail),
                card("Ethnicity, Religion & Origin", f"{background_data['born_abroad_pct']}% born outside the UK" if background_data and background_data.get("born_abroad_pct") is not None else "No data", "ok" if background_data else "muted", detail=background_detail),
                card("Health, Relationships & Social Grade", f"{wellbeing_data['good_health_pct']}% good or very good health" if wellbeing_data and wellbeing_data.get("good_health_pct") is not None else "No data", "ok" if wellbeing_data else "muted", detail=wellbeing_detail),
                card(
                    "Resident Reviews",
                    "Not available for this area" if area_level else (f"{area_reviews['average']}/5 from {area_reviews['count']} review{'s' if area_reviews['count'] != 1 else ''}" if area_reviews and area_reviews.get("count") else "No reviews yet"),
                    "muted",
                    detail=None if area_level else reviews_detail,
                ),
            ],
        },
    ]

    # Same full signal set property_search feeds overview_score.compute
    # for a logged-in user (bar extension_signal, which needs an exact
    # address neither this endpoint nor a postcode-only site search
    # has) - a Premium extension user's score now matches what they'd
    # see logging into the site itself for this postcode, not the
    # lighter free-tier score /api/extension-report shows.
    full_context = {
        "hpi": hpi_area,
        "flood_zone": flood_zone_data,
        "surface_water": surface_water,
        "noise": noise_data,
        "radon": radon_data,
        "air_quality": aq_data,
        "historic_landfill": landfill,
        "coal_mining": coal,
        "planning_flags": planning_flags,
        "environmental_flags": environmental_flags,
        "broadband": broadband_data,
        "mobile": mobile_data,
        "deprivation": deprivation_data,
        "school_landscape": landscape_data,
        "certificates": certs_list,
        "crime_comparison": _crime_comparison(crime_data, crime_outcode_data) if crime_data and crime_outcode_data else [],
    }

    payload = {
        "postcode": canonical,
        "sections": sections,
        "area_level": area_level,
        "district": postcode.strip().upper() if area_level else None,
        "overview": overview_score.compute(full_context, premium_unlocked=True),
    }
    _cache.set(cache_key, payload)
    return JSONResponse(payload, headers=_EXTENSION_CORS_HEADERS)


@app.get("/api/commute")
async def api_commute(lat: float, lon: float, postcode: str = ""):
    """Real driving/cycling time from a property's coordinates to a
    user-typed destination postcode, for the "Getting Around" commute
    calculator. Called from the property page's own JS, not the
    browser extension - no CORS headers needed."""
    postcode = postcode.strip()
    if not postcode:
        return JSONResponse({"error": "postcode_required"}, status_code=400)
    if not routing.is_configured():
        return JSONResponse({"error": "not_configured"}, status_code=503)

    try:
        destination = await lookup_postcode(postcode)
    except httpx.HTTPError:
        return JSONResponse({"error": "lookup_failed"}, status_code=502)
    if destination is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    result = await routing.commute_times(lat, lon, destination["latitude"], destination["longitude"])
    if result is None:
        return JSONResponse({"error": "not_configured"}, status_code=503)

    payload = {"destination_postcode": destination["postcode"]}
    for mode in ("driving", "cycling"):
        leg = result[mode]
        payload[mode] = (
            {"distance_m": round(leg["distance_m"]), "duration_min": round(leg["duration_min"])} if leg else None
        )
    return JSONResponse(payload)


@app.get("/property/comparables")
async def property_comparables(request: Request, postcode: str = "", house_number: str = ""):
    postcode = postcode.strip()
    house_number = house_number.strip()
    context = base_context(request)
    context["query"] = postcode
    context["house_number"] = house_number

    if not postcode:
        return RedirectResponse("/", status_code=303)

    try:
        location = await lookup_postcode(postcode)
    except httpx.HTTPError:
        context["error"] = "lookup_error"
        return templates.TemplateResponse(request, "comparables.html", context)

    if location is None:
        context["error"] = "not_found"
        return templates.TemplateResponse(request, "comparables.html", context)

    context["location"] = location
    context["active_tab"] = "comparables"
    canonical = location["postcode"]
    lat, lon = location["latitude"], location["longitude"]

    try:
        nearby = await nearby_postcodes(lat, lon)
        distance_by_postcode = {p["postcode"]: p["distance_m"] for p in nearby}
        coords_by_postcode = {p["postcode"]: (p["latitude"], p["longitude"]) for p in nearby}
        transactions = await sold_prices_for_postcodes([p["postcode"] for p in nearby])

        for tx in transactions:
            tx["distance_m"] = distance_by_postcode.get(tx["postcode"])
            coords = coords_by_postcode.get(tx["postcode"])
            tx["latitude"], tx["longitude"] = coords if coords else (None, None)
        transactions.sort(key=lambda t: (t["distance_m"] is None, t["distance_m"]))

        amounts = sorted(float(t["amount"]) for t in transactions if t.get("amount"))
        context["comparables"] = transactions
        context["comparables_count"] = len(transactions)

        if amounts:
            context["comparables_median"] = _median(amounts)
            context["comparables_min"] = amounts[0]
            context["comparables_max"] = amounts[-1]

            reference_price = None
            subject_sales = [t for t in transactions if t["postcode"] == canonical]
            if house_number:
                subject_sales = [t for t in subject_sales if house_number.lower() in t["address"].lower()]
            if subject_sales:
                try:
                    reference_price = float(subject_sales[0]["amount"])
                except (TypeError, ValueError):
                    reference_price = None
            if reference_price:
                below = sum(1 for a in amounts if a < reference_price)
                context["comparables_reference_price"] = reference_price
                context["comparables_percentile"] = round(below / len(amounts) * 100)
    except Exception:
        context["comparables_error"] = True

    return templates.TemplateResponse(request, "comparables.html", context)


@app.get("/property/pdf")
async def property_pdf(request: Request, postcode: str = "", house_number: str = ""):
    """A full, printable due-diligence document - the thing a buyer can
    actually hand to a solicitor or mortgage broker, unlike a link to a
    live interactive page. Reuses _full_property_gather (the same
    ~28-service dataset /property itself shows) so this report and the
    live page can never drift apart - it's the same data, just laid
    out for paper instead of a browser. A few things are deliberately
    left out because they don't translate to a static document: the
    interactive map, live train times, and a handful of lower-priority
    cards (food hygiene, CQC ratings, Google ratings) that are about
    the neighbourhood rather than the property's own due diligence.

    A Premium feature: gated the same way dashboard-card-locking is
    everywhere else, via premium_unlocked on the current session."""
    postcode = postcode.strip()
    house_number = house_number.strip()
    if not postcode:
        return RedirectResponse("/", status_code=303)

    current_user = auth.current_user(request)
    if not current_user:
        qs = urlencode({"postcode": postcode, "house_number": house_number}) if house_number else urlencode({"postcode": postcode})
        return RedirectResponse(f"/login?next=/property?{qs}", status_code=303)
    if not current_user.get("is_premium"):
        return RedirectResponse(f"/premium?postcode={postcode}", status_code=303)

    try:
        location = await lookup_postcode(postcode)
    except httpx.HTTPError:
        return RedirectResponse(f"/property?postcode={quote(postcode)}", status_code=303)
    if location is None:
        return RedirectResponse("/", status_code=303)

    report = await _full_property_gather(location, house_number, premium_unlocked=True, wait_for_amenities=True)

    report["buyer_questions"] = solicitor_questions.grouped(solicitor_questions.build(report))
    html = templates.get_template("pdf_report_full.html").render({
        **report,
        "house_number": house_number,
        "generated_date": datetime.date.today().strftime("%d %B %Y"),
        "postcode_url": quote(location["postcode"]),
        "house_number_url": quote(house_number),
    })
    pdf_bytes = pdf_export.html_to_pdf(html)
    if pdf_bytes is None:
        return RedirectResponse(f"/property?postcode={quote(postcode)}", status_code=303)

    filename = f"UKPropertyInsight-{location['postcode'].replace(' ', '')}.pdf"
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_OUTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9]{1,2}[A-Z]?$", re.I)
AREA_GUIDE_CACHE_TTL_S = 86400 * 7  # public, crawler-facing. A week, not a day: none of these sources move faster than monthly, there are 2,943 of these pages, and a day's TTL meant a crawler almost always paid the full cold 5s gather. Refreshed ahead of expiry by the prewarm job.
# Bump whenever the cached area-guide payload gains or loses a field.
AREA_GUIDE_PAYLOAD_VERSION = 17
AREA_SALES_RECENT_YEARS = 2
AREA_SALES_SHOWN = 6
AREA_SALES_MIN_FOR_MEDIAN = 5
# Land Registry's residential property types. "other" is its commercial
# and mixed-use bucket and is deliberately absent.
AREA_SALES_HOME_TYPES = {"detached", "semi-detached", "terraced", "flat-maisonette"}
AREA_SALES_TYPE_LABELS = {
    "detached": "detached houses",
    "semi-detached": "semi-detached houses",
    "terraced": "terraced houses",
    "flat-maisonette": "flats and maisonettes",
}


async def _outcode_sales(lat: float, lon: float) -> dict | None:
    """Recent real sales on the streets around a district's centre.

    Reuses the comparables chain (nearby postcodes, then one batched
    Land Registry query) rather than scanning the district: a prefix
    scan over the whole dataset times out on that endpoint, while a
    VALUES clause of exact postcodes returns in well under a second.
    """
    nearby = await nearby_postcodes(lat, lon)
    if not nearby:
        return None
    transactions = await sold_prices_for_postcodes([p["postcode"] for p in nearby])
    if not transactions:
        return None

    # Land Registry's "other" property type is commercial and mixed-use,
    # not housing. Left in, a city-centre district reported a £22.9m
    # office block inside its house-price range and told the reader most
    # local sales were "other properties". This page is about living
    # somewhere, so it counts homes only.
    homes = [t for t in transactions if (t.get("property_type") or "").lower() in AREA_SALES_HOME_TYPES]
    if not homes:
        return None

    cutoff = (datetime.date.today() - datetime.timedelta(days=365 * AREA_SALES_RECENT_YEARS)).isoformat()
    recent = [t for t in homes if (t.get("date") or "") >= cutoff]
    pool = recent or homes

    amounts = sorted(int(t["amount"]) for t in pool if str(t.get("amount", "")).isdigit())
    if not amounts:
        return None

    latest = sorted(pool, key=lambda t: t.get("date") or "", reverse=True)[:AREA_SALES_SHOWN]
    types = collections.Counter(
        AREA_SALES_TYPE_LABELS.get((t["property_type"] or "").lower(), t["property_type"])
        for t in pool if t.get("property_type")
    )
    return {
        "count": len(pool),
        "is_recent": bool(recent),
        "years": AREA_SALES_RECENT_YEARS,
        # A "median" of two sales is a number pretending to be a
        # statistic. Below the threshold the page shows the sales
        # themselves and says there are too few to average, which is
        # the honest version and still tells the reader something real
        # about a district with almost no housing in it.
        "enough_for_median": len(amounts) >= AREA_SALES_MIN_FOR_MEDIAN,
        "median": amounts[len(amounts) // 2],
        "low": amounts[0],
        "high": amounts[-1],
        "commonest_type": types.most_common(1)[0][0] if types else None,
        "latest": [
            {
                "address": t["address"],
                "postcode": t["postcode"],
                "amount": t["amount"],
                "date": t["date"],
                "property_type": t.get("property_type"),
            }
            for t in latest
        ],
    }


# The aggregate fields area_guide.html reads from the schools landscape.
# Everything else in that dict is per-school rows (all_schools, and the
# `schools` lists nested inside by_rating/by_phase) - hundreds of rows
# and over a megabyte for a central London district, cached for a page
# that never reads them.
AREA_GUIDE_LANDSCAPE_FIELDS = (
    "radius_km", "radius_miles", "total_schools", "good_or_better_pct", "special_count",
    "further_education", "higher_education_count",
)


AREA_NAMED_SCHOOLS = 6
_RATING_RANK = {"Outstanding": 0, "Good": 1, "Requires improvement": 2, "Inadequate": 3}


def _named_schools(landscape: dict | None) -> list[dict]:
    """The best-rated schools nearest a district's centre, by name.

    A guide that says "109 schools within 5km" tells a parent nothing
    they can act on, and it is the same sentence on every district in
    the city. Naming them is what someone searching "schools near M14"
    actually wants, and it is text no other district's page can have.

    Kept to a handful of fields: the full landscape carries around 250
    schools with exam histories attached, and caching that whole
    structure once per district is what previously exhausted the
    instance's memory during a crawl.
    """
    if not landscape:
        return []
    # all_schools entries carry no phase of their own; by_phase is where
    # the grouping lives, so the stage comes from there.
    phase_by_urn = {
        s["urn"]: bucket["label"]
        for bucket in landscape.get("by_phase", [])
        for s in bucket.get("schools", [])
    }
    # Primary and secondary first: a nursery's rating rarely decides
    # where someone buys, and an Outstanding one would otherwise take
    # the whole list.
    phase_rank = {"Primary": 0, "Secondary": 0, "Nursery": 1}
    ranked = sorted(
        (s for s in landscape.get("all_schools", []) if s.get("name")),
        key=lambda s: (
            phase_rank.get(phase_by_urn.get(s.get("urn")), 1),
            _RATING_RANK.get(s.get("ofsted_rating_label"), 9),
            s.get("distance_m") or 10 ** 9,
        ),
    )
    picked = ranked[:AREA_NAMED_SCHOOLS]
    # Link through only where the school really has an admission page.
    slugs = schools_db.admission_slugs_by_urn([s.get("urn") for s in picked if s.get("urn")])
    return [
        {
            "name": s["name"],
            "urn": s.get("urn"),
            "slug": slugs.get(s.get("urn")),
            "rating": s.get("ofsted_rating_label"),
            # 1-4, which is what the site's existing .ofsted-N badge
            # colours key off.
            "rating_code": s.get("ofsted_rating"),
            "phase": phase_by_urn.get(s.get("urn")),
            "distance_m": s.get("distance_m"),
        }
        for s in picked
    ]


def _area_guide_extras(context: dict, outcode: str, lat: float, lon: float) -> None:
    """FAQs and neighbouring-district links for an area guide. Every
    answer is the guide's own real data rephrased as a sentence, and a
    question is only asked when the data behind its answer exists."""
    import math

    def _nearest():
        cached = _cache.get(("nearby_outcodes", outcode), 7 * 86400)
        if cached is not None:
            return cached
        scored = []
        for o in ALL_OUTCODES:
            if o["outcode"] == outcode:
                continue
            d = math.hypot((o["lat"] - lat) * 111.0, (o["lon"] - lon) * 68.0)
            scored.append((d, o))
        scored.sort(key=lambda x: x[0])
        result = [{"outcode": o["outcode"], "district": o["district"]} for _, o in scored[:6]]
        _cache.set(("nearby_outcodes", outcode), result)
        return result

    context["nearby_outcodes"] = _nearest()

    faqs = []
    la = (context.get("hpi") or {}).get("local_authority") if isinstance(context.get("hpi"), dict) else None
    if la and la.get("average_price"):
        period = (la.get("period") or "")[:7]
        faqs.append((
            f"What is the average house price in {outcode}?",
            f"The average sold price in {la['name']} is \u00a3{la['average_price']:,.0f}"
            + (f" as of {period}" if period else "")
            + ", according to the UK House Price Index.",
        ))
        if la.get("annual_change_pct") is not None:
            direction = "up" if la["annual_change_pct"] >= 0 else "down"
            faqs.append((
                f"Are house prices rising in {outcode}?",
                f"Prices in {la['name']} are {direction} {abs(la['annual_change_pct']):.1f}% on a year ago "
                "(UK House Price Index).",
            ))
    # The council-wide average above is shared with every district in the
    # same authority; this one is specific to these streets, so it is the
    # answer worth surfacing in a search result.
    sales = context.get("local_sales")
    if sales and sales.get("median") and sales.get("enough_for_median"):
        faqs.append((
            f"What do houses actually sell for in {outcode}?",
            f"The median of {sales['count']} recorded sales around central {outcode}"
            + (f" in the last {sales['years']} years" if sales.get("is_recent") else "")
            + f" is £{sales['median']:,.0f}, ranging from £{sales['low']:,.0f} to "
            f"£{sales['high']:,.0f} (HM Land Registry Price Paid Data).",
        ))

    landscape = context.get("landscape")
    if not context.get("is_scotland") and landscape and landscape.get("total_schools") and landscape.get("good_or_better_pct") is not None:
        faqs.append((
            f"Are the schools good in {outcode}?",
            f"{landscape['total_schools']} schools sit within {landscape.get('radius_miles', 3)} miles of central {outcode}, "
            f"and {landscape['good_or_better_pct']}% are rated Outstanding or Good by Ofsted.",
        ))
    crime_data = context.get("crime")
    # Police.uk barely covers Scotland, so a Scottish "0 crimes" is a
    # coverage gap; never state it as an answer.
    if not context.get("is_scotland") and crime_data and crime_data.get("total"):
        month = f" in {crime_data['month']}" if crime_data.get("month") else ""
        common = ""
        if crime_data.get("by_category"):
            common = f", most commonly {crime_data['by_category'][0]['category']}"
        faqs.append((
            f"How much crime is there in {outcode}?",
            f"{crime_data['total']} crimes were recorded within roughly a mile of central {outcode}{month}{common} (Police.uk).",
        ))
    flood = context.get("flood_zone")
    if flood and flood.get("label") and not context.get("is_scotland"):
        faqs.append((
            f"Is {outcode} at risk of flooding?",
            f"Central {outcode} sits in {flood['label']} for river and sea flooding (Environment Agency). "
            "Individual addresses vary, so check the full report for a specific property.",
        ))
    # Two questions people actually type, taken from this site's own
    # Search Console: "is eh12 a good place to live", "is doncaster
    # safe". The guide already held the answers and never used those
    # words, so it could not match the query.
    #
    # Both are answered by laying out the figures and their sources,
    # never by delivering a verdict. Whether somewhere is good to live
    # is not a thing open data can tell you, and pretending otherwise
    # would be the opinion this site refuses to trade in.
    quality_bits = []
    if landscape and landscape.get("good_or_better_pct") is not None and not context.get("is_scotland"):
        quality_bits.append(
            f"{landscape['good_or_better_pct']}% of nearby schools are rated Outstanding or Good by Ofsted"
        )
    if sales and sales.get("enough_for_median"):
        quality_bits.append(f"the median home sells for £{sales['median']:,.0f}")
    dep = context.get("deprivation")
    if dep and dep.get("imd_decile"):
        quality_bits.append(
            f"the area sits in decile {dep['imd_decile']} of 10 on the Index of Multiple Deprivation"
        )
    if crime_data and crime_data.get("total") and not context.get("is_scotland"):
        quality_bits.append(f"{crime_data['total']} crimes were recorded within about a mile")
    if len(quality_bits) >= 2:
        faqs.append((
            f"Is {outcode} a good place to live?",
            "That depends on what you need, so here is what the official data says rather than an "
            f"opinion: {'; '.join(quality_bits)}. Every one of those figures names its source on this page.",
        ))

    if not context.get("is_scotland") and crime_data:
        if crime_data.get("total"):
            month = f" in {crime_data['month']}" if crime_data.get("month") else ""
            common = (
                f" The most common category was {crime_data['by_category'][0]['category']}."
                if crime_data.get("by_category") else ""
            )
            faqs.append((
                f"Is {outcode} safe?",
                f"{crime_data['total']} crimes were recorded within roughly a mile of central "
                f"{outcode}{month}, according to Police.uk.{common} Crime counts follow how many "
                "people are around, so a busy district records more than a quiet one of the same size.",
            ))
        elif crime_data.get("unpublished"):
            faqs.append((
                f"Is {outcode} safe?",
                "The local force has not published street-level crime data for this area recently, "
                "so we show no figure rather than a zero that would wrongly suggest no crime.",
            ))

    context["area_faqs"] = faqs
    context["area_faqs_jsonld"] = _faq_jsonld(faqs)


def _faq_jsonld(faqs) -> str:
    """FAQPage structured data from (question, answer) pairs.

    The area guides had this and nothing else did, so the school
    admission pages, both calculators and the comparison pages were
    showing real questions on screen with nothing telling Google they
    were questions. Those are the pages built around a query someone
    actually types ("what is the catchment area for X"), which is
    exactly where an FAQ rich result earns its place.
    """
    if not faqs:
        return ""
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}}
            for q, a in faqs
        ],
    }, separators=(",", ":"))


async def _bounded(coro, seconds: float):
    """Give a slow upstream a hard budget. The task keeps running past
    the deadline (shielded), so its own cache still fills for the next
    visitor - we just stop waiting.

    Amenities come from Overpass, which measured 10.6s cold against
    under 4s for everything else on an area guide. Those pages exist to
    be crawled and ranked, and a crawler will not wait ten seconds for
    "12 supermarkets, 40 pubs"; past the budget the line is simply
    omitted, which the template already copes with.
    """
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=seconds)
    except asyncio.TimeoutError:
        return None


AREA_PREWARM_BATCH = 250
AREA_PREWARM_REFRESH_AHEAD_S = 86400 * 2
# Breathing room between districts. Render runs one worker, and a
# back-to-back batch measurably starved live requests: a report that
# normally takes seconds timed out twice at two minutes while a 400
# district batch was in flight. Warming is never urgent, so it yields.
AREA_PREWARM_SPACING_S = 2.0


@app.post("/internal/prewarm-area-guides")
async def prewarm_area_guides(request: Request, limit: int = AREA_PREWARM_BATCH):
    """Warm the persistent cache for area guides that are missing or
    close to expiring.

    A cold guide takes about five seconds, because it waits on live
    Police.uk and Land Registry calls. With 2,943 of them and a crawler
    that visits each URL rarely, essentially every crawl was paying
    that five seconds, which is a poor use of the crawl budget Google
    gives a new site. Warmed ahead of time, the same page serves in
    under half a second from the database.

    Deliberately a small batch on a schedule rather than all 2,943 at
    once: the upstreams are public services doing us a favour, and
    nothing here is urgent enough to justify hammering them. At this
    size the whole set stays warm on a comfortable rotation.
    """
    configured_secret = os.environ.get("ALERTS_CRON_SECRET")
    provided_secret = request.headers.get("x-alerts-secret", "")
    if not configured_secret or not hmac.compare_digest(provided_secret, configured_secret):
        return JSONResponse({"error": "not_found"}, status_code=404)

    # A batch takes minutes (each cold guide is about five seconds), far
    # longer than any HTTP client or platform will hold a request open.
    # Run it detached and answer straight away: the caller is a cron
    # trigger that only needs to know the work started, and a request
    # that times out mid-batch would report failure on a run that
    # actually succeeded.
    limit = max(1, min(limit, len(ALL_OUTCODES)))
    task = asyncio.create_task(_prewarm_area_batch(limit))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return JSONResponse({"started": True, "limit": limit, "total": len(ALL_OUTCODES)})


async def _prewarm_area_batch(limit: int) -> dict:
    outcodes = [o["outcode"] for o in ALL_OUTCODES]
    # Refresh before expiry, so a guide never goes cold between runs.
    fresh_for = AREA_GUIDE_CACHE_TTL_S - AREA_PREWARM_REFRESH_AHEAD_S
    warmed, skipped, failed = 0, 0, 0

    for outcode in outcodes:
        if warmed >= limit:
            break
        cache_key = ("area_guide", AREA_GUIDE_PAYLOAD_VERSION, outcode)
        if await asyncio.to_thread(_cache.get_persistent, cache_key, fresh_for) is not None:
            skipped += 1
            continue
        try:
            location, _ = await _resolve_extension_location(outcode)
            if location is None:
                failed += 1
                continue
            await _build_area_payload(outcode, location, cache_key)
            warmed += 1
        except Exception:  # noqa: BLE001 - one bad district must not stop the run
            failed += 1
        await asyncio.sleep(AREA_PREWARM_SPACING_S)

    logging.getLogger(__name__).info(
        "area prewarm: warmed %d, already fresh %d, failed %d", warmed, skipped, failed
    )
    return {"warmed": warmed, "already_fresh": skipped, "failed": failed}


def _occupation_mix(lsoa_code: str) -> list[dict] | None:
    """What people around here do for a living, from Census 2021 (TS063).

    The closest thing in open data to the "job sectors" an investor asks
    about. It is occupation rather than industry, which is a real
    distinction: it says a neighbourhood is full of skilled trades or of
    professionals, not which industries employ them. The page says which
    of the two it is rather than letting one stand in for the other.

    The breakdown itself already exists on the report; this only picks
    the largest few, so there is one definition of the categories.
    """
    if not lsoa_code:
        return None
    data = census_stats.occupation_for_lsoa(lsoa_code)
    if not data or not data.get("breakdown"):
        return None
    top = sorted(data["breakdown"], key=lambda b: -(b.get("count") or 0))
    return [b for b in top if b.get("count")][:5] or None


async def _build_area_payload(outcode: str, location: dict, cache_key: tuple) -> dict:
    """Fetch and assemble everything an area guide renders, and store
    it in the persistent cache. Shared by the page itself and the
    prewarm job, so a warmed entry is exactly what a visitor would
    have been served rather than a thinner stand-in."""
    lat, lon = location["latitude"], location["longitude"]
    codes = location.get("codes", {})

    _last_gather_timings.clear()
    (hpi_result, crime_result, landscape_result, flood_zone_result, deprivation_result,
     amenities_result, local_sales_result) = await asyncio.gather(
        _timed("hpi", hpi.area_comparison(location["admin_district"], location["region"], location.get("country", ""))),
        _timed("crime", crime.summary_for_outcode(outcode)),
        _timed("school-landscape", asyncio.to_thread(schools_db.school_landscape, lat, lon)),
        _timed("flood-zone", flood_zones.zone_for(lat, lon)),
        _timed("deprivation", asyncio.to_thread(area_stats.deprivation_for_lsoa, codes.get("lsoa", ""))),
        _timed("amenities", _bounded(amenities.nearby_amenities_and_station(lat, lon, lite=True), 3.0)),
        # Actual sales on the streets of this district, not the council's
        # average. Everything else on this page is measured from the
        # district's centre point, and neighbouring centres in a city are
        # a few hundred metres apart, so M2 and M3 came out with the same
        # schools, the same crime and the same flood zone: the pages
        # differed only in their own name. Real transactions differ
        # street by street, which is both what makes each guide worth
        # reading and what makes it a distinct page.
        _timed("local-sales", _bounded(_outcode_sales(lat, lon), 6.0)),
        return_exceptions=True,
    )

    def ok(result):
        return result if not isinstance(result, Exception) else None

    amenities_data = ok(amenities_result)
    amenity_plurals = {
        "supermarket": "supermarkets", "pharmacy": "pharmacies", "restaurant": "restaurants",
        "pub": "pubs", "hospital": "hospitals",
    }
    amenity_summary = [
        {"count": len(amenities_data["categories"][cat]), "label": label if len(amenities_data["categories"][cat]) != 1 else cat}
        for cat, label in amenity_plurals.items()
        if amenities_data and amenities_data["categories"].get(cat)
    ]

    landscape = ok(landscape_result)
    if landscape:
        # This page reads three aggregate fields from the landscape. The
        # full per-school list that comes with it (250 rows, 1.4 MB for a
        # central London district) was being cached along with them -
        # making area guides the heaviest thing in both cache tiers by a
        # factor of a hundred, and the reason the Render instance ran out
        # of memory during a crawl. Keep only what the template uses.
        landscape = {k: landscape.get(k) for k in AREA_GUIDE_LANDSCAPE_FIELDS}

    landscape_full = ok(landscape_result)
    page_data = {
        "named_schools": _named_schools(landscape_full),
        # Both already collected and never shown on this page. An
        # investor browsing area guides had no way to know the site
        # held either.
        "universities": (landscape_full or {}).get("higher_education_names", [])[:6],
        "university_count": (landscape_full or {}).get("higher_education_count", 0),
        "private_schools": (landscape_full or {}).get("independent_names", [])[:6],
        "private_school_count": (landscape_full or {}).get("independent_count", 0),
        "by_sector": (landscape_full or {}).get("by_sector"),
        "occupation_mix": _occupation_mix(codes.get("lsoa", "")),
        "hpi": ok(hpi_result),
        "crime": ok(crime_result),
        "landscape": landscape,
        "flood_zone": ok(flood_zone_result),
        "deprivation": ok(deprivation_result),
        "amenity_summary": amenity_summary,
        "local_sales": ok(local_sales_result),
        "has_data": any([ok(hpi_result), ok(crime_result), ok(landscape_result), ok(flood_zone_result)]),
    }
    await asyncio.to_thread(_cache.set_persistent, cache_key, page_data)
    return page_data


@app.get("/area/{outcode}")
async def area_guide(request: Request, outcode: str):
    """A standing SEO landing page per UK postcode district (e.g.
    /area/SW1A), separate from /property?postcode=X: that page is
    written for someone evaluating one specific purchase and runs the
    full ~15-service gather; this one is written for someone browsing
    an area generally (the actual search intent behind "SW1A house
    prices"-style queries), so it stays to a lighter, area-only signal
    set and genuinely different copy - not just the same page under a
    cleaner URL. Cached a full day since search crawlers are the
    primary audience and this data doesn't move that fast anyway."""
    outcode = outcode.strip().upper()
    context = base_context(request)
    context["query"] = outcode
    # Path-based, but not case-normalized in the URL itself - without this
    # override, /area/sw1a and /area/SW1A canonicalize to two different
    # URLs for identical content.
    context["canonical_url"] = f"{_public_base_url(request)}/area/{outcode}"

    if not _OUTCODE_RE.match(outcode):
        return templates.TemplateResponse(request, "area_guide.html", context, status_code=404)

    location, _ = await _resolve_extension_location(outcode)
    if location is None:
        return templates.TemplateResponse(request, "area_guide.html", context, status_code=404)

    # location["outcode"] can legitimately be a NEIGHBOURING district
    # (e.g. requesting SW1A can resolve via SW1Y) when the requested
    # one has no real postcode within _resolve_extension_location's own
    # search radius - common for districts that are mostly non-
    # residential. The area-level data is still representative (the
    # neighbour is close enough to the requested centroid to be a fair
    # stand-in), but the page's own identity - title, H1, cache key -
    # stays the outcode actually requested, not the one that happened
    # to supply the geocoding.
    lat, lon = location["latitude"], location["longitude"]
    codes = location.get("codes", {})
    context["outcode"] = outcode
    context["admin_district"] = location["admin_district"]
    context["region"] = location["region"]
    # House prices (UK HPI) are a genuine 4-nations dataset and work
    # fine here - it's crime (data.police.uk: British Transport Police
    # only in Scotland) and flood zone (Environment Agency: England
    # only, silently defaults to "Zone 1") that read as real findings
    # while actually being data-coverage gaps. See the same note on
    # property_search.
    context["is_scotland"] = location.get("country") == "Scotland"

    # Versioned: entries hold a fixed set of fields, and the persistent
    # tier now keeps them for a week. Without a bump, adding a field
    # means every already-cached district silently renders without it
    # until its entry expires.
    cache_key = ("area_guide", AREA_GUIDE_PAYLOAD_VERSION, outcode)
    # Persistent tier: survives deploys, which is what keeps the 2,943
    # guides warm for crawlers. The DB round trip runs off the event loop.
    cached = await asyncio.to_thread(_cache.get_persistent, cache_key, AREA_GUIDE_CACHE_TTL_S)
    if cached is not None:
        context.update(cached)
        _area_guide_extras(context, outcode, lat, lon)
        response = templates.TemplateResponse(request, "area_guide.html", context)
        response.headers["Server-Timing"] = f'cache;desc="{_cache.last_outcome}"'
        return response

    page_data = await _build_area_payload(outcode, location, cache_key)
    context.update(page_data)
    _area_guide_extras(context, outcode, lat, lon)
    response = templates.TemplateResponse(request, "area_guide.html", context)
    timing = _server_timing_header()
    response.headers["Server-Timing"] = (timing + ", " if timing else "") + f'cache;desc="{_cache.last_outcome}"'
    return response


# --- Admin ---


def _is_admin(user: dict | None) -> bool:
    admin_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    return bool(admin_email and user and user.get("email", "").lower() == admin_email)


def _pct_change(old: int, new: int) -> float | None:
    """None means "no baseline to compare against" (old was zero) -
    callers show "new" rather than a meaningless divide-by-zero %."""
    if old == 0:
        return None
    return round((new - old) / old * 100, 1)


def _fmt_change(pct: float | None) -> str:
    if pct is None:
        return "new"
    if pct > 0:
        return f"↑{pct}%"
    if pct < 0:
        return f"↓{abs(pct)}%"
    return "flat"


def _admin_metrics(session, now: datetime.datetime) -> dict:
    """The query set behind /admin, factored out so the daily Telegram
    summary (see /internal/send-daily-summary below) reads the exact
    same numbers rather than a second, driftable copy of this logic."""
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - datetime.timedelta(days=7)
    month_start = now - datetime.timedelta(days=30)

    m: dict = {}
    m["pageviews_today"] = session.scalar(select(func.count()).select_from(PageView).where(PageView.created_at >= today_start)) or 0
    m["pageviews_week"] = session.scalar(select(func.count()).select_from(PageView).where(PageView.created_at >= week_start)) or 0
    m["pageviews_month"] = session.scalar(select(func.count()).select_from(PageView).where(PageView.created_at >= month_start)) or 0
    m["pageviews_total"] = session.scalar(select(func.count()).select_from(PageView)) or 0
    m["pageviews_clean"] = session.scalar(
        select(func.count()).select_from(PageView).where(PageView.created_at >= PAGEVIEW_CLEAN_FROM)
    ) or 0
    m["signups_clean"] = session.scalar(
        select(func.count()).select_from(User).where(User.created_at >= PAGEVIEW_CLEAN_FROM)
    ) or 0
    m["clean_from"] = PAGEVIEW_CLEAN_FROM

    # The funnel: each stage counted twice, all time and since the
    # clean date, because the early numbers include the owner's own
    # visits. Stages are people where a person can be identified
    # (accounts), otherwise events.
    def _funnel(since):
        pv = select(func.count()).select_from(PageView)
        users = select(func.count()).select_from(User)
        if since is not None:
            pv = pv.where(PageView.created_at >= since)
            users = users.where(User.created_at >= since)
        visits = session.scalar(pv.where(PageView.path != PAYWALL_PATH)) or 0
        searches = session.scalar(pv.where(PageView.path == "/property")) or 0
        signups = session.scalar(users) or 0
        used_q = select(func.count(func.distinct(PremiumUnlock.user_id)))
        if since is not None:
            used_q = used_q.where(PremiumUnlock.created_at >= since)
        used_any = session.scalar(used_q) or 0
        wall_q = select(func.count(func.distinct(PageView.user_id))).where(PageView.path == PAYWALL_PATH)
        if since is not None:
            wall_q = wall_q.where(PageView.created_at >= since)
        hit_wall = session.scalar(wall_q) or 0
        subscribed = session.scalar(users.where(User.is_premium.is_(True))) or 0
        stages = [
            ("Visits", visits, "pageviews"),
            ("Searched a property", searches, "report views"),
            ("Signed up", signups, "accounts"),
            ("Used a free report", used_any, "accounts"),
            ("Hit the paywall", hit_wall, "accounts with no free reports left that opened a new property"),
            ("Subscribed", subscribed, "accounts"),
        ]
        out, prev = [], None
        for label, n, unit in stages:
            rate = None if prev in (None, 0) else round(100 * n / prev, 1)
            out.append({"label": label, "count": n, "unit": unit, "rate": rate})
            prev = n
        return out
    m["funnel_all"] = _funnel(None)
    m["funnel_clean"] = _funnel(PAGEVIEW_CLEAN_FROM)

    m["share_links"] = session.scalar(select(func.count()).select_from(ShareLink)) or 0
    m["share_views"] = session.scalar(select(func.coalesce(func.sum(ShareLink.views), 0))) or 0
    m["share_sharers"] = session.scalar(select(func.count(func.distinct(ShareLink.user_id)))) or 0

    m["signups_today"] = session.scalar(select(func.count()).select_from(User).where(User.created_at >= today_start)) or 0
    m["signups_week"] = session.scalar(select(func.count()).select_from(User).where(User.created_at >= week_start)) or 0
    m["signups_month"] = session.scalar(select(func.count()).select_from(User).where(User.created_at >= month_start)) or 0
    m["signups_total"] = session.scalar(select(func.count()).select_from(User)) or 0

    m["premium_total"] = session.scalar(select(func.count()).select_from(User).where(User.is_premium.is_(True))) or 0
    # Free unlocks spent so far, and how many accounts have used all
    # of theirs - the two numbers that say whether the free allowance is
    # doing its job of showing people the product.
    m["free_unlocks_spent"] = session.scalar(
        select(func.count()).select_from(PremiumUnlock)
    ) or 0
    m["accounts_out_of_unlocks"] = session.scalar(
        select(func.count()).select_from(
            select(PremiumUnlock.user_id)
            .group_by(PremiumUnlock.user_id)
            .having(func.count() >= auth.FREE_PREMIUM_UNLOCKS)
            .subquery()
        )
    ) or 0

    plan_rows = session.execute(
        select(User.plan, func.count()).where(User.is_premium.is_(True)).group_by(User.plan)
    ).all()
    m["plan_breakdown"] = [{"plan": p or "Comped / no plan on file", "count": c} for p, c in plan_rows]

    # Daily pageviews and signups for the last 14 days, zero-filled so
    # every day appears even with no activity - without this, a single
    # active day among mostly-zero days renders as one bar filling the
    # whole chart width, since the bars split width evenly across
    # however many rows the query actually returned.
    date_range = [(today_start - datetime.timedelta(days=i)).date() for i in range(13, -1, -1)]

    daily_rows = session.execute(
        select(func.date(PageView.created_at), func.count())
        .where(PageView.created_at >= today_start - datetime.timedelta(days=13))
        .group_by(func.date(PageView.created_at))
    ).all()
    pageview_counts = {str(d): c for d, c in daily_rows}
    m["daily_pageviews"] = [{"date": str(d), "count": pageview_counts.get(str(d), 0)} for d in date_range]

    signup_rows = session.execute(
        select(func.date(User.created_at), func.count())
        .where(User.created_at >= today_start - datetime.timedelta(days=13))
        .group_by(func.date(User.created_at))
    ).all()
    signup_counts = {str(d): c for d, c in signup_rows}
    m["daily_signups"] = [{"date": str(d), "count": signup_counts.get(str(d), 0)} for d in date_range]

    # Trend: day-on-day and week-on-week % change, so a single number
    # ("35 today") gets context ("...which is up from 4 yesterday")
    # instead of standing alone with no sense of direction.
    m["pageviews_yesterday"] = pageview_counts.get(str(date_range[-2]), 0)
    m["pageviews_dod_change"] = _pct_change(m["pageviews_yesterday"], m["pageviews_today"])

    prev_week_start = week_start - datetime.timedelta(days=7)
    m["pageviews_prev_week"] = session.scalar(
        select(func.count()).select_from(PageView)
        .where(PageView.created_at >= prev_week_start, PageView.created_at < week_start)
    ) or 0
    m["pageviews_wow_change"] = _pct_change(m["pageviews_prev_week"], m["pageviews_week"])

    m["signups_prev_week"] = session.scalar(
        select(func.count()).select_from(User)
        .where(User.created_at >= prev_week_start, User.created_at < week_start)
    ) or 0
    m["signups_wow_change"] = _pct_change(m["signups_prev_week"], m["signups_week"])

    # Variance: how much daily pageviews normally swing around their own
    # 14-day average, so a busy or quiet single day can be read against
    # what's actually typical rather than compared to nothing.
    daily_counts = [d["count"] for d in m["daily_pageviews"]]
    m["pageviews_avg_14d"] = round(statistics.mean(daily_counts), 1) if daily_counts else 0
    m["pageviews_stdev_14d"] = round(statistics.pstdev(daily_counts), 1) if len(daily_counts) > 1 else 0

    # Top pages in the last 30 days - what's actually getting looked at.
    top_pages_rows = session.execute(
        select(PageView.path, func.count())
        .where(PageView.created_at >= month_start)
        .group_by(PageView.path)
        .order_by(func.count().desc())
        .limit(15)
    ).all()
    m["top_pages"] = [{"path": p, "count": c} for p, c in top_pages_rows]

    # Revenue: estimated MRR from ACTIVE subscriptions only (trialing
    # ones aren't paying yet, so they're surfaced separately rather
    # than folded into the total). Monthly-equivalent prices here
    # mirror stripe_billing.PLANS - keep them in sync if pricing changes.
    _monthly_equiv = {"monthly": 9.99, "quarterly": 24.99 / 3}
    active_plan_rows = session.execute(
        select(User.plan, func.count()).where(User.subscription_status == "active").group_by(User.plan)
    ).all()
    m["mrr_estimate"] = round(sum(_monthly_equiv.get(p, 0) * c for p, c in active_plan_rows), 2)
    m["active_subscriber_count"] = sum(c for _, c in active_plan_rows)
    m["trialing_count"] = session.scalar(
        select(func.count()).select_from(User).where(User.subscription_status == "trialing")
    ) or 0

    status_rows = session.execute(
        select(User.subscription_status, func.count())
        .where(User.stripe_subscription_id.is_not(None))
        .group_by(User.subscription_status)
    ).all()
    m["subscription_status_breakdown"] = [{"status": s or "unknown", "count": c} for s, c in status_rows]

    referral_rows = session.execute(
        select(User.referred_by, func.count())
        .where(User.referred_by.is_not(None))
        .group_by(User.referred_by)
        .order_by(func.count().desc())
    ).all()
    m["referral_breakdown"] = [{"code": code, "count": c} for code, c in referral_rows]

    recent = session.scalars(select(User).order_by(User.created_at.desc()).limit(20)).all()
    m["recent_signups"] = [
        {
            "email": u.email, "created_at": u.created_at, "is_premium": u.is_premium,
            "plan": u.plan, "subscription_status": u.subscription_status, "referred_by": u.referred_by,
        }
        for u in recent
    ]
    return m


@app.get("/admin")
def admin_dashboard(request: Request):
    """A single daily-review page, not a full admin panel - traffic,
    signups, Premium conversion and plan mix, all from data this app
    already has (no new third-party analytics service, which would
    also contradict the "no tracking" line in /privacy). Gated by
    ADMIN_EMAIL rather than a real roles/permissions system - there's
    exactly one person who needs this, so a proper roles table would
    be solving a problem that doesn't exist yet. 404s (not 403) for
    anyone else, so the route's existence isn't advertised either."""
    context = base_context(request)
    if not _is_admin(context["current_user"]):
        return templates.TemplateResponse(request, "404.html", context, status_code=404)

    with db.get_session() as session:
        context.update(_admin_metrics(session, datetime.datetime.now(datetime.timezone.utc)))

    with db.get_session() as session:
        context["figure_reports"] = session.scalars(
            select(FigureReport).order_by(
                (FigureReport.status != "open"), FigureReport.created_at.desc()
            ).limit(50)
        ).all()
    context["figure_statuses"] = FIGURE_STATUSES
    return templates.TemplateResponse(request, "admin.html", context)


@app.post("/internal/send-daily-summary")
async def send_daily_summary(request: Request):
    """Scheduled job (see .github/workflows/daily-summary.yml) - posts a
    short morning digest of the /admin dashboard's key numbers to
    Telegram, so there's no need to open the dashboard just to see
    whether anything happened overnight. Reuses _admin_metrics() so
    this never drifts from what /admin itself shows.

    Gated by the same shared secret as the watchlist-alerts job - both
    are cron triggers with no user attached, no need for a second one."""
    configured_secret = os.environ.get("ALERTS_CRON_SECRET")
    provided_secret = request.headers.get("x-alerts-secret", "")
    if not configured_secret or not hmac.compare_digest(provided_secret, configured_secret):
        return JSONResponse({"error": "not_found"}, status_code=404)

    if not telegram.is_configured():
        return JSONResponse({"error": "telegram_not_configured"}, status_code=503)

    now = datetime.datetime.now(datetime.timezone.utc)
    with db.get_session() as session:
        m = _admin_metrics(session, now)

    top_page = m["top_pages"][0]["path"] if m["top_pages"] else "—"
    trialing_note = f", {m['trialing_count']} trialing" if m["trialing_count"] else ""
    lines = [
        f"<b>UKPropertyInsight — {now.strftime('%A %d %B %Y')}</b>",
        "",
        f"\U0001F441 Pageviews: <b>{m['pageviews_today']}</b> today ({_fmt_change(m['pageviews_dod_change'])} vs yesterday), "
        f"{m['pageviews_week']} this week ({_fmt_change(m['pageviews_wow_change'])} vs last week)",
        f"✍️ Signups: <b>{m['signups_today']}</b> today, {m['signups_week']} this week "
        f"({_fmt_change(m['signups_wow_change'])} vs last week), {m['signups_total']} total",
        f"⭐ Premium: <b>{m['premium_total']}</b> of {m['signups_total']} accounts",
        f"\U0001F4B0 Est. MRR: <b>£{m['mrr_estimate']:.2f}</b>/month — {m['active_subscriber_count']} active{trialing_note}",
        f"\U0001F4CA 14-day trend: avg {m['pageviews_avg_14d']}/day, typical swing ±{m['pageviews_stdev_14d']}",
        f"\U0001F51D Top page: {top_page}",
        "🚶 Funnel since clean date: " + " → ".join(str(st["count"]) for st in m["funnel_clean"])
        + " (visits, searches, signups, used free, paywall, paid); "
        + f"{m['share_links']} share links, {m['share_views']} share views",
    ]
    sent = await telegram.send_message("\n".join(lines))
    return JSONResponse({"sent": sent})


# --- Accounts ---


def _public_base_url(request: Request) -> str:
    configured = os.environ.get("SITE_URL")
    if configured:
        return configured.rstrip("/")
    # Render serves every public request over HTTPS even though the
    # request this app sees internally may report http (no
    # --proxy-headers on the uvicorn start command) - force the scheme
    # rather than trust request.base_url's.
    return str(request.base_url).rstrip("/").replace("http://", "https://", 1)


def _oauth_redirect_uri(request: Request, provider: str = "google") -> str:
    """Where the provider sends the browser back to after sign-in.

    Deliberately not _public_base_url(): that forces https, which is
    correct in production but produces https://127.0.0.1:8000 locally,
    and Google rejects a redirect_uri that doesn't match the console
    entry exactly.
    """
    configured = os.environ.get("SITE_URL")
    if configured:
        base = configured.rstrip("/")
    elif IS_PRODUCTION:
        base = _public_base_url(request)
    else:
        base = str(request.base_url).rstrip("/")
    return f"{base}/auth/{provider}/callback"


@app.get("/premium")
def premium_info(request: Request, checkout: str = "", error: str = ""):
    context = base_context(request)
    context["billing_configured"] = stripe_billing.is_configured()
    context["plans"] = stripe_billing.plan_choices()
    context["pass_available"] = stripe_billing.pass_available()
    context["pass_months"] = stripe_billing.PASS_MONTHS
    context["checkout_cancelled"] = checkout == "cancelled"
    context["checkout_error"] = error == "checkout_failed"
    context["portal_error"] = error == "portal_failed"
    return templates.TemplateResponse(request, "premium.html", context)


@app.post("/premium/checkout")
async def premium_checkout(request: Request, plan: str = Form(...)):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login?next=/premium", status_code=303)

    base_url = _public_base_url(request)
    checkout_url = await stripe_billing.create_checkout_session(
        plan=plan,
        user_id=user["id"],
        user_email=user["email"],
        success_url=f"{base_url}/premium/success",
        cancel_url=f"{base_url}/premium/cancel",
    )
    if not checkout_url:
        return RedirectResponse("/premium?error=checkout_failed", status_code=303)
    return RedirectResponse(checkout_url, status_code=303)


@app.get("/premium/success")
def premium_success(request: Request):
    context = base_context(request)
    return templates.TemplateResponse(request, "premium_success.html", context)


@app.get("/premium/cancel")
def premium_cancel(request: Request):
    return RedirectResponse("/premium?checkout=cancelled", status_code=303)


@app.post("/premium/manage")
async def premium_manage(request: Request):
    """Redirects an existing subscriber to Stripe's hosted billing
    portal, where they can update payment details, switch plan, or
    cancel - all handled by Stripe, not custom UI here."""
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login?next=/premium", status_code=303)

    with db.get_session() as session:
        db_user = session.get(User, user["id"])
        customer_id = db_user.stripe_customer_id if db_user else None
    if not customer_id:
        return RedirectResponse("/premium", status_code=303)

    portal_url = await stripe_billing.create_billing_portal_session(
        customer_id, return_url=f"{_public_base_url(request)}/premium"
    )
    if not portal_url:
        return RedirectResponse("/premium?error=portal_failed", status_code=303)
    return RedirectResponse(portal_url, status_code=303)


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    if not stripe_billing.verify_webhook_signature(payload, sig_header):
        return JSONResponse({"error": "invalid_signature"}, status_code=400)

    event = json.loads(payload)
    event_type = event.get("type", "")
    data = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        user_id, customer_id = data.get("client_reference_id"), data.get("customer")
        if user_id and data.get("mode") == "payment" and data.get("payment_status") == "paid":
            # The one-off buying pass: grant time-boxed Premium now -
            # there is no subscription event coming to do it for us.
            with db.get_session() as session:
                db_user = session.get(User, int(user_id))
                if db_user:
                    db_user.is_premium = True
                    db_user.plan = "pass"
                    db_user.subscription_status = "pass"
                    db_user.pass_expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                        days=30 * stripe_billing.PASS_MONTHS
                    )
                    if customer_id:
                        db_user.stripe_customer_id = customer_id
                    session.commit()
        elif user_id and customer_id:
            with db.get_session() as session:
                db_user = session.get(User, int(user_id))
                if db_user:
                    db_user.stripe_customer_id = customer_id
                    db_user.stripe_subscription_id = data.get("subscription")
                    session.commit()

    elif event_type in ("customer.subscription.created", "customer.subscription.updated"):
        customer_id, status = data.get("customer"), data.get("status")
        with db.get_session() as session:
            db_user = session.scalar(select(User).where(User.stripe_customer_id == customer_id))
            if db_user is None:
                # Stripe doesn't guarantee delivery order between this
                # event and checkout.session.completed - if that one
                # hasn't linked stripe_customer_id onto a user yet,
                # there's genuinely nothing to update against right
                # now. A non-2xx here makes Stripe retry this same
                # event later (it does so automatically, for days),
                # rather than silently losing the update the way
                # returning 200 with nothing done would.
                return JSONResponse({"error": "customer_not_linked_yet"}, status_code=409)
            db_user.subscription_status = status
            db_user.is_premium = stripe_billing.grants_access(status)
            db_user.stripe_subscription_id = data.get("id")
            items = data.get("items", {}).get("data", [])
            price_id = items[0].get("price", {}).get("id") if items else None
            db_user.plan = stripe_billing.plan_for_price_id(price_id)
            trial_end = data.get("trial_end")
            db_user.trial_ends_at = (
                datetime.datetime.fromtimestamp(trial_end, tz=datetime.timezone.utc) if trial_end else None
            )
            session.commit()

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        with db.get_session() as session:
            db_user = session.scalar(select(User).where(User.stripe_customer_id == customer_id))
            if db_user is None:
                return JSONResponse({"error": "customer_not_linked_yet"}, status_code=409)
            db_user.subscription_status = "canceled"
            db_user.is_premium = False
            session.commit()

    return JSONResponse({"received": True})


# Google hands back an opaque code on failure; each maps to something a
# person can act on. Never render the raw value - it comes from the query
# string and lands straight in the page.
_AUTH_ERRORS = {
    "google_unavailable": "Google sign-in isn't set up right now — use your email and password.",
    "google_state": "That Google sign-in link had expired. Please try again.",
    "google_failed": "Couldn't finish signing in with Google. Please try again.",
    "oauth_unavailable": "That sign-in method isn't set up right now — use your email and password.",
    "oauth_state": "That sign-in link had expired. Please try again.",
    "oauth_failed": "Couldn't finish signing in. Please try again.",
    "accounts_unavailable": "Accounts are temporarily unavailable. Please try again shortly.",
}


@app.get("/signup")
def signup_form(request: Request, next: str = "/", error: str = ""):
    context = base_context(request)
    context["next"] = next
    context["error"] = _AUTH_ERRORS.get(error)
    return templates.TemplateResponse(request, "signup.html", context)


@app.post("/signup")
def signup_submit(
    request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/")
):
    context = base_context(request)
    context["next"] = next
    email = email.strip().lower()

    if len(password) < 8:
        context["error"] = "Password must be at least 8 characters."
        return templates.TemplateResponse(request, "signup.html", context)

    with db.get_session() as session:
        if auth.find_user_by_email(session, email):
            context["error"] = "An account with that email already exists."
            return templates.TemplateResponse(request, "signup.html", context)

        user = User(
            email=email, password_hash=auth.hash_password(password),
            referred_by=request.cookies.get(REFERRAL_COOKIE),
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        request.session["user_id"] = user.id

    return RedirectResponse(_safe_next(next), status_code=303)


@app.get("/login")
def login_form(request: Request, next: str = "/", error: str = ""):
    context = base_context(request)
    context["next"] = next
    context["error"] = _AUTH_ERRORS.get(error)
    return templates.TemplateResponse(request, "login.html", context)


@app.post("/login")
def login_submit(
    request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/")
):
    context = base_context(request)
    context["next"] = next
    email = email.strip().lower()

    with db.get_session() as session:
        user = auth.find_user_by_email(session, email)
        if user is None or not auth.verify_password(password, user.password_hash):
            context["error"] = "Incorrect email or password."
            return templates.TemplateResponse(request, "login.html", context)
        request.session["user_id"] = user.id

    return RedirectResponse(_safe_next(next), status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/api/postcode-suggest")
async def postcode_suggest(q: str = ""):
    """Autocomplete for the postcode search boxes. Cached per prefix
    for a day, so the many keystrokes of launch-day traffic collapse
    into few upstream postcodes.io calls."""
    q = q.strip().upper()
    if len(q) < 2:
        return JSONResponse({"suggestions": []})
    cache_key = ("pc_suggest", q)
    cached = _cache.get(cache_key, 86400)
    if cached is None:
        cached = await postcodes.autocomplete(q)
        _cache.set(cache_key, cached)
    return JSONResponse({"suggestions": cached})


@app.get("/api/report-ready")
async def report_ready(postcode: str = "", house_number: str = ""):
    """Polled by the report_building page. True once the gather for
    this address is cached. Also (re)starts the gather, so a server
    restart mid-build cannot strand a polling browser."""
    try:
        location = await lookup_postcode(postcode.strip())
    except httpx.HTTPError:
        return JSONResponse({"ready": False})
    if location is None:
        return JSONResponse({"ready": True})  # let the reload show not-found
    hn = house_number.strip()
    if _cache.get(("property_search_gather", location["postcode"], hn), PROPERTY_SEARCH_CACHE_TTL_S) is not None:
        return JSONResponse({"ready": True, "done": GATHER_SOURCE_ORDER, "sources": GATHER_SOURCE_ORDER})
    progress = _gather_progress.get((location["postcode"], hn))
    # Restart a gather that never started, or one that has gone quiet
    # long enough to be dead (a server restart, a task dropped
    # mid-flight). Without this a polling browser waits forever; with
    # it firing on every poll we would run a dozen gathers at once, so
    # a live one is left alone.
    if progress is None or time.time() - progress["touched"] > STALLED_GATHER_S:
        _spawn_gather(location, hn)
        progress = _gather_progress.get((location["postcode"], hn))
    return JSONResponse({
        "ready": False,
        # The sources actually back, so the page ticks off real work
        # rather than running a timer and hoping.
        "done": list(progress["done"]) if progress else [],
        "sources": GATHER_SOURCE_ORDER,
    })


@app.post("/admin/grant-premium")
def admin_grant_premium(request: Request, email: str = Form(...), action: str = Form("grant")):
    """Comp an account (or take a comp back). Exists so a promise like
    "DM me and I'll switch premium on" is honourable in two clicks.
    Refuses to touch accounts with a real Stripe subscription - the
    webhook owns those and would fight any manual change."""
    context = base_context(request)
    if not _is_admin(context["current_user"]):
        return templates.TemplateResponse(request, "404.html", context, status_code=404)
    with db.get_session() as session:
        target = auth.find_user_by_email(session, email.strip().lower())
        if target is None:
            return RedirectResponse("/admin?comp=notfound", status_code=303)
        if target.stripe_subscription_id:
            return RedirectResponse("/admin?comp=stripe", status_code=303)
        if action == "revoke":
            target.is_premium = False
            target.plan = None
            target.subscription_status = None
        else:
            target.is_premium = True
            target.plan = "comped"
            target.subscription_status = "comped"
        session.commit()
    return RedirectResponse("/admin?comp=done", status_code=303)


# ---- Shareable reports ----

@app.post("/share")
def create_share(request: Request, postcode: str = Form(...), house_number: str = Form("")):
    """Mint (or reuse) a public link for a report the caller can see in
    full. The access check is the same one the report page makes, so a
    free account can only share a property it has actually unlocked."""
    current = auth.current_user(request)
    postcode, house_number = auth.property_key(postcode, house_number)
    back = f"/property?{urlencode({'postcode': postcode, 'house_number': house_number})}"
    if not current or not db.is_configured():
        return RedirectResponse(f"/login?next={quote(back)}", status_code=303)

    with db.get_session() as session:
        allowed = current.get("subscribed") or auth.has_unlocked(session, current["id"], postcode, house_number)
        if not allowed:
            return RedirectResponse(back, status_code=303)
        link = session.scalar(select(ShareLink).where(
            ShareLink.user_id == current["id"], ShareLink.postcode == postcode,
            ShareLink.house_number == house_number,
        ))
        if link is None:
            link = ShareLink(token=secrets.token_urlsafe(18), user_id=current["id"],
                             postcode=postcode, house_number=house_number)
            session.add(link)
            session.commit()
    return RedirectResponse(back + "&shared=1#share", status_code=303)


@app.get("/s/{token}")
async def view_share(request: Request, token: str):
    """The shared report. Full view for anyone holding the link, no
    account and no unlock needed. Noindexed, and never offers to share
    again - that belongs to the person who owns the report."""
    if not db.is_configured():
        return RedirectResponse("/", status_code=303)
    with db.get_session() as session:
        link = session.get(ShareLink, token)
        if link is None:
            context = base_context(request)
            return templates.TemplateResponse(request, "404.html", context, status_code=404)
        link.views = (link.views or 0) + 1
        session.commit()
        postcode, house_number = link.postcode, link.house_number
        share = ShareLink(token=link.token, user_id=link.user_id, postcode=postcode,
                          house_number=house_number, views=link.views)
    return await _render_property(request, postcode, house_number, _share=share)


# ---- "Is this wrong?" figure reports ----

FIGURE_STATUSES = {
    "open": "Open",
    "confirmed": "Confirmed and fixed",
    "labelled": "Figure was right, page now explains it",
    "correct": "Checked, figure was correct",
}

# Corrections that reached us directly (a reader emailing, a tester
# going through a PDF) rather than through the report-a-figure form, so
# they have no FigureReport row. They belong on the accuracy log all the
# same: the log's whole point is that it shows the misses, and quietly
# omitting the ones that arrived by another route would defeat it.
# Newest first. Districts only, never a full postcode.
CORRECTIONS = [
    {
        "date": "2026-08-27",
        "district": "SK4",
        "card": "Crime & Safety",
        "reported": "A reader knew of a fire and a bike theft on their street, but the card showed no crimes at all.",
        "found": "Greater Manchester Police had published nothing to Police.uk for May or June 2026 while other forces were current. We were reading only the latest month, finding it empty, and rendering that as zero.",
        "fixed": "The card now walks back up to six months to find the most recent month the force actually published, and where a force has published nothing at all it says so in words instead of showing a figure. A publication gap can never again be displayed as a crime-free area.",
    },
    {
        "date": "2026-08-27",
        "district": "SK4",
        "card": "Noise",
        "reported": "A property known to sit under a flight path showed no aircraft noise, while a quieter one nearby did.",
        "found": "The figure was correct but the absence was misleading. DEFRA's noise maps only model levels above a minimum threshold near major roads, railways and the largest airports, so an address outside those contours has no reading at all, which is not the same as silence.",
        "fixed": "Both the report and the PDF now explain that a source not listed is outside DEFRA's mapped area rather than a zero reading.",
    },
]


@app.post("/report-figure")
async def report_figure(
    request: Request,
    postcode: str = Form(...), house_number: str = Form(""), card: str = Form(...),
    message: str = Form(...), email: str = Form(""),
):
    """One click from any card on a property report. Stores the report,
    pings the owner, and sends the reader back to the same report with a
    thank-you rather than a dead end."""
    postcode = postcode.strip().upper()[:16]
    house_number = house_number.strip()[:32]
    card = card.strip()[:120] or "Unspecified"
    message = message.strip()[:2000]
    email = email.strip().lower()[:255] or None
    back = f"/property?{urlencode({'postcode': postcode, 'house_number': house_number})}"

    if not message:
        return RedirectResponse(back + "&report=empty", status_code=303)
    if not db.is_configured():
        return RedirectResponse(back + "&report=unavailable", status_code=303)

    current = auth.current_user(request)
    with db.get_session() as session:
        session.add(FigureReport(
            postcode=postcode, house_number=house_number, card=card, message=message,
            email=email, user_id=current["id"] if current else None,
        ))
        session.commit()

    if telegram.is_configured():
        await telegram.send_message(
            f"🔍 Figure reported: <b>{card}</b> at {postcode} {house_number}\n"
            f"{message[:300]}\n{_public_base_url(request)}/admin#figure-reports"
        )
    return RedirectResponse(back + "&report=thanks", status_code=303)


@app.get("/accuracy")
def accuracy_log(request: Request):
    """Every figure anyone has reported, and what we found. Public on
    purpose: a site whose pitch is accuracy should show its working,
    including the times it was wrong. Postcodes are reduced to their
    district so no specific home can be identified."""
    context = base_context(request)
    reports, counts = [], {"total": 0, "open": 0, "confirmed": 0, "labelled": 0, "correct": 0}
    if db.is_configured():
        with db.get_session() as session:
            rows = session.scalars(
                select(FigureReport).order_by(FigureReport.created_at.desc()).limit(200)
            ).all()
        for r in rows:
            counts["total"] += 1
            counts[r.status] = counts.get(r.status, 0) + 1
            reports.append({
                "district": r.postcode.split(" ")[0] if " " in r.postcode else r.postcode[:-3] or r.postcode,
                "card": r.card, "created": r.created_at, "status": r.status,
                "status_label": FIGURE_STATUSES.get(r.status, r.status),
                "resolution": r.resolution, "resolved": r.resolved_at,
            })
    counts["total"] += len(CORRECTIONS)
    counts["confirmed"] = counts.get("confirmed", 0) + len(CORRECTIONS)
    context["reports"] = reports
    context["corrections"] = CORRECTIONS
    context["counts"] = counts
    context["statuses"] = FIGURE_STATUSES
    return templates.TemplateResponse(request, "accuracy.html", context)


@app.post("/admin/exclude-me")
def admin_exclude_me(request: Request):
    """Marks this browser as the owner's: a year-long cookie the pageview
    middleware honours whether or not they are logged in. Per browser,
    so it wants pressing once on each device."""
    context = base_context(request)
    if not _is_admin(context["current_user"]):
        return templates.TemplateResponse(request, "404.html", context, status_code=404)
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(PAGEVIEW_EXCLUDE_COOKIE, "1", max_age=365 * 24 * 3600,
                        httponly=True, samesite="lax", secure=IS_PRODUCTION)
    return response


@app.post("/admin/figure-reports/{report_id}")
def admin_resolve_figure(request: Request, report_id: int, status: str = Form(...), resolution: str = Form("")):
    context = base_context(request)
    if not _is_admin(context["current_user"]) or not db.is_configured():
        return templates.TemplateResponse(request, "404.html", context, status_code=404)
    if status not in FIGURE_STATUSES:
        return RedirectResponse("/admin#figure-reports", status_code=303)
    with db.get_session() as session:
        row = session.get(FigureReport, report_id)
        if row is not None:
            row.status = status
            row.resolution = resolution.strip()[:2000] or None
            row.resolved_at = datetime.datetime.now(datetime.timezone.utc) if status != "open" else None
            session.commit()
    return RedirectResponse("/admin#figure-reports", status_code=303)


# ---- Password reset ----
#
# Signed, time-limited token in the email link, nothing stored: the
# token carries the user id and is checked against the account's
# CURRENT password hash, so it is single-use by construction - the
# moment the password changes, every outstanding token for that account
# stops verifying. Same itsdangerous pattern as the extension login,
# under its own salt so the two token kinds can never be swapped.
RESET_TOKEN_MAX_AGE_S = 60 * 60  # one hour


def _reset_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(SESSION_SECRET, salt="password-reset")


def _reset_token_for(user: User) -> str:
    # The hash fingerprint is what makes the link single-use. Only a
    # prefix is embedded, which is plenty to detect a change and avoids
    # putting the whole hash in a URL.
    return _reset_serializer().dumps({"uid": user.id, "h": user.password_hash[:16]})


def _user_for_reset_token(session, token: str) -> User | None:
    try:
        data = _reset_serializer().loads(token, max_age=RESET_TOKEN_MAX_AGE_S)
    except (BadSignature, SignatureExpired):
        return None
    user = session.get(User, data.get("uid"))
    if user is None or user.password_hash[:16] != data.get("h"):
        return None
    return user


_RESET_ERRORS = {
    "invalid": "That reset link has expired or already been used. Request a new one below.",
    "short": "Password must be at least 8 characters.",
    "mismatch": "The two passwords didn't match.",
}


@app.get("/forgot-password")
def forgot_password_form(request: Request):
    context = base_context(request)
    context["email_configured"] = email_service.is_configured()
    return templates.TemplateResponse(request, "forgot_password.html", context)


@app.post("/forgot-password")
async def forgot_password_submit(request: Request, email: str = Form(...)):
    context = base_context(request)
    context["email_configured"] = email_service.is_configured()
    email = email.strip().lower()

    # Always the same response whether or not the address exists: the
    # form must not be usable to discover which emails have accounts.
    context["sent_to"] = email

    if not db.is_configured() or not email_service.is_configured():
        return templates.TemplateResponse(request, "forgot_password.html", context)

    with db.get_session() as session:
        user = auth.find_user_by_email(session, email)
        if user is None:
            return templates.TemplateResponse(request, "forgot_password.html", context)
        link = f"{_public_base_url(request)}/reset-password?token={_reset_token_for(user)}"

    await email_service.send_email(
        email,
        "Reset your UKPropertyInsight password",
        _reset_email_html(link),
    )
    return templates.TemplateResponse(request, "forgot_password.html", context)


def _reset_email_html(link: str) -> str:
    return (
        '<div style="font-family:sans-serif;max-width:520px;margin:0 auto;">'
        "<h2>Reset your password</h2>"
        "<p>Someone asked to reset the password for this UKPropertyInsight account. "
        "If that was you, the link below works for one hour and can be used once.</p>"
        f'<p><a href="{link}" style="display:inline-block;padding:10px 18px;background:#2b4c8c;'
        'color:#fff;border-radius:6px;text-decoration:none;">Choose a new password</a></p>'
        '<p style="color:#667085;font-size:12px;">If you did not ask for this, ignore it. '
        "Your password has not changed and nothing else needs doing.</p>"
        "</div>"
    )


@app.get("/reset-password")
def reset_password_form(request: Request, token: str = "", error: str = ""):
    context = base_context(request)
    context["token"] = token
    context["error"] = _RESET_ERRORS.get(error)
    if not db.is_configured():
        context["error"] = _RESET_ERRORS["invalid"]
        return templates.TemplateResponse(request, "reset_password.html", context)
    with db.get_session() as session:
        if _user_for_reset_token(session, token) is None:
            context["error"] = _RESET_ERRORS["invalid"]
            context["token"] = ""
    return templates.TemplateResponse(request, "reset_password.html", context)


@app.post("/reset-password")
def reset_password_submit(
    request: Request, token: str = Form(...), password: str = Form(...), confirm: str = Form(...)
):
    if len(password) < 8:
        return RedirectResponse(f"/reset-password?token={token}&error=short", status_code=303)
    if password != confirm:
        return RedirectResponse(f"/reset-password?token={token}&error=mismatch", status_code=303)
    if not db.is_configured():
        return RedirectResponse("/reset-password?error=invalid", status_code=303)

    with db.get_session() as session:
        user = _user_for_reset_token(session, token)
        if user is None:
            return RedirectResponse("/reset-password?error=invalid", status_code=303)
        user.password_hash = auth.hash_password(password)
        session.commit()
        # Sign them in: they have just proved control of the mailbox,
        # which is what logging in proves too.
        request.session["user_id"] = user.id

    return RedirectResponse("/?reset=done", status_code=303)


def _oauth_provider_configured(provider: str) -> bool:
    if provider == "google":
        return google_oauth.is_configured()
    return oauth_providers.is_configured(provider)


OAUTH_PROVIDER_NAMES = ("google", "facebook", "linkedin")


@app.get("/auth/{provider}")
def oauth_login(request: Request, provider: str, next: str = "/"):
    if provider not in OAUTH_PROVIDER_NAMES or not _oauth_provider_configured(provider):
        return RedirectResponse("/login?error=oauth_unavailable", status_code=303)
    if not db.is_configured():
        return RedirectResponse("/login?error=accounts_unavailable", status_code=303)

    # The state token is what stops a third party from feeding this app a
    # code they obtained themselves: it has to come back matching the one
    # we just put in this browser's signed session cookie.
    state = secrets.token_urlsafe(32)
    request.session[f"{provider}_oauth_state"] = state
    request.session[f"{provider}_oauth_next"] = _safe_next(next)
    redirect_uri = _oauth_redirect_uri(request, provider)
    if provider == "google":
        url = google_oauth.authorization_url(redirect_uri, state)
    else:
        url = oauth_providers.authorization_url(provider, redirect_uri, state)
    return RedirectResponse(url, status_code=303)


@app.get("/auth/{provider}/callback")
async def oauth_callback(
    request: Request, provider: str, code: str = "", state: str = "", error: str = ""
):
    if provider not in OAUTH_PROVIDER_NAMES:
        return RedirectResponse("/login?error=oauth_unavailable", status_code=303)
    expected_state = request.session.pop(f"{provider}_oauth_state", None)
    next_url = _safe_next(request.session.pop(f"{provider}_oauth_next", "/"))

    # error=access_denied is the normal "user clicked Cancel" path, not a
    # fault - put them back on the login page without an alarming message.
    if error or not code:
        return RedirectResponse("/login", status_code=303)
    if not expected_state or not hmac.compare_digest(state, expected_state):
        return RedirectResponse("/login?error=oauth_state", status_code=303)
    if not db.is_configured():
        return RedirectResponse("/login?error=accounts_unavailable", status_code=303)

    redirect_uri = _oauth_redirect_uri(request, provider)
    if provider == "google":
        email = await google_oauth.fetch_verified_email(code, redirect_uri)
    else:
        email = await oauth_providers.fetch_verified_email(provider, code, redirect_uri)
    if not email:
        return RedirectResponse("/login?error=oauth_failed", status_code=303)

    with db.get_session() as session:
        user = auth.find_user_by_email(session, email)
        if user is None:
            user = User(
                email=email,
                password_hash=auth.GOOGLE_ACCOUNT_PLACEHOLDER,
                referred_by=request.cookies.get(REFERRAL_COOKIE),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
        # An existing password account with this address gets signed in
        # rather than rejected as a duplicate. The provider has verified the
        # person controls the mailbox, which is the same thing the
        # password proves, so this is a second key to their own door -
        # their password keeps working too.
        request.session["user_id"] = user.id

    return RedirectResponse(next_url, status_code=303)


# --- Watchlist ---


@app.get("/watchlist")
async def watchlist_view(request: Request):
    context = base_context(request)
    if not context["current_user"]:
        return RedirectResponse("/login?next=/watchlist", status_code=303)

    items = watchlist.list_items(context["current_user"]["id"])
    # Reports this account has opened in full but never saved - shown
    # as one-click adds, so the page fills itself from real activity
    # instead of starting empty.
    saved_keys = {(i["postcode"], i["house_number"]) for i in items}
    with db.get_session() as session:
        unlock_rows = session.scalars(
            select(PremiumUnlock)
            .where(PremiumUnlock.user_id == context["current_user"]["id"])
            .order_by(PremiumUnlock.created_at.desc())
        ).all()
        context["opened_reports"] = [
            {"postcode": u.postcode, "house_number": u.house_number, "opened_at": u.created_at}
            for u in unlock_rows
            if (u.postcode, u.house_number) not in saved_keys
        ]
    if items:
        fresh_summaries = await asyncio.gather(
            *(_comparison_summary(item["postcode"], item["house_number"]) for item in items),
            return_exceptions=True,
        )
        for item, fresh in zip(items, fresh_summaries):
            if isinstance(fresh, Exception):
                item["changes"] = []
                continue
            old = json.loads(item["last_snapshot"]) if item["last_snapshot"] else None
            item["changes"] = _snapshot_changes(old, fresh) if old else []
            watchlist.update_snapshot(context["current_user"]["id"], item["id"], json.dumps(fresh, default=str))
    context["items"] = items
    context["changed_item_count"] = sum(1 for item in items if item["changes"])
    context["alerts_configured"] = email_service.is_configured()
    return templates.TemplateResponse(request, "watchlist.html", context)


@app.get("/watchlist/compare")
async def watchlist_compare(request: Request, item_ids: list[int] = Query(default=[])):
    context = base_context(request)
    if not context["current_user"]:
        return RedirectResponse("/login?next=/watchlist", status_code=303)

    items = watchlist.get_items_by_ids(context["current_user"]["id"], item_ids)
    if items:
        summaries = await asyncio.gather(
            *(_comparison_summary(item["postcode"], item["house_number"]) for item in items),
            return_exceptions=True,
        )
        context["columns"] = [
            {**item, "summary": ({"not_found": True} if isinstance(s, Exception) else s)}
            for item, s in zip(items, summaries)
        ]
    else:
        context["columns"] = []
    return templates.TemplateResponse(request, "compare.html", context)


OG_IMAGE_CACHE_TTL_S = 60 * 60 * 6


def _og_facts(context: dict) -> list[tuple[str, str]]:
    """Up to three headline figures for a share card, in the order a
    reader would want them. Free-tier data only: a share image is
    public, so nothing behind the paywall belongs on it."""
    facts: list[tuple[str, str]] = []

    # Values are kept short enough to sit on one line of a chip at the
    # card's size; anything longer gets clipped, which looks broken.
    flood_zone = context.get("flood_zone")
    if context.get("flood_warnings"):
        facts.append(("Flood risk", "Active warning"))
    elif flood_zone and flood_zone.get("label"):
        facts.append(("Flood risk", flood_zone["label"].split(" (")[0]))

    landscape = context.get("school_landscape")
    if landscape and landscape.get("good_or_better_pct") is not None:
        facts.append(("Schools", f"{landscape['good_or_better_pct']}% good or better"))

    crime_data = context.get("crime")
    if crime_data and crime_data.get("total") is not None:
        facts.append(("Crime", f"{crime_data['total']:,} nearby"))
    elif crime_data and crime_data.get("unpublished"):
        facts.append(("Crime", "Not published"))

    if len(facts) < 3:
        transactions = context.get("transactions")
        if transactions:
            facts.append(("Last sale", _format_gbp(transactions[0]["amount"])))
    return facts[:3]


def _og_payload(context: dict) -> dict:
    """The handful of finished values a share card draws, extracted from
    a completed gather context."""
    overview = context.get("overview") or {}
    return {
        "score": overview.get("score"),
        "grade": overview.get("grade", ""),
        "facts": _og_facts(context),
    }


@app.get("/og/property.png")
async def og_property_image(request: Request, postcode: str = "", house_number: str = ""):
    """The share card for one report. Built only from what is already
    cached: an image request must never be able to start the full
    gather, or a crawler following a few links would run it for us."""
    if not og_image.is_available():
        return RedirectResponse("/static/img/og-default.png", status_code=302)

    try:
        location = await lookup_postcode(postcode.strip())
    except httpx.HTTPError:
        location = None
    if location is None:
        return RedirectResponse("/static/img/og-default.png", status_code=302)

    canonical = location["postcode"]
    hn = house_number.strip()
    cache_key = ("og_card", canonical, hn)
    cached = _cache.get(cache_key, OG_IMAGE_CACHE_TTL_S)
    if cached is None:
        payload = _cache.get(("og_payload", canonical, hn), PROPERTY_SEARCH_CACHE_TTL_S) or {}
        cached = og_image.render(
            postcode=canonical,
            district=location.get("admin_district", ""),
            region=location.get("region", ""),
            score=payload.get("score"),
            grade=payload.get("grade", ""),
            facts=[tuple(f) for f in payload.get("facts", [])],
        )
        # A card drawn before the gather finished has the address but no
        # score. Serve it (a share should never wait on us) but do not
        # keep it, or the scoreless version is what everyone sees for
        # the next six hours.
        if payload.get("score") is not None:
            _cache.set(cache_key, cached)
        else:
            return Response(content=cached, media_type="image/png",
                            headers={"Cache-Control": "public, max-age=120"})

    return Response(
        content=cached,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=21600"},
    )


MAX_COMPARE_COLUMNS = 3


@app.get("/compare")
async def compare_postcodes(request: Request, postcode: list[str] = Query(default=[])):
    """Side-by-side for anyone, no account. Buyers are nearly always
    choosing between two or three areas rather than assessing one in
    isolation, and until now the only comparison view was behind a
    login and tied to saved properties. Reuses the watchlist compare's
    summary gather and table, so there is one comparison to maintain."""
    context = base_context(request)
    entered = [p.strip() for p in postcode if p and p.strip()][:MAX_COMPARE_COLUMNS]

    columns = []
    if entered:
        summaries = await asyncio.gather(
            *(_comparison_summary(p, "") for p in entered), return_exceptions=True
        )
        for typed, s in zip(entered, summaries):
            if isinstance(s, Exception):
                columns.append({"postcode": typed.upper(), "house_number": "", "summary": {"not_found": True}})
            else:
                columns.append({"postcode": s["postcode"], "house_number": "", "summary": s})

    context["columns"] = columns
    context["anonymous_compare"] = True
    context["entered"] = entered
    context["max_columns"] = MAX_COMPARE_COLUMNS
    return templates.TemplateResponse(request, "compare.html", context)


def _weekly_digest_email_html(rows: list[dict], watchlist_url: str, settings_url: str) -> str:
    """The opt-in weekly round-up. Every property gets a line whether or
    not anything moved, because "nothing changed this week" is a real
    and useful answer for someone tracking an area."""
    base = watchlist_url.rsplit("/watchlist", 1)[0]
    blocks = []
    for row in rows:
        report_url = f"{base}/property?{urlencode({'postcode': row.get('postcode', ''), 'house_number': row.get('house_number', '')})}"
        if row["changes"]:
            body = "".join(
                f'<li style="margin:4px 0;color:#3d3833;">{c}</li>' for c in row["changes"]
            )
            body = f'<ul style="margin:8px 0 0;padding-left:18px;">{body}</ul>'
        else:
            body = '<p style="margin:8px 0 0;color:#8a8378;font-size:14px;">No change this week.</p>'
        blocks.append(
            f'<div style="border:1px solid #e6e1d8;border-radius:8px;padding:14px 16px;margin-bottom:12px;">'
            f'<a href="{report_url}" style="font-size:16px;font-weight:600;color:#1f2a5a;text-decoration:none;">{row["label"]}</a>'
            f'{body}</div>'
        )
    moved = sum(1 for r in rows if r["changes"])
    headline = (
        f"{moved} of your {len(rows)} propert{'y' if len(rows) == 1 else 'ies'} changed this week"
        if moved else
        f"No changes on your {len(rows)} saved propert{'y' if len(rows) == 1 else 'ies'} this week"
    )
    return (
        '<div style="font-family:Georgia,serif;max-width:540px;margin:0 auto;padding:8px;">'
        '<p style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;color:#8a8378;margin:0 0 4px;">UKPropertyInsight</p>'
        f'<h2 style="margin:0 0 14px;color:#191613;">{headline}</h2>'
        + "".join(blocks) +
        f'<p style="margin:16px 0;"><a href="{watchlist_url}" style="color:#1f2a5a;">Open My properties</a></p>'
        '<p style="color:#8a8378;font-size:12px;line-height:1.5;">'
        "You asked for this weekly round-up when you ticked the box in My properties. "
        f'<a href="{settings_url}" style="color:#8a8378;">Turn it off</a> and you will only hear from us '
        "when something on a saved property actually changes."
        "</p></div>"
    )


@app.post("/watchlist/weekly-digest")
def watchlist_weekly_digest(request: Request, enabled: str = Form("")):
    context = base_context(request)
    if not context["current_user"]:
        return RedirectResponse("/login?next=/watchlist", status_code=303)
    watchlist.set_weekly_digest(context["current_user"]["id"], enabled == "on")
    return RedirectResponse("/watchlist?digest=" + ("on" if enabled == "on" else "off"), status_code=303)


@app.post("/internal/send-weekly-digest")
async def send_weekly_digest(request: Request):
    """Scheduled job (see .github/workflows/weekly-digest.yml). Emails
    only the accounts that opted in, and only about properties they
    saved. Shares the change detection with the daily alert job, but
    deliberately does NOT consume the snapshot: the alert job owns
    that, and a digest that silently swallowed a change would stop the
    person being told about it promptly."""
    configured_secret = os.environ.get("ALERTS_CRON_SECRET")
    provided_secret = request.headers.get("x-alerts-secret", "")
    if not configured_secret or not hmac.compare_digest(provided_secret, configured_secret):
        return JSONResponse({"error": "not_found"}, status_code=404)

    if not email_service.is_configured():
        return JSONResponse({"error": "email_not_configured"}, status_code=503)

    watchlist_url = f"{_public_base_url(request)}/watchlist"
    subscribers = watchlist.digest_subscribers()
    sent = 0

    for sub in subscribers:
        rows = []
        for item in sub["items"]:
            try:
                fresh = await _comparison_summary(item["postcode"], item["house_number"])
            except Exception:  # noqa: BLE001 - one bad address must not sink the digest
                continue
            old = json.loads(item["last_snapshot"]) if item["last_snapshot"] else None
            rows.append({
                "label": item["postcode"] + (f", {item['house_number']}" if item["house_number"] else ""),
                "postcode": item["postcode"],
                "house_number": item["house_number"],
                "changes": _snapshot_changes(old, fresh) if old else [],
            })
        if not rows:
            continue
        moved = sum(1 for r in rows if r["changes"])
        subject = (
            f"Your week: {moved} propert{'y' if moved == 1 else 'ies'} changed"
            if moved else "Your week: nothing changed on your saved properties"
        )
        ok = await email_service.send_email(
            sub["email"], subject,
            _weekly_digest_email_html(rows, watchlist_url, watchlist_url),
        )
        if ok:
            watchlist.mark_digest_sent(sub["user_id"])
            sent += 1

    return JSONResponse({"subscribers": len(subscribers), "sent": sent})


def _watchlist_alert_email_html(entries: list[dict], watchlist_url: str) -> str:
    base = watchlist_url.rsplit("/watchlist", 1)[0]
    blocks = []
    for e in entries:
        report_url = f"{base}/property?{urlencode({'postcode': e.get('postcode', ''), 'house_number': e.get('house_number', '')})}"
        change_rows = "".join(
            f'<li style="margin:4px 0;color:#3d3833;">{c}</li>' for c in e["changes"]
        )
        blocks.append(
            f'<div style="border:1px solid #e6e1d8;border-radius:8px;padding:14px 16px;margin-bottom:12px;">'
            f'<a href="{report_url}" style="font-size:16px;font-weight:600;color:#1f2a5a;text-decoration:none;">{e["label"]}</a>'
            f'<ul style="margin:8px 0 0;padding-left:18px;">{change_rows}</ul>'
            f'</div>'
        )
    return (
        '<div style="font-family:Georgia,serif;max-width:540px;margin:0 auto;padding:8px;">'
        '<p style="font-size:12px;letter-spacing:0.08em;text-transform:uppercase;color:#8a8378;margin:0 0 4px;">UKPropertyInsight</p>'
        '<h2 style="margin:0 0 14px;color:#191613;">Something changed on a property you follow</h2>'
        + "".join(blocks) +
        f'<p style="margin:16px 0;"><a href="{watchlist_url}" style="color:#1f2a5a;">Open My properties</a></p>'
        '<p style="color:#8a8378;font-size:12px;line-height:1.5;">'
        "You get this email only when something changes on a property saved in My properties, never on a schedule. "
        f'Remove a property from <a href="{watchlist_url}" style="color:#8a8378;">My properties</a> and its alerts stop.'
        "</p></div>"
    )


@app.post("/internal/run-watchlist-alerts")
async def run_watchlist_alerts(request: Request):
    """Scheduled job (see .github/workflows/watchlist-alerts.yml), not a
    user-facing route - re-checks every watchlist item across every user
    the same way the /watchlist page itself does on each visit, and
    emails anyone whose items picked up a meaningful change since last
    checked. A page visit and this job both update the same
    last_snapshot, so whichever happens first "consumes" a change -
    nobody gets double-notified via both paths.

    Gated by a shared secret header rather than a session/login check,
    since the caller is a cron trigger with no user attached."""
    configured_secret = os.environ.get("ALERTS_CRON_SECRET")
    provided_secret = request.headers.get("x-alerts-secret", "")
    if not configured_secret or not hmac.compare_digest(provided_secret, configured_secret):
        return JSONResponse({"error": "not_found"}, status_code=404)

    if not email_service.is_configured():
        return JSONResponse({"error": "email_not_configured"}, status_code=503)

    items = watchlist.all_items_with_owner_email()
    changes_by_email: dict[str, list[dict]] = {}

    for item in items:
        try:
            fresh = await _comparison_summary(item["postcode"], item["house_number"])
        except Exception:
            continue
        old = json.loads(item["last_snapshot"]) if item["last_snapshot"] else None
        changes = _snapshot_changes(old, fresh) if old else []
        watchlist.update_snapshot(item["user_id"], item["id"], json.dumps(fresh, default=str))
        if changes:
            label = item["postcode"] + (f", {item['house_number']}" if item["house_number"] else "")
            changes_by_email.setdefault(item["email"], []).append({
                "label": label, "changes": changes,
                "postcode": item["postcode"], "house_number": item["house_number"],
            })

    watchlist_url = f"{_public_base_url(request)}/watchlist"
    notified = 0
    for to_email, entries in changes_by_email.items():
        n_props = len(entries)
        subject = (f"{entries[0]['label']}: {entries[0]['changes'][0]}" if n_props == 1 and len(entries[0]["changes"]) == 1
                   else f"Changes on {n_props} propert{'y' if n_props == 1 else 'ies'} you follow")
        sent = await email_service.send_email(
            to_email, subject, _watchlist_alert_email_html(entries, watchlist_url)
        )
        if sent:
            notified += 1

    return JSONResponse({"checked": len(items), "users_with_changes": len(changes_by_email), "emails_sent": notified})


@app.post("/watchlist/save")
def watchlist_save(
    request: Request,
    postcode: str = Form(...),
    house_number: str = Form(""),
    note: str = Form(""),
):
    house_number = house_number.strip()
    qs = urlencode({"postcode": postcode, "house_number": house_number}) if house_number else urlencode({"postcode": postcode})
    user = auth.current_user(request)
    if not user:
        return RedirectResponse(f"/login?next=/property?{qs}", status_code=303)
    watchlist.save_item(user["id"], postcode, house_number, note.strip())
    if request.headers.get("referer", "").split("?")[0].endswith("/watchlist"):
        # Saved or note-edited from the My properties page itself - go
        # back there, not to the report.
        return RedirectResponse("/watchlist", status_code=303)
    return RedirectResponse(f"/property?{qs}", status_code=303)


@app.post("/watchlist/remove")
def watchlist_remove(request: Request, item_id: int = Form(...)):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login?next=/watchlist", status_code=303)
    watchlist.remove_item(user["id"], item_id)
    return RedirectResponse("/watchlist", status_code=303)


# --- School Guide ---

MAX_COMPARE_AREAS = 4


def _parse_areas_param(raw: str) -> list[dict]:
    areas = []
    for chunk in raw.split("|"):
        parts = chunk.split(",", 2)
        if len(parts) != 3:
            continue
        try:
            areas.append({"latitude": float(parts[0]), "longitude": float(parts[1]), "label": parts[2]})
        except ValueError:
            continue
    return areas


def _areas_param(areas: list[dict]) -> str:
    return "|".join(f"{a['latitude']},{a['longitude']},{a['label']}" for a in areas)


SCHOOL_ADMISSION_CACHE_TTL_S = 86400 * 7
AREA_VS_CACHE_TTL_S = 86400 * 7


DISTRICT_PRICES_CACHE_TTL_S = 86400
DISTRICT_PRICES_SHOWN = 25


def _district_price_table() -> dict:
    """What homes actually sell for, district by district, from the
    medians already computed for every area guide.

    Nobody publishes outcode-level medians from Land Registry for free.
    The local authority figures everyone quotes cover whole cities, so
    "Manchester" is one number for eighty thousand very different
    houses. This reads the medians the area guides already hold, so it
    costs nothing extra and grows as the prewarm job works through the
    country.
    """
    prefix = f"area_guide:{AREA_GUIDE_PAYLOAD_VERSION}:"
    rows = []
    with db.get_session() as session:
        for entry in session.scalars(select(PageCache)).all():
            key = entry.cache_key or ""
            if not key.startswith(prefix):
                continue
            try:
                payload = json.loads(entry.value)
            except (TypeError, ValueError):
                continue
            sales = payload.get("local_sales") or {}
            if not sales.get("enough_for_median"):
                continue
            outcode = key[len(prefix):]
            hpi_local = (payload.get("hpi") or {}).get("local_authority") or {}
            rows.append({
                "outcode": outcode,
                "median": sales["median"],
                "count": sales["count"],
                "low": sales.get("low"),
                "high": sales.get("high"),
                "district": hpi_local.get("name") or "",
            })

    rows.sort(key=lambda r: r["median"])
    return {
        "total": len(rows),
        "cheapest": rows[:DISTRICT_PRICES_SHOWN],
        "dearest": list(reversed(rows[-DISTRICT_PRICES_SHOWN:])),
        "median_of_medians": rows[len(rows) // 2]["median"] if rows else None,
    }


@app.get("/market/district-prices")
async def district_prices(request: Request):
    """A ranked table of what homes really sell for by postcode
    district. Refreshes as the area guides do, so it is never a
    hand-written article going stale the day after publication."""
    context = base_context(request)
    cached = _cache.get(("district_prices",), DISTRICT_PRICES_CACHE_TTL_S)
    if cached is None:
        cached = await asyncio.to_thread(_district_price_table)
        _cache.set(("district_prices",), cached)
    context.update(cached)
    context["generated_date"] = datetime.date.today().strftime("%d %B %Y")
    context["canonical_url"] = f"{_public_base_url(request)}/market/district-prices"
    return templates.TemplateResponse(request, "district_prices.html", context)


def _tool_jsonld(name: str, description: str, url: str) -> str:
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "WebApplication",
        "name": name,
        "description": description,
        "url": url,
        "applicationCategory": "FinanceApplication",
        "operatingSystem": "Any",
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "GBP"},
    }, separators=(",", ":"))


# The report has carried a stamp duty and mortgage calculator since the
# start, but inside a dialog on a noindex page, where nothing could ever
# find it. These are the same maths on a page of their own, which is a
# search anyone buying a house makes and a page other sites link to
# without being asked.
TOOLS = {
    "stamp-duty-calculator": {
        "key": "stamp-duty",
        "title": "Stamp Duty Calculator 2026: England, Scotland and Wales",
        "heading": "Stamp duty calculator",
        "meta": "Work out stamp duty (SDLT), Scotland's LBTT or Wales's LTT on any purchase price, including first-time buyer relief and the additional property surcharge. Free, no sign-up.",
        "dek": "Enter a price and it works out the tax, including first-time buyer relief and the extra charge on second homes. Nothing leaves your browser.",
        "default_rate": "4.5",
        "disclaimer": (
            "An estimate, calculated in your browser from published rates as of April 2025. "
            "Budgets change these, so confirm the exact figure on "
            '<a href="https://www.gov.uk/stamp-duty-land-tax" rel="noopener" target="_blank">gov.uk</a>, '
            '<a href="https://www.revenue.scot/land-buildings-transaction-tax" rel="noopener" target="_blank">Revenue Scotland</a> '
            'or <a href="https://www.gov.uk/land-transaction-tax" rel="noopener" target="_blank">the Welsh Revenue Authority</a> '
            "before you exchange. Not financial advice."
        ),
        "explainer_heading": "How stamp duty actually works",
        "explainer": [
            "The tax is charged in slices, not all at one rate. A £400,000 purchase in England does "
            "not attract one percentage on the whole amount: the first slice is taxed at nothing, "
            "the next at 2%, the next at 5%, and only the part above each threshold is charged at "
            "the higher rate. That is why the effective rate above is lower than the headline band.",
            "The three nations run separate taxes. England and Northern Ireland charge Stamp Duty "
            "Land Tax, Scotland charges Land and Buildings Transaction Tax, and Wales charges Land "
            "Transaction Tax. The thresholds differ in all three, so the same price produces three "
            "different bills.",
            "First-time buyer relief applies only up to a ceiling, and disappears entirely above it "
            "rather than tapering. Buying an additional property adds a surcharge on top of the "
            "whole amount, which is usually the largest single surprise in a buy-to-let sum.",
        ],
        "faqs": [
            ("When do I pay it?",
             "Within 14 days of completion in England and Northern Ireland, and 30 days in Scotland "
             "and Wales. In practice your solicitor files and pays it out of the funds you send them, "
             "so you need the money available at completion, not afterwards."),
            ("Do first-time buyers pay nothing?",
             "Only below the relief threshold, and only if every buyer is a first-time buyer. Above "
             "the ceiling the relief vanishes completely and standard rates apply to the whole price."),
            ("Does the surcharge apply if I am replacing my main home?",
             "Not if you sell your previous main residence at the same time. If the sale completes "
             "later you usually pay the surcharge up front and reclaim it, within a time limit."),
            ("Is the calculator's figure the exact amount?",
             "Treat it as an estimate. Rates change at Budgets, and unusual purchases such as shared "
             "ownership, mixed-use property or company buyers follow different rules entirely."),
        ],
    },
    "mortgage-calculator": {
        "key": "mortgage",
        "title": "Mortgage Repayment Calculator: monthly cost on any UK price",
        "heading": "Mortgage repayment calculator",
        "meta": "Work out the monthly repayment on a UK mortgage from the purchase price, deposit, interest rate and term. Free, instant, nothing stored.",
        "dek": "Purchase price, deposit, rate and term in; the monthly repayment out. Calculated in your browser, nothing is sent anywhere.",
        "default_rate": "4.5",
        "disclaimer": (
            "An estimate on a standard repayment mortgage. A lender's own figure will differ with "
            "fees, the exact product and how they assess affordability. Not financial advice, and "
            "we are not a broker."
        ),
        "explainer_heading": "What the number does and does not include",
        "explainer": [
            "This is the capital-and-interest repayment on the amount borrowed, spread evenly over "
            "the term. Early payments are mostly interest and later ones mostly capital, which is "
            "why overpaying in the first years saves disproportionately more than overpaying later.",
            "It does not include the things that arrive alongside it: buildings insurance, ground "
            "rent and service charge on a leasehold flat, council tax, or the energy bill, which on "
            "a poorly rated property can exceed the difference between two mortgage rates.",
            "Almost nobody keeps the same rate for the whole term. A fixed rate ends after two or "
            "five years and the loan moves to whatever is available then, so it is worth checking "
            "the payment at a rate two or three points higher than today's before committing.",
        ],
        "faqs": [
            ("How much deposit do I need?",
             "Five percent is the usual minimum, but rates improve in steps at 10%, 15% and 25%. "
             "Moving from a 5% to a 10% deposit often cuts the rate enough to change the monthly "
             "payment by more than the extra deposit costs per month."),
            ("Why is a lender offering me less than this suggests?",
             "Affordability is assessed on income, outgoings and a stressed interest rate well above "
             "the one you are quoted, not on the monthly figure alone."),
            ("Should I take a longer term to lower the payment?",
             "It lowers the monthly cost and raises the total interest substantially. Both are true "
             "at once, and which matters more depends on your circumstances rather than on arithmetic."),
        ],
    },
}


@app.get("/tools/{slug}")
def tool_page(request: Request, slug: str):
    tool = TOOLS.get(slug)
    context = base_context(request)
    if tool is None:
        return templates.TemplateResponse(request, "404.html", context, status_code=404)
    url = f"{_public_base_url(request)}/tools/{slug}"
    context["tool"] = {
        **tool,
        "jsonld": _tool_jsonld(tool["heading"], tool["meta"], url),
        "faq_jsonld": _faq_jsonld(tool["faqs"]),
    }
    context["canonical_url"] = url
    return templates.TemplateResponse(request, "tool_calculator.html", context)


@app.get("/compare/{left}/vs/{right}")
async def area_versus(request: Request, left: str, right: str):
    """One district against another.

    Nobody picks an area in isolation; they pick between two, and they
    search for it that way ("didsbury or chorlton", "M20 vs M21"). The
    logged-in compare view answered this for saved properties only, and
    /compare answers it for a form submission that search engines never
    see. This is the same answer as a page that can be linked and found.

    Restricted to genuine neighbours: any two of 2,943 districts is four
    million pages, virtually all of them comparisons nobody would make.
    """
    left, right = left.strip().upper(), right.strip().upper()
    context = base_context(request)

    if not (_OUTCODE_RE.match(left) and _OUTCODE_RE.match(right)) or left == right:
        return templates.TemplateResponse(request, "404.html", context, status_code=404)
    # One page per pair, not two: A vs B and B vs A are the same
    # comparison and would compete with each other in search.
    if left > right:
        return RedirectResponse(f"/compare/{right}/vs/{left}", status_code=301)
    if right not in _neighbour_outcodes(left):
        return templates.TemplateResponse(request, "404.html", context, status_code=404)

    cache_key = ("area_vs", AREA_GUIDE_PAYLOAD_VERSION, left, right)
    cached = await asyncio.to_thread(_cache.get_persistent, cache_key, AREA_VS_CACHE_TTL_S)
    if cached is None:
        # A district is not a postcode, and lookup_postcode only takes a
        # real one; resolve each side to an actual postcode inside it
        # first, the same way the area guide does.
        places = await asyncio.gather(
            _resolve_extension_location(left), _resolve_extension_location(right),
            return_exceptions=True,
        )
        if any(isinstance(p, Exception) or p is None or p[0] is None for p in places):
            return templates.TemplateResponse(request, "404.html", context, status_code=404)
        # The summary's own average comes from one postcode inside the
        # district and is often empty. The district-wide median, from
        # the same query the area guides use, is the figure worth
        # comparing and is present far more often.
        sides = await asyncio.gather(
            _comparison_summary(places[0][0]["postcode"], ""),
            _comparison_summary(places[1][0]["postcode"], ""),
            _bounded(_outcode_sales(places[0][0]["latitude"], places[0][0]["longitude"]), 8.0),
            _bounded(_outcode_sales(places[1][0]["latitude"], places[1][0]["longitude"]), 8.0),
            return_exceptions=True,
        )
        if any(isinstance(s, Exception) for s in sides[:2]):
            return templates.TemplateResponse(request, "404.html", context, status_code=404)
        for summary, sales in ((sides[0], sides[2]), (sides[1], sides[3])):
            if isinstance(sales, dict) and sales.get("enough_for_median"):
                summary["local_median"] = sales["median"]
                summary["local_sales_count"] = sales["count"]
        cached = {"left": sides[0], "right": sides[1]}
        await asyncio.to_thread(_cache.set_persistent, cache_key, cached)

    context["canonical_url"] = f"{_public_base_url(request)}/compare/{left}/vs/{right}"
    context["left_code"], context["right_code"] = left, right
    context["columns"] = [
        {"postcode": left, "house_number": "", "summary": cached["left"], "outcode": left},
        {"postcode": right, "house_number": "", "summary": cached["right"], "outcode": right},
    ]
    context["versus"] = True
    context["anonymous_compare"] = False
    context["differences"] = _versus_differences(left, right, cached["left"], cached["right"])
    context["versus_faqs"] = _versus_faqs(left, right, cached["left"], cached["right"])
    context["versus_faqs_jsonld"] = _faq_jsonld(context["versus_faqs"])
    return templates.TemplateResponse(request, "compare.html", context)


def _versus_faqs(left: str, right: str, a: dict, b: dict):
    """The questions someone comparing two districts actually types.

    Answered from the two summaries rather than in general terms, and
    only where both sides hold the figure: a comparison with a gap on
    one side is not a comparison.
    """
    faqs = []
    la, lb = a.get("local_median") or a.get("avg_price"), b.get("local_median") or b.get("avg_price")
    if la and lb:
        cheaper = left if la < lb else right
        faqs.append((
            f"Is {left} or {right} cheaper?",
            f"{cheaper} is the cheaper of the two. Homes around {left} sell for about "
            f"£{la:,.0f} and around {right} for about £{lb:,.0f}, from HM Land Registry "
            "records of what actually changed hands.",
        ))
    ca, cb = a.get("crime_total"), b.get("crime_total")
    if ca is not None and cb is not None:
        quieter = left if ca < cb else right
        faqs.append((
            f"Which has less crime, {left} or {right}?",
            f"{quieter} recorded fewer crimes in the same period ({ca} in {left} against "
            f"{cb} in {right}, Police.uk). Busier places record more, so read this "
            "alongside how built-up each area is rather than on its own.",
        ))
    da, db = a.get("imd_decile"), b.get("imd_decile")
    if da and db:
        faqs.append((
            f"Is {left} or {right} the more affluent area?",
            f"On the Index of Multiple Deprivation, {left} sits in decile {da} of 10 and "
            f"{right} in decile {db}, where 10 is the least deprived.",
        ))
    faqs.append((
        f"Should I buy in {left} or {right}?",
        "That depends on what you need, and no dataset answers it. What this page gives you "
        "is the evidence side by side: what homes actually sell for, the energy ratings, the "
        "flood zone, recorded crime and deprivation, each traced to the body that published it.",
    ))
    return faqs


def _neighbour_outcodes(outcode: str, limit: int = 8) -> list[str]:
    """The nearest districts by centre point. The comparison pages only
    exist for these, because "M20 vs M21" is a real question and "M20 vs
    IV27" is not."""
    here = next((o for o in ALL_OUTCODES if o["outcode"] == outcode), None)
    if here is None:
        return []
    ranked = sorted(
        ((o, _haversine_km(here["lat"], here["lon"], o["lat"], o["lon"])) for o in ALL_OUTCODES
         if o["outcode"] != outcode),
        key=lambda pair: pair[1],
    )
    return [o["outcode"] for o, _ in ranked[:limit]]


def _versus_differences(left: str, right: str, a: dict, b: dict) -> list[str]:
    """Plain sentences naming which district wins on what, so the page
    says something rather than leaving the reader to diff two columns.
    Only where both sides have the figure: a gap is not a finding."""
    out = []

    la, lb = a.get("local_median") or a.get("avg_price"), b.get("local_median") or b.get("avg_price")
    if la and lb and la != lb:
        cheaper, dearer = (left, right) if la < lb else (right, left)
        gap = abs(la - lb) / max(la, lb) * 100
        out.append(f"Homes sell for about {gap:.0f}% less in {cheaper} than in {dearer}.")

    ca, cb = a.get("crime_total"), b.get("crime_total")
    if ca is not None and cb is not None and ca != cb:
        quieter = left if ca < cb else right
        out.append(f"{quieter} recorded fewer crimes in the same period, though busier places always record more.")

    da, db = a.get("imd_decile"), b.get("imd_decile")
    if da and db and da != db:
        less = left if da > db else right
        out.append(f"{less} is the less deprived of the two on the Index of Multiple Deprivation.")

    ea, eb = a.get("energy_band"), b.get("energy_band")
    if ea and eb and ea != eb:
        out.append(f"The most recent EPC we hold is {ea} in {left} and {eb} in {right}.")
    return out


@app.get("/school/{urn}/{slug}")
async def school_admission_page(request: Request, urn: int, slug: str):
    """One school's real admission distance.

    "School catchment area for X" is one of the most searched property
    questions in the country, and the honest answer for most English
    schools is that no catchment area exists: places are offered
    outward from the school until they run out. What does exist is how
    far the last child admitted actually lived, which each authority
    publishes and almost nobody surfaces. That number is what this page
    is built around, described as what it is rather than drawn as a
    boundary on a map.

    Only the ~3,200 schools with a genuine published figure get a page.
    """
    profile = await asyncio.to_thread(schools_db.admission_profile, urn)
    context = base_context(request)
    if profile is None:
        return templates.TemplateResponse(request, "404.html", context, status_code=404)

    base = _public_base_url(request)
    canonical_path = f"/school/{urn}/{profile['slug']}"
    if slug != profile["slug"]:
        return RedirectResponse(canonical_path, status_code=301)

    context["canonical_url"] = f"{base}{canonical_path}"
    context["school"] = profile
    context["nearby_areas"] = await asyncio.to_thread(
        _outcodes_within, profile["latitude"], profile["longitude"], profile["miles"]
    )
    # The three questions the page answers on screen, as structured data.
    # Phrased exactly as someone would search them.
    context["school_faqs_jsonld"] = _faq_jsonld([
        (f"What is the catchment area for {profile['name']}?",
         "It does not have one in the sense most people mean. Like most English schools, "
         "when it is oversubscribed it offers places outward from the school until they run "
         f"out. The furthest child admitted in {profile['academic_year']} lived "
         f"{profile['miles']} miles away, according to {profile['authority']}."),
        (f"How close do I need to live to get into {profile['name']}?",
         f"{profile['miles']} miles was enough in {profile['academic_year']}, which makes it "
         "the best evidence available rather than a promise about next year. The distance "
         "moves every year with the number of applications."),
        (f"Does buying a house near {profile['name']} guarantee a place?",
         "No. Admission criteria usually put looked-after children, siblings and sometimes "
         "faith or aptitude criteria ahead of distance, so places can be filled before "
         "distance is reached at all."),
    ])
    context["school_jsonld"] = json.dumps({
        "@context": "https://schema.org",
        "@type": "School",
        "name": profile["name"],
        "url": context["canonical_url"],
        "address": {
            "@type": "PostalAddress",
            "streetAddress": profile.get("street") or None,
            "addressLocality": profile.get("town") or None,
            "postalCode": profile.get("postcode") or None,
            "addressCountry": "GB",
        },
        "geo": {"@type": "GeoCoordinates", "latitude": profile["latitude"], "longitude": profile["longitude"]},
    }, separators=(",", ":"))
    return templates.TemplateResponse(request, "school_admission.html", context)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _outcodes_within(lat: float, lon: float, miles: float) -> list[dict]:
    """Postcode districts whose centre falls inside the school's last
    admitted distance. Not a promise of a place: a district's centre
    being in range says nothing about a specific address, which is the
    whole point of checking one."""
    km = (miles or 0) * 1.60934
    if km <= 0:
        return []
    out = []
    for entry in ALL_OUTCODES:
        d = _haversine_km(lat, lon, entry["lat"], entry["lon"])
        if d <= km:
            out.append({
                "outcode": entry["outcode"],
                "district": entry.get("district", ""),
                # Shown in miles, like every other distance on the site.
                "miles": round(d / 1.60934, 1),
            })
    out.sort(key=lambda e: e["miles"])
    return out[:12]


@app.get("/schools/guide")
async def schools_guide(request: Request, q: str = "", areas: str = ""):
    context = base_context(request)
    context["query"] = q

    area_list = _parse_areas_param(areas)

    if q:
        resolved = await place_search.resolve(q)
        if resolved is None:
            context["search_error"] = True
        elif any(
            abs(a["latitude"] - resolved["latitude"]) < 0.001 and abs(a["longitude"] - resolved["longitude"]) < 0.001
            for a in area_list
        ):
            context["search_duplicate"] = True
        else:
            area_list.append(resolved)

    area_list = area_list[:MAX_COMPARE_AREAS]

    context["national_baseline"] = await asyncio.to_thread(schools_db.national_baseline)

    areas_with_stats = []
    for i, area in enumerate(area_list):
        landscape = await asyncio.to_thread(schools_db.school_landscape, area["latitude"], area["longitude"])
        remaining = area_list[:i] + area_list[i + 1:]
        label = (area.get("label") or "").strip().upper()
        areas_with_stats.append({
            **area, "landscape": landscape, "remove_areas_param": _areas_param(remaining),
            # A search that was a postcode district gets a link to its area
            # guide; a town or full postcode doesn't have one.
            "outcode": label if _OUTCODE_RE.match(label) else None,
        })
    context["areas"] = areas_with_stats
    context["areas_param"] = _areas_param(area_list)
    context["can_add_more"] = len(area_list) < MAX_COMPARE_AREAS

    # A one-district guide is a real page worth ranking: /schools/guide?q=M1
    # carries 40,000 words of Ofsted detail found nowhere else on the site.
    # The default canonical drops the query string, which pointed all of
    # them at the 3,700-word landing page and told Google to index that
    # instead - so none of them could ever rank for "schools in M1".
    #
    # Only the single-district case gets its own canonical. A two-to-four
    # area comparison is combinatorial (2,943 districts choose 4) and a
    # free-text search resolves to whatever a geocoder returns, so both
    # keep folding into the landing page rather than opening the index up
    # to an unbounded set of URLs. Normalized to the uppercase outcode, so
    # "m1", "M1" and " M1 " are one URL rather than three.
    if len(areas_with_stats) == 1 and areas_with_stats[0]["outcode"] in KNOWN_OUTCODES:
        context["canonical_url"] = (
            f"{_public_base_url(request)}/schools/guide?q={areas_with_stats[0]['outcode']}"
        )
    # The landing state shows the full district index under the search
    # box, like /areas does; the results page does not carry it.
    if not areas_with_stats:
        context["regions"] = _area_index()
        context["total"] = len(ALL_OUTCODES)

    return templates.TemplateResponse(request, "schools_guide.html", context)


# --- School shortlist ---


@app.get("/schools/shortlist")
def school_shortlist_view(request: Request):
    context = base_context(request)
    if not context["current_user"]:
        return RedirectResponse("/login?next=/schools/shortlist", status_code=303)
    context["items"] = school_shortlist.list_items(context["current_user"]["id"])
    return templates.TemplateResponse(request, "school_shortlist.html", context)


@app.post("/schools/shortlist/save")
def school_shortlist_save(
    request: Request, urn: int = Form(...), postcode: str = Form(...), note: str = Form("")
):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse(f"/login?next=/property?postcode={postcode}", status_code=303)
    school_shortlist.save_item(user["id"], urn, note.strip())
    return RedirectResponse(f"/property?postcode={postcode}#schools", status_code=303)


@app.post("/schools/shortlist/remove")
def school_shortlist_remove(request: Request, item_id: int = Form(...)):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse("/login?next=/schools/shortlist", status_code=303)
    school_shortlist.remove_item(user["id"], item_id)
    return RedirectResponse("/schools/shortlist", status_code=303)


# --- Reviews ---


@app.post("/reviews/submit")
def reviews_submit(
    request: Request, target_type: str = Form(...), target_key: str = Form(...),
    rating: int = Form(...), body: str = Form(""), next: str = Form("/"),
):
    safe_next = _safe_next(next)
    user = auth.current_user(request)
    if not user:
        return RedirectResponse(f"/login?next={quote(safe_next, safe='')}", status_code=303)
    if target_type not in ("property", "school") or not (1 <= rating <= 5):
        return RedirectResponse(safe_next, status_code=303)
    reviews.submit(user["id"], target_type, target_key, rating, body)
    return RedirectResponse(safe_next, status_code=303)
