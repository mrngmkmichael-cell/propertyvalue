import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import auth, db, watchlist
from app.models import User
from app.services import crime, epc, flood
from app.services.land_registry import sold_prices_for_postcode
from app.services.postcodes import lookup_postcode

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


templates.env.filters["gbp"] = _format_gbp


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
    raise exc


@app.exception_handler(Exception)
async def server_error_handler(request: Request, exc: Exception):
    return templates.TemplateResponse(
        request, "500.html", base_context(request), status_code=500
    )


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", base_context(request))


@app.get("/property")
async def property_search(request: Request, postcode: str = ""):
    postcode = postcode.strip()
    context = base_context(request)
    context["query"] = postcode

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
    canonical = location["postcode"]

    try:
        context["transactions"] = await sold_prices_for_postcode(canonical)
    except httpx.HTTPError:
        context["tx_error"] = True

    context["epc_configured"] = epc.is_configured()
    if context["epc_configured"]:
        try:
            context["certificates"] = await epc.certificates_for_postcode(canonical)
        except httpx.HTTPError:
            context["epc_error"] = True

    lat, lon = location["latitude"], location["longitude"]

    try:
        context["flood_warnings"] = await flood.warnings_near(lat, lon)
    except httpx.HTTPError:
        context["flood_error"] = True

    try:
        context["crime"] = await crime.summary_near(lat, lon)
    except httpx.HTTPError:
        context["crime_error"] = True

    if context["current_user"]:
        try:
            context["watchlist_item"] = watchlist.get_item(context["current_user"]["id"], canonical)
        except Exception:
            context["watchlist_item"] = None

    return templates.TemplateResponse(request, "property.html", context)


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
