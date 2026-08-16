import asyncio
import os

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
from app.services import amenities, area_stats, broadband, census_stats, crime, epc, flood, hpi, noise, schools_db
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


async def _empty_list():
    return []


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
    return f"{int(m)} m" if m < 1000 else f"{m / 1000:.1f} km"


templates.env.filters["gbp"] = _format_gbp
templates.env.filters["distance"] = _format_distance


@app.on_event("startup")
def on_startup():
    db.init_db()


def base_context(request: Request) -> dict:
    return {
        "current_user": auth.current_user(request),
        "accounts_configured": db.is_configured(),
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
        tx_result, epc_result, flood_result, crime_result, district_crime_result,
        amenities_result, hpi_result, noise_result,
        schools_result, deprivation_result, income_result,
        occupation_result, qualification_result, broadband_result,
    ) = await asyncio.gather(
        sold_prices_for_postcode(canonical),
        epc.certificates_for_postcode(canonical) if context["epc_configured"] else _empty_list(),
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
        return_exceptions=True,
    )

    if isinstance(tx_result, Exception):
        context["tx_error"] = True
    else:
        context["avg_price"] = _average_amount(tx_result)
        context["transactions"] = _filter_by_address(tx_result, house_number)
        context["postcode_has_transactions"] = bool(tx_result)

    if context["epc_configured"]:
        if isinstance(epc_result, Exception):
            context["epc_error"] = True
        else:
            context["certificates"] = _filter_by_address(epc_result, house_number)
            context["postcode_has_certificates"] = bool(epc_result)
            if context["certificates"]:
                try:
                    context["property_detail"] = await epc.certificate_detail(
                        context["certificates"][0]["certificate_number"]
                    )
                except httpx.HTTPError:
                    pass

    if isinstance(flood_result, Exception):
        context["flood_error"] = True
    else:
        context["flood_warnings"] = flood_result

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

    if context["current_user"]:
        try:
            context["watchlist_item"] = watchlist.get_item(context["current_user"]["id"], canonical)
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
        transactions = await sold_prices_for_postcodes([p["postcode"] for p in nearby])

        for tx in transactions:
            tx["distance_m"] = distance_by_postcode.get(tx["postcode"])
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
def watchlist_save(request: Request, postcode: str = Form(...), note: str = Form("")):
    user = auth.current_user(request)
    if not user:
        return RedirectResponse(f"/login?next=/property?postcode={postcode}", status_code=303)
    watchlist.save_item(user["id"], postcode, note.strip())
    return RedirectResponse(f"/property?postcode={postcode}", status_code=303)


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
