"""Build app/data/outcodes.json: every real UK postcode district, with
the region, district and coordinates the /areas index and the sitemap
need, so neither makes a live call per outcode.

    .venv/Scripts/python.exe scripts/build_outcode_list.py

Why: area guides (/area/{outcode}) render for any valid outcode, but only
367 hand-listed seeds were in the sitemap. The UK has ~2,900. Each guide
is a distinct page answering "{place} house prices / schools / crime /
flood risk" queries that the big portals serve thinly - this is the
site's realistic route to page-one rankings, and it was 87% unbuilt.

How: enumerate every candidate district (postcode area letters x 0-99,
plus the lettered London sub-districts) and keep the ones postcodes.io
recognises. ~12,000 cheap GETs at modest concurrency, run once and
committed. Re-run only if a district is added or retired, which is rare.
"""
import asyncio
import json
import os
import re
import string
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "app", "data", "outcodes.json")
API = "https://api.postcodes.io/outcodes/"
HEADERS = {"User-Agent": "UKPropertyInsight/1.0 (ukpropertyinsight.co.uk; one-off index build)"}
CONCURRENCY = 8

# Every postcode area in the UK (the one- or two-letter prefix).
AREAS = """AB AL B BA BB BD BH BL BN BR BS BT CA CB CF CH CM CO CR CT CV CW DA DD DE DG DH DL DN
DT DY E EC EH EN EX FK FY G GL GU GY HA HD HG HP HR HS HU HX IG IM IP IV JE KA KT KW KY L LA LD
LE LL LN LS LU M ME MK ML N NE NG NN NP NR NW OL OX PA PE PH PL PO PR RG RH RM S SA SE SG SK SL
SM SN SO SP SR SS ST SW SY TA TD TF TN TQ TR TS TW UB W WA WC WD WF WN WR WS WV YO ZE""".split()

# Districts that are subdivided with a trailing letter (central London
# plus a handful elsewhere). Anything else with a letter suffix is not a
# real district, so candidates are limited to these.
LETTERED = {"EC1", "EC2", "EC3", "EC4", "E1", "N1", "NW1", "SE1", "SW1", "W1", "WC1", "WC2",
            "CR0", "CR2", "E20", "NW1", "SW1", "W1"}


def candidates():
    for area in AREAS:
        for n in range(0, 100):
            yield f"{area}{n}"
    for base in LETTERED:
        for letter in string.ascii_uppercase:
            yield f"{base}{letter}"


async def check(client, sem, outcode):
    async with sem:
        try:
            r = await client.get(API + outcode)
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        res = r.json().get("result") or {}
        if res.get("latitude") is None:
            return None
        districts = [d for d in (res.get("admin_district") or []) if d]
        regions = [x for x in (res.get("region") or []) if x]
        countries = [x for x in (res.get("country") or []) if x]
        return {
            "outcode": res["outcode"],
            "lat": round(res["latitude"], 4),
            "lon": round(res["longitude"], 4),
            "district": districts[0] if districts else None,
            "region": regions[0] if regions else (countries[0] if countries else None),
            "country": countries[0] if countries else None,
        }


async def refine(client, sem, o):
    """The outcode endpoint lists every district an outcode touches, in
    no useful order - SW1A came back as Wandsworth. The area guide page
    geocodes the centroid instead, so the index must do the same or its
    labels won't match the pages they link to. Also yields the proper
    English region, which the outcode endpoint doesn't carry."""
    async with sem:
        try:
            r = await client.get("https://api.postcodes.io/postcodes",
                                 params={"lon": o["lon"], "lat": o["lat"], "limit": 1, "radius": 2000})
            res = (r.json().get("result") or [None])[0]
        except (httpx.HTTPError, ValueError):
            res = None
    if res:
        o["district"] = res.get("admin_district") or o["district"]
        o["region"] = res.get("region") or res.get("country") or o["region"]
    return o


async def main():
    sem = asyncio.Semaphore(CONCURRENCY)
    cands = list(dict.fromkeys(candidates()))
    print(f"checking {len(cands)} candidate districts against postcodes.io...")
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
        results = await asyncio.gather(*(check(client, sem, c) for c in cands))
    valid = [r for r in results if r]
    print(f"{len(valid)} real districts; resolving the district at each centroid...")
    async with httpx.AsyncClient(headers=HEADERS, timeout=20) as client:
        valid = await asyncio.gather(*(refine(client, sem, o) for o in valid))
    valid = sorted(valid, key=lambda r: (
        re.match(r"[A-Z]+", r["outcode"]).group(0),
        int(re.search(r"\d+", r["outcode"]).group(0)),
        r["outcode"],
    ))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(valid, fh, separators=(",", ":"))
    by_country = {}
    for v in valid:
        by_country[v["country"]] = by_country.get(v["country"], 0) + 1
    print(f"{len(valid)} real districts written to {OUT}")
    print("  by country:", by_country)
    print("  without a district name:", sum(1 for v in valid if not v["district"]))


if __name__ == "__main__":
    asyncio.run(main())
