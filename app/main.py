import httpx
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.services.land_registry import sold_prices_for_postcode
from app.services.postcodes import lookup_postcode

app = FastAPI(title="PropertyValue")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def _format_gbp(value) -> str:
    try:
        return f"£{int(float(value)):,}"
    except (TypeError, ValueError):
        return str(value)


templates.env.filters["gbp"] = _format_gbp


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/property")
async def property_search(request: Request, postcode: str = ""):
    postcode = postcode.strip()
    context = {"query": postcode}

    if not postcode:
        return templates.TemplateResponse(request, "property.html", context)

    location = await lookup_postcode(postcode)
    if location is None:
        context["error"] = "not_found"
        return templates.TemplateResponse(request, "property.html", context)

    context["location"] = location

    try:
        context["transactions"] = await sold_prices_for_postcode(location["postcode"])
    except httpx.HTTPError:
        context["error"] = "api_error"

    return templates.TemplateResponse(request, "property.html", context)
