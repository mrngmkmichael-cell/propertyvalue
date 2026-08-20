import asyncio
import datetime
import hmac
import json
import logging
import os
import re
import secrets
from urllib.parse import quote, urlencode

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select

from app import auth, db, school_shortlist, watchlist
from app.services import _cache
from app.models import User
from app.services import (
    air_quality, amenities, area_stats, broadband, catchment, census_stats, clay_risk, coal_mining, cqc_ratings,
    crime, demographics, designations, email as email_service, epc, flood, flood_zones, food_hygiene,
    google_places, heritage, historic_landfill, hpi, mobile_coverage, noise, orientation, overview_score,
    pdf_export, place_search, radon, rental, reviews, routing, schools_db, sewage_discharge, stripe_billing,
    surface_water_risk, valuation,
)
from app.services.land_registry import sold_prices_for_postcode, sold_prices_for_postcodes
from app.services.postcodes import lookup_postcode, nearby_postcodes, outcode_centroid

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


app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")
# Cache-busting query string for static assets, tied to the CSS
# file's own mtime - without this, browsers (and Render's static
# asset caching) can keep serving a stale stylesheet indefinitely
# after a deploy, which happened repeatedly during development.
try:
    templates.env.globals["css_version"] = int(os.path.getmtime("app/static/css/style.css"))
except OSError:
    templates.env.globals["css_version"] = 0


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
        summary["avg_price"] = _average_amount(_filter_by_address(tx_result, house_number))

    if not isinstance(epc_flow_result, Exception) and epc_configured:
        _, property_detail, _ = epc_flow_result
        if property_detail:
            summary["dwelling_type"] = property_detail.get("dwelling_type")
            summary["floor_area"] = property_detail.get("total_floor_area")
            summary["year_built"] = property_detail.get("year_built")

    if not isinstance(flood_zone_result, Exception) and flood_zone_result:
        summary["flood_zone"] = flood_zone_result["label"]

    if not isinstance(crime_result, Exception) and crime_result:
        summary["crime_total"] = crime_result.get("total")

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
            changes.append(f"Area house-price trend flipped — {direction} ({new_growth:+.1f}% YoY)")

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


@app.on_event("startup")
def on_startup():
    db.init_db()


def base_context(request: Request) -> dict:
    return {
        "current_user": auth.current_user(request),
        "accounts_configured": db.is_configured(),
        "google_maps_api_key": os.environ.get("GOOGLE_MAPS_API_KEY", ""),
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
    return templates.TemplateResponse(
        request, "500.html", base_context(request), status_code=500
    )


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", base_context(request))


# A starting seed of major UK city/town postcode districts, not an
# exhaustive list (there are ~11,000 outcodes nationally) - /area/{outcode}
# works for any valid one regardless of sitemap inclusion, this just
# gives crawlers a fast, curated starting point. Worth growing over
# time (e.g. from real search/watchlist activity) rather than trying
# to enumerate the whole country in one go.
AREA_GUIDE_SEED_OUTCODES = [
    "EC1A", "EC2A", "EC3A", "EC4A", "W1A", "WC1A", "WC2A", "SW1A", "SW3", "SW7", "SE1", "N1", "E1", "E14", "NW1", "NW3",
    "M1", "M2", "M3", "M4", "M15", "M20",
    "B1", "B2", "B3", "B15", "B16",
    "LS1", "LS2", "LS6",
    "L1", "L2", "L18",
    "S1", "S2", "S10",
    "NE1", "NE2",
    "BS1", "BS8",
    "BN1", "BN2",
    "CB1", "CB2",
    "OX1", "OX2", "OX4",
    "CF10", "CF11", "CF24",
    "EH1", "EH2", "EH3", "EH6",
    "G1", "G2", "G3", "G12",
    "AB10", "AB11", "AB24",
    "DD1", "DD2",
    "BT1", "BT9",
    "SO14", "SO15",
    "PO1", "PO5",
    "NG1", "NG7",
    "LE1", "LE2",
    "CV1", "CV3",
    "PL1", "PL4",
    "EX1", "EX4",
    "YO1", "YO10",
    "DE1", "DE22",
    "NR1", "NR2",
    "RG1", "RG6",
    "SN1", "SN3",
    "GL1", "GL50",
    "CT1", "CT2",
    "ME1", "ME4",
]


@app.get("/sitemap.xml")
def sitemap(request: Request):
    base = _public_base_url(request)
    static_paths = ["/", "/methodology", "/premium", "/schools/guide", "/privacy", "/terms", "/support"]
    urls = [f"{base}{p}" for p in static_paths] + [f"{base}/area/{o}" for o in AREA_GUIDE_SEED_OUTCODES]
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls)
        + "</urlset>"
    )
    return Response(content=body, media_type="application/xml")


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


