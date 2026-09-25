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

SELECT ?refMonth ?averagePrice ?percentageAnnualChange ?salesVolume ?label WHERE {{
  ?obs ukhpi:refRegion ?region ;
       ukhpi:refMonth ?refMonth ;
       ukhpi:averagePrice ?averagePrice ;
       ukhpi:percentageAnnualChange ?percentageAnnualChange .
  OPTIONAL {{ ?obs ukhpi:salesVolume ?salesVolume }}
  ?region rdfs:label ?label .
  FILTER(LANG(?label) = "en")
  FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{name}")))
  FILTER(?refMonth >= "{cutoff}"^^<http://www.w3.org/2001/XMLSchema#gYearMonth>)
}}
ORDER BY DESC(?refMonth)
"""

# How far back the "latest figure" query looks. Long enough to survive
# a month where the index has not been published yet, short enough that
# matching several areas stays a handful of rows rather than a history.
_LATEST_LOOKBACK_MONTHS = 8


def _city_forms(wanted: str) -> tuple[str, ...]:
    """The labels a city goes by in the index, lower case: "City of
    Nottingham", "Aberdeen City", and the "Bristol, City of" form."""
    return (f"city of {wanted}", f"{wanted} city", f"{wanted}, city of")


def is_the_place_asked_for(label: str, name: str) -> bool:
    """Whether an area's published label is the place that was asked for,
    by its own name or as that city, rather than a county or region that
    only contains the name. The market report calls its list cities only
    while every entry passes this."""
    wanted = name.strip().lower()
    return label.strip().lower() in (wanted, *_city_forms(wanted))


def _pick_area(labels, name: str) -> str | None:
    """Which of the areas the CONTAINS filter matched is the one that
    was actually asked for.

    CONTAINS is a substring match, and UK area names nest: asking for
    "Manchester" also matches "Greater Manchester", and asking for
    "York" matches six areas including "North Yorkshire" and
    "Yorkshire and The Humber". The filter has to stay, because
    postcodes.io's admin_district ("Westminster") does not always equal
    the HPI label ("City of Westminster") - so the choice is made here
    instead of being left to whichever row the endpoint returned first.

    Exact match wins. Next comes the city's own label, the name with
    "City of" before it or "City" after it. Failing both the shortest
    containing label wins, which resolves "Manchester" over "Greater
    Manchester".

    The city step is from 18 Sep 2026 (first-visitor audit item D6).
    Shortest-first picked "Nottinghamshire" over "City of Nottingham" and
    "Aberdeenshire" over "City of Aberdeen", so the market report listed
    two counties among its "major UK cities" and a report for an NG1
    address quoted the county's average under the county's name.
    """
    wanted = name.strip().lower()
    labels = list(labels)
    for label in labels:
        if label.strip().lower() == wanted:
            return label
    for form in _city_forms(wanted):
        for label in labels:
            if label.strip().lower() == form:
                return label
    containing = sorted(
        (l for l in labels if wanted in l.strip().lower()),
        key=lambda l: (len(l), l),
    )
    return containing[0] if containing else None


def _latest_cutoff() -> str:
    return (date.today() - timedelta(days=31 * _LATEST_LOOKBACK_MONTHS)).strftime("%Y-%m")


async def _latest_for_area(client: httpx.AsyncClient, name: str) -> dict | None:
    if not name:
        return None
    query = _QUERY_TEMPLATE.format(name=name.replace('"', ""), cutoff=_latest_cutoff())
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

    chosen = _pick_area({row["label"]["value"] for row in bindings}, name)
    if chosen is None:
        return None
    # Rows arrive newest first, so the first one for the chosen area is
    # its latest published month.
    row = next((r for r in bindings if r["label"]["value"] == chosen), None)
    if row is None:
        return None
    # How many sales the index month rests on (25 Sep 2026). HM Land
    # Registry publishes no count for its newest months, which are first
    # estimates it revises as late sales are registered, so the count is
    # the newest month that has one. Read 25 Sep 2026: City of
    # Westminster's July 2026 index read -20.7%, June -24.2%, both without
    # a count, and May rested on 84 sales.
    counted = next((r for r in bindings if r["label"]["value"] == chosen and r.get("salesVolume")), None)
    return {
        # The area's own published label, not the name that was searched
        # for: when they differ the reader should see which area the
        # figure actually describes.
        "name": chosen,
        "average_price": float(row["averagePrice"]["value"]),
        "annual_change_pct": float(row["percentageAnnualChange"]["value"]),
        "period": row["refMonth"]["value"],
        "provisional": not row.get("salesVolume"),
        "sales_volume": int(float(counted["salesVolume"]["value"])) if counted else None,
        "sales_volume_period": counted["refMonth"]["value"] if counted else None,
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

SELECT ?refMonth ?averagePrice ?label WHERE {{
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

# The spans the index's own history is read over (18 Sep 2026,
# first-visitor audit item D3). Until then this module fitted a straight
# line to five years of the series and projected it one and two years on.
# The line's starting point sat about £12,000 under the latest index, so
# KT3 4HX's locked card showed "Now £591,555" beside "Projected, +1 year
# £579,490", a fall, on the same page as the index's own "+3.3% year on
# year". A projection is not a published figure, so it is gone: the card
# shows the index and how far it moved over one, five and ten years,
# each only where the series reaches back that far.
CHANGE_YEARS = (1, 5, 10)


def _months_before(period: str, months: int) -> str:
    """"2026-06" and 12 as "2025-06"."""
    year, month = int(period[:4]), int(period[5:7])
    total = year * 12 + (month - 1) - months
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def _changes(series: list[dict]) -> list[dict]:
    """How far the index moved to its latest month over each span in
    CHANGE_YEARS, from the same month that many years earlier. A span the
    series does not reach, or whose month is missing from it, is left out
    rather than taken from the nearest month."""
    if not series:
        return []
    latest = series[-1]
    by_period = {p["period"]: p["average_price"] for p in series}
    out = []
    for years in CHANGE_YEARS:
        period = _months_before(latest["period"], 12 * years)
        then = by_period.get(period)
        if not then:
            continue
        out.append({
            "years": years,
            "period": period,
            "price": then,
            "pct": (latest["average_price"] - then) / then * 100,
        })
    return out


async def price_trend(admin_district: str, years: int = max(CHANGE_YEARS)) -> dict | None:
    if not admin_district:
        return None
    # Half a year further back than the span itself: the index is
    # published a couple of months in arrears, so a cutoff exactly ten
    # years before today stops short of ten years before the latest month
    # and the ten-year change would never be found. The series is cut
    # back to the span below.
    cutoff = (date.today() - timedelta(days=365 * years + 183)).strftime("%Y-%m")
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
    # One area only. CONTAINS matches every area whose name contains
    # this one, so an unfiltered series interleaved several: "Manchester"
    # returned Manchester and Greater Manchester alternating month by
    # month, and "York" returned six areas at once. The chart drew a
    # zigzag between different places, the projection was fitted to it,
    # and the x-axis printed each year once per area, which is how this
    # was found.
    chosen = _pick_area({row["label"]["value"] for row in bindings}, admin_district)
    if chosen is None:
        return None
    series = [
        {"period": row["refMonth"]["value"][:7], "average_price": float(row["averagePrice"]["value"])}
        for row in bindings
        if row["label"]["value"] == chosen
    ]
    if series:
        start = _months_before(series[-1]["period"], 12 * years)
        series = [p for p in series if p["period"] >= start]
    if len(series) < MIN_TREND_POINTS:
        return None

    changes = _changes(series)
    five = next((c for c in changes if c["years"] == 5), None)

    return {
        # The area the figures actually describe, which is not always
        # the name that was searched for ("Westminster" resolves to
        # "City of Westminster").
        "area_name": chosen,
        "series": series,
        "current_price": series[-1]["average_price"],
        "current_period": series[-1]["period"],
        # The one, five and ten year changes the series has (see
        # CHANGE_YEARS). start_price and pct_change are the five-year one,
        # for the card and the PDF, and None when the series is shorter:
        # they were the first point of the series, which read as "over 5
        # years" for an authority with three years of index behind it.
        "changes": changes,
        "start_price": five["price"] if five else None,
        "pct_change": five["pct"] if five else None,
    }
