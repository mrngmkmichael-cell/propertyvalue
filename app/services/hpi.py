"""UK House Price Index comparison data (local authority, region,
country averages), from the same HM Land Registry SPARQL endpoint
as the sold-price history service - live, no key required.

The REST-style JSON endpoint (landregistry.data.gov.uk/data/ukhpi/...)
turned out unreliable for anything other than regions - local
authority and country resources return a self-reference instead of
inline data for reasons that aren't documented. SPARQL against the
same underlying dataset works consistently for all three levels, so
that's what this uses instead. Area names are matched with CONTAINS
rather than an exact string, since postcodes.io's admin_district
("Westminster") doesn't always match the HPI dataset's official
label ("City of Westminster") exactly.
"""
import asyncio
from datetime import date, timedelta

import httpx

SPARQL_ENDPOINT = "https://landregistry.data.gov.uk/landregistry/query"

_QUERY_TEMPLATE = """
prefix ukhpi: <http://landregistry.data.gov.uk/def/ukhpi/>
prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?refMonth ?averagePrice ?percentageAnnualChange WHERE {{
  ?obs ukhpi:refRegion ?region ;
       ukhpi:refMonth ?refMonth ;
       ukhpi:averagePrice ?averagePrice ;
       ukhpi:percentageAnnualChange ?percentageAnnualChange .
  ?region rdfs:label ?label .
  FILTER(LANG(?label) = "en")
  FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{name}")))
}}
ORDER BY DESC(?refMonth)
LIMIT 1
"""


async def _latest_for_area(client: httpx.AsyncClient, name: str) -> dict | None:
    if not name:
        return None
    query = _QUERY_TEMPLATE.format(name=name.replace('"', ""))
    try:
        response = await client.get(
            SPARQL_ENDPOINT,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    bindings = response.json()["results"]["bindings"]
    if not bindings:
        return None

    row = bindings[0]
    return {
        "name": name,
        "average_price": float(row["averagePrice"]["value"]),
        "annual_change_pct": float(row["percentageAnnualChange"]["value"]),
        "period": row["refMonth"]["value"],
    }


async def area_comparison(admin_district: str, region: str, country: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        local, reg, nat = await asyncio.gather(
            _latest_for_area(client, admin_district),
            _latest_for_area(client, region),
            _latest_for_area(client, country),
        )
    return {"local_authority": local, "region": reg, "country": nat}


_SERIES_QUERY_TEMPLATE = """
prefix ukhpi: <http://landregistry.data.gov.uk/def/ukhpi/>
prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?refMonth ?averagePrice WHERE {{
  ?obs ukhpi:refRegion ?region ;
       ukhpi:refMonth ?refMonth ;
       ukhpi:averagePrice ?averagePrice .
  ?region rdfs:label ?label .
  FILTER(LANG(?label) = "en")
  FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{name}")))
  FILTER(?refMonth >= "{cutoff}"^^<http://www.w3.org/2001/XMLSchema#gYearMonth>)
}}
ORDER BY ASC(?refMonth)
"""

MIN_TREND_POINTS = 24  # need at least 2 years of monthly data for a trend worth showing
PROJECTION_MONTHS = (12, 24)


def _linear_regression(points: list[tuple[int, float]]) -> tuple[float, float]:
    """Least-squares slope/intercept for (x, y) pairs - pure Python, no numpy."""
    n = len(points)
    sum_x = sum(p[0] for p in points)
    sum_y = sum(p[1] for p in points)
    sum_xy = sum(p[0] * p[1] for p in points)
    sum_xx = sum(p[0] * p[0] for p in points)
    denom = n * sum_xx - sum_x * sum_x
    if denom == 0:
        return 0.0, sum_y / n
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


async def price_trend(admin_district: str, years: int = 5) -> dict | None:
    if not admin_district:
        return None
    cutoff = (date.today() - timedelta(days=365 * years)).strftime("%Y-%m")
    query = _SERIES_QUERY_TEMPLATE.format(name=admin_district.replace('"', ""), cutoff=cutoff)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                SPARQL_ENDPOINT,
                params={"query": query},
                headers={"Accept": "application/sparql-results+json"},
            )
            response.raise_for_status()
    except httpx.HTTPError:
        return None

    bindings = response.json()["results"]["bindings"]
    series = [
        {"period": row["refMonth"]["value"][:7], "average_price": float(row["averagePrice"]["value"])}
        for row in bindings
    ]
    if len(series) < MIN_TREND_POINTS:
        return None

    points = [(i, p["average_price"]) for i, p in enumerate(series)]
    slope, intercept = _linear_regression(points)
    last_index = len(series) - 1

    projections = [
        {"months_ahead": m, "price": intercept + slope * (last_index + m)}
        for m in PROJECTION_MONTHS
    ]

    start_price = series[0]["average_price"]
    current_price = series[-1]["average_price"]
    pct_change = ((current_price - start_price) / start_price) * 100 if start_price else None

    return {
        "area_name": admin_district,
        "series": series,
        "start_price": start_price,
        "current_price": current_price,
        "pct_change": pct_change,
        "monthly_trend": slope,
        "projections": projections,
    }