@app.get("/property")
async def property_search(request: Request, postcode: str = "", house_number: str = ""):
    postcode = postcode.strip()
    house_number = house_number.strip()
    context = base_context(request)
    context["query"] = postcode
    context["house_number"] = house_number

    if not postcode:
        return templates.TemplateResponse(request, "property.html", context)

    try:
        location = await lookup_postcode(postcode)
    except httpx.HTTPError:
        context["error"] = "lookup_error"
        return templates.TemplateResponse(request, "property.html", context)

    if location is None:
        context["error"] = "not_found"
        return templates.TemplateResponse(request, "property.html", context)

    context["location"] = location
    context["active_tab"] = "summary"
    canonical = location["postcode"]
    lat, lon = location["latitude"], location["longitude"]
    codes = location.get("codes", {})
    context["epc_configured"] = epc.is_configured()

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
    gather_results = _cache.get(gather_cache_key, PROPERTY_SEARCH_CACHE_TTL_S)
    if gather_results is None:
        gather_results = await asyncio.gather(
            sold_prices_for_postcode(canonical),
            _epc_flow(canonical, house_number, context["epc_configured"]),
            flood.warnings_near(lat, lon),
            crime.summary_near(lat, lon),
            crime.summary_for_outcode(location["outcode"]),
            amenities.nearby_amenities_and_station(lat, lon),
            hpi.area_comparison(location["admin_district"], location["region"], location.get("country", "")),
            noise.noise_near(lat, lon),
            asyncio.to_thread(schools_db.nearby_schools, lat, lon),
            asyncio.to_thread(area_stats.deprivation_for_lsoa, codes.get("lsoa", "")),
            asyncio.to_thread(area_stats.income_for_msoa, codes.get("msoa", "")),
            asyncio.to_thread(census_stats.occupation_for_lsoa, codes.get("lsoa", "")),
            asyncio.to_thread(census_stats.qualification_for_lsoa, codes.get("lsoa", "")),
            asyncio.to_thread(broadband.coverage_for_postcode, canonical),
            asyncio.to_thread(mobile_coverage.coverage_for_laua, codes.get("admin_district", "")),
            radon.risk_near(lat, lon),
            heritage.nearby_listed_buildings(lat, lon),
            _nearby_comparables(lat, lon),
            asyncio.to_thread(demographics.age_profile_for_lsoa, codes.get("lsoa", "")),
            asyncio.to_thread(demographics.housing_for_lsoa, codes.get("lsoa", "")),
            asyncio.to_thread(demographics.background_for_lsoa, codes.get("lsoa", "")),
            asyncio.to_thread(demographics.wellbeing_for_lsoa, codes.get("lsoa", "")),
            asyncio.to_thread(rental.rental_for_laua, codes.get("admin_district", "")),
            designations.check_all(lat, lon),
            food_hygiene.nearby_ratings(lat, lon),
            flood_zones.zone_for(lat, lon),
            google_places.nearby_food_ratings(lat, lon),
            orientation.orientation_for(lat, lon),
            asyncio.to_thread(air_quality.for_location, location.get("eastings"), location.get("northings")),
            historic_landfill.check_near(lat, lon),
            catchment.catchments_for(lat, lon),
            asyncio.to_thread(schools_db.school_landscape, lat, lon),
            hpi.price_trend(location["admin_district"]),
            clay_risk.risk_near(lat, lon),
            sewage_discharge.nearby_outfalls(lat, lon),
            coal_mining.check_near(lat, lon),
            surface_water_risk.risk_for(lat, lon),
            cqc_ratings.nearby_ratings(lat, lon, canonical),
            return_exceptions=True,
        )
        _cache.set(gather_cache_key, gather_results)

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
    else:
        context["amenities"] = amenities_result["categories"]
        context["stations"] = amenities_result["stations"]
        context["stations_list"] = amenities_result["stations_list"]
        context["nearest_transport"] = min(
            amenities_result["stations"].values(), key=lambda s: s["distance_m"], default=None
        )

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

    premium_unlocked = bool(context["current_user"] and context["current_user"].get("is_premium"))
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

    return templates.TemplateResponse(request, "property.html", context)


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

    return JSONResponse(payload, headers=_EXTENSION_CORS_HEADERS)


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
        return await lookup_postcode(postcode), False
    centroid = await outcode_centroid(postcode)
    if not centroid:
        return None, True
    nearby = await nearby_postcodes(centroid["latitude"], centroid["longitude"], radius_m=800, limit=1)
    if not nearby:
        return None, True
    return await lookup_postcode(nearby[0]["postcode"]), True


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
        premium_unlocked = bool(token_user and token_user.is_premium)

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
    if not token_user or not token_user.is_premium:
        return JSONResponse({"error": "premium_required"}, status_code=403, headers=_EXTENSION_CORS_HEADERS)

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
    """A one-page shareable summary - the thing a buyer can actually
    hand to a solicitor or mortgage broker, unlike a link to a live
    interactive page. Deliberately NOT a dump of all 37 dashboard cards:
    reuses _comparison_summary's already-curated, already-cached field
    set (the same one the watchlist page shows) rather than the site's
    full ~28-service gather, since this is meant to read as a clean
    reference document, not a second copy of the whole report.

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

    summary = await _comparison_summary(postcode, house_number)
    if summary.get("not_found"):
        return RedirectResponse("/", status_code=303)

    html = templates.get_template("pdf_report.html").render({
        **summary,
        "generated_date": datetime.date.today().strftime("%d %B %Y"),
        "postcode_url": quote(summary["postcode"]),
        "house_number_url": quote(house_number),
    })
    pdf_bytes = pdf_export.html_to_pdf(html)
    if pdf_bytes is None:
        return RedirectResponse(f"/property?postcode={quote(postcode)}", status_code=303)

    filename = f"UKPropertyInsight-{summary['postcode'].replace(' ', '')}.pdf"
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_OUTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9]{1,2}[A-Z]?$", re.I)
AREA_GUIDE_CACHE_TTL_S = 86400  # public, crawler-facing - a day's staleness is a fair trade for not re-running this gather on every crawl hit


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

    cache_key = ("area_guide", outcode)
    cached = _cache.get(cache_key, AREA_GUIDE_CACHE_TTL_S)
    if cached is not None:
        context.update(cached)
        return templates.TemplateResponse(request, "area_guide.html", context)

    hpi_result, crime_result, landscape_result, flood_zone_result, deprivation_result, amenities_result = await asyncio.gather(
        hpi.area_comparison(location["admin_district"], location["region"], location.get("country", "")),
        crime.summary_for_outcode(outcode),
        asyncio.to_thread(schools_db.school_landscape, lat, lon),
        flood_zones.zone_for(lat, lon),
        asyncio.to_thread(area_stats.deprivation_for_lsoa, codes.get("lsoa", "")),
        amenities.nearby_amenities_and_station(lat, lon, lite=True),
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

    page_data = {
        "hpi": ok(hpi_result),
        "crime": ok(crime_result),
        "landscape": ok(landscape_result),
        "flood_zone": ok(flood_zone_result),
        "deprivation": ok(deprivation_result),
        "amenity_summary": amenity_summary,
        "has_data": any([ok(hpi_result), ok(crime_result), ok(landscape_result), ok(flood_zone_result)]),
    }
    _cache.set(cache_key, page_data)
    context.update(page_data)
    return templates.TemplateResponse(request, "area_guide.html", context)


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


@app.get("/premium")
def premium_info(request: Request, checkout: str = "", error: str = ""):
    context = base_context(request)
    context["billing_configured"] = stripe_billing.is_configured()
    context["plans"] = stripe_billing.plan_choices()
    context["checkout_cancelled"] = checkout == "cancelled"
    context["checkout_error"] = bool(error)
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
        if user_id and customer_id:
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


@app.get("/signup")
def signup_form(request: Request):
    return templates.TemplateResponse(request, "signup.html", base_context(request))


@app.post("/signup")
def signup_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    context = base_context(request)
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

    return RedirectResponse("/watchlist", status_code=303)


@app.get("/login")
def login_form(request: Request, next: str = "/"):
    context = base_context(request)
    context["next"] = next
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


# --- Watchlist ---


@app.get("/watchlist")
async def watchlist_view(request: Request):
    context = base_context(request)
    if not context["current_user"]:
        return RedirectResponse("/login?next=/watchlist", status_code=303)

    items = watchlist.list_items(context["current_user"]["id"])
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


def _watchlist_alert_email_html(entries: list[dict], watchlist_url: str) -> str:
    items_html = "".join(
        f'<li style="margin-bottom:14px;"><strong>{e["label"]}</strong>'
        f'<ul>{"".join(f"<li>{c}</li>" for c in e["changes"])}</ul></li>'
        for e in entries
    )
    return (
        '<div style="font-family:sans-serif;max-width:520px;margin:0 auto;">'
        "<h2>Your watchlist has updates</h2>"
        f'<ul style="list-style:none;padding:0;">{items_html}</ul>'
        f'<p><a href="{watchlist_url}">View your full watchlist →</a></p>'
        '<p style="color:#667085;font-size:12px;">'
        "You're getting this because these properties/areas are on your UKPropertyInsight watchlist. "
        f'Remove any of them any time from <a href="{watchlist_url}">your watchlist page</a>.'
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
            changes_by_email.setdefault(item["email"], []).append({"label": label, "changes": changes})

    watchlist_url = f"{_public_base_url(request)}/watchlist"
    notified = 0
    for to_email, entries in changes_by_email.items():
        sent = await email_service.send_email(
            to_email, "Changes to your UKPropertyInsight watchlist", _watchlist_alert_email_html(entries, watchlist_url)
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
        areas_with_stats.append({
            **area, "landscape": landscape, "remove_areas_param": _areas_param(remaining),
        })
    context["areas"] = areas_with_stats
    context["areas_param"] = _areas_param(area_list)
    context["can_add_more"] = len(area_list) < MAX_COMPARE_AREAS

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
