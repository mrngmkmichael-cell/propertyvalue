"""Sold-price history from HM Land Registry Price Paid Data,
queried live via their public SPARQL endpoint (no key required).
"""
import httpx

SPARQL_ENDPOINT = "https://landregistry.data.gov.uk/landregistry/query"

_QUERY_TEMPLATE = """
prefix xsd: <http://www.w3.org/2001/XMLSchema#>
prefix lrppi: <http://landregistry.data.gov.uk/def/ppi/>
prefix lrcommon: <http://landregistry.data.gov.uk/def/common/>
prefix skos: <http://www.w3.org/2004/02/skos/core#>

SELECT ?paon ?saon ?street ?town ?county ?amount ?date ?category
WHERE {{
  VALUES ?postcode {{"{postcode}"^^xsd:string}}
  ?addr lrcommon:postcode ?postcode.
  ?transx lrppi:propertyAddress ?addr ;
          lrppi:pricePaid ?amount ;
          lrppi:transactionDate ?date ;
          lrppi:transactionCategory/skos:prefLabel ?category.
  OPTIONAL {{?addr lrcommon:county ?county}}
  OPTIONAL {{?addr lrcommon:paon ?paon}}
  OPTIONAL {{?addr lrcommon:saon ?saon}}
  OPTIONAL {{?addr lrcommon:street ?street}}
  OPTIONAL {{?addr lrcommon:town ?town}}
}}
ORDER BY DESC(?date)
"""


def _binding_value(row: dict, key: str, default: str = "") -> str:
    return row.get(key, {}).get("value", default)


async def sold_prices_for_postcode(canonical_postcode: str) -> list[dict]:
    """Fetch all recorded sold transactions for an exact, canonically
    formatted postcode (e.g. 'PL6 8RU'). Returns newest first."""
    query = _QUERY_TEMPLATE.format(postcode=canonical_postcode)

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            SPARQL_ENDPOINT,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
        )
    response.raise_for_status()
    bindings = response.json()["results"]["bindings"]

    transactions = []
    for row in bindings:
        paon = _binding_value(row, "paon")
        saon = _binding_value(row, "saon")
        street = _binding_value(row, "street")
        address_parts = [p for p in (saon, paon, street) if p]
        transactions.append({
            "address": " ".join(address_parts) or "Address not available",
            "town": _binding_value(row, "town"),
            "county": _binding_value(row, "county"),
            "amount": _binding_value(row, "amount"),
            "date": _binding_value(row, "date")[:10],
            "category": _binding_value(row, "category"),
        })
    return transactions
