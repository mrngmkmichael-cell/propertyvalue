import asyncio
import datetime
import os
import re
from urllib.parse import quote, urlencode

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import auth, db, school_shortlist, watchlist
from app.models import User
from app.services import (
    air_quality, amenities, area_stats, broadband, catchment, census_stats, crime, demographics, designations, epc,
    flood, flood_zones, food_hygiene, google_places, heritage, historic_landfill, hpi, mobile_coverage, noise,
    orientation, radon, rental, schools_db, valuation,
)
from app.services.land_registry import sold_prices_for_postcode, sold_prices_for_postcodes
from app.services.postcodes import lookup_postcode, nearby_postcodes

load_dotenv()

app = FastAPI(title="PropertyValue")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "dev-only-insecure-secret"),
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def _format_gbp(value) -> str:
    try:
        return f"£{int(float(value)):,}"
    except (TypeError, ValueError):
        return str(value)


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
    (
        tx_result, epc_flow_result, flood_result, crime_result, district_crime_result,
        amenities_result, hpi_result, noise_result,
        schools_result, deprivation_result, income_result,
        occupation_result, qualification_result, broadband_result, mobile_result,
        radon_result, heritage_result, comparables_result,
        age_profile_result, housing_result, background_result, wellbeing_result, rental_result,
        designations_result, food_hygiene_result, flood_zone_result, google_ratings_result,
        orientation_result, air_quality_result, historic_landfill_result, catchment_result,
    ) = await asyncio.gather(
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
        return_exceptions=True,
    )

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

    if isinstance(schools_result, Exception):
        context["schools_error"] = True
    else:
        context["schools"] = schools_result
        context["schools_total"] = sum(len(v) for v in schools_result.values())

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

    # MEES compliance + lead-plumbing era, both computed from EPC data
    # already fetched above - no extra API calls needed.
    if context.get("certificates"):
        rating = context["certificates"][0].get("rating", "")
        context["mees_compliant"] = (rating not in ("F", "G")) if rating else None
    if context.get("property_detail", {}).get("year_built"):
        context["lead_plumbing_era"] = _likely_pre_1970(context["property_detail"]["year_built"])

    if context["current_user"]:
        try:
            context["watchlist_item"] = watchlist.get_item(
                context["current_user"]["id"], canonical, house_number
            )
        except Exception:
            context["watchlist_item"] = None
        try:
            context["shortlisted_urns"] = {
                item["urn"] for item in school_shortlist.list_items(context["current_user"]["id"])
            }
        except Exception:
            context["shortlisted_urns"] = set()

    return templates.TemplateResponse(request, "property.html", context)


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


# --- Accounts ---


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

        user = User(email=email, password_hash=auth.hash_password(password))
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

    return RedirectResponse(next or "/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


# --- Watchlist ---


@app.get("/watchlist")
def watchlist_view(request: Request):
    context = base_context(request)
    if not context["current_user"]:
        return RedirectResponse("/login?next=/watchlist", status_code=303)
    context["items"] = watchlist.list_items(context["current_user"]["id"])
    return templates.TemplateResponse(request, "watchlist.html", context)


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
