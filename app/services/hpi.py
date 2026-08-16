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
    async with httpx.AsyncClient(timeout=20) as client:
        local, reg, nat = await asyncio.gather(
            _latest_for_area(client, admin_district),
            _latest_for_area(client, region),
            _latest_for_area(client, country),
        )
    return {"local_authority": local, "region": reg, "country": nat}
