"""Crime stats from the Police.uk API (no key required).
Summarized by category for the latest available month, within
roughly a 1-mile radius of the given coordinates (fixed by the API).
"""
import asyncio
import calendar
from collections import Counter

import httpx

from app.services import _cache, postcodes

API_BASE = "https://data.police.uk/api"
CACHE_TTL_S = 86400  # Police.uk data only updates monthly

# The map layer (18 Sep 2026, first-visitor audit F8). Until now the
# fetch counted the month's records by category and threw the records
# themselves away, so the report could say "229 crimes within about a
# mile" and never show where. Police.uk gives each record a point, and
# the summary now keeps it, capped at MAX_POINTS nearest the address so
# a central-London month (4,728 records read on 18 Sep 2026) cannot
# swell a cache entry or the page it is written into. The cap is said
# out loud on the map whenever it bites.
#
# Those points are NOT addresses and must never be presented as one:
# Police.uk snaps every record to the nearest of a fixed list of
# anonymised map points, which can be a street or two away, and several
# crimes at one point are the same point repeated. The caveat travels
# with the layer, in POINT_CAVEAT.
MAX_POINTS = 500
POINT_CAVEAT = (
    "Police.uk moves each record to an anonymised point on a nearby street, not the address it "
    "happened at, so a pin marks a street rather than a door."
)

# The five chips the map offers, in the order they are shown. Police.uk
# publishes fourteen categories; four of them are what a buyer asks
# about by name and the rest are one honest "other" rather than a wall
# of chips. Each of the four is exactly one Police.uk category and is
# not a grouping of our own: a bicycle theft is not filed under vehicle
# crime here because Police.uk does not file it there, and weapons
# possession is not called violent crime because the register does not
# call it that. Both fall to "Other", which is named as such. Violent
# crime carries two slugs because Police.uk renamed the same category.
CHIPS = [
    ("burglary", "Burglary", ("burglary",)),
    ("vehicle", "Vehicle crime", ("vehicle-crime",)),
    ("asb", "Antisocial behaviour", ("anti-social-behaviour",)),
    ("violent", "Violent crime", ("violent-crime", "violence-and-sexual-offences")),
    ("other", "Other", ()),
]
_CHIP_OF = {category: key for key, _, categories in CHIPS for category in categories}


def chip_for(category: str) -> str:
    """Which map chip a Police.uk category belongs to. Anything the
    list above does not name is "other", never dropped."""
    return _CHIP_OF.get((category or "").strip().lower(), "other")


# Places where Police.uk's street-level figures are known to be far from
# complete, so that any count would make a place look much safer than it
# is (18 Sep 2026). Read that day at 18 points for July 2026: six Greater
# Manchester boroughs gave 0 to 2 records within a mile and central
# Manchester 5, all violence or public order, while every other force
# sampled gave 25 to 4,728 across 7 to 14 categories (Headingley in Leeds
# 493, central Birmingham 1,892). The homepage's own sample report is in
# Manchester and said "6 crimes recorded". Police Scotland does not
# publish to Police.uk at all; only British Transport Police records
# appear there. Police force areas are built from whole local
# authorities, so the council a postcode sits in names its force
# exactly. scripts/check_sources.py reads central Manchester on every
# run and says when the force publishes in full again, which is when it
# comes off this list.
GREATER_MANCHESTER_DISTRICTS = frozenset({
    "Bolton", "Bury", "Manchester", "Oldham", "Rochdale",
    "Salford", "Stockport", "Tameside", "Trafford", "Wigan",
})

_GAPS = {
    "scotland": {
        "force": "Police Scotland",
        "status": "Not published for Scotland",
        "short": "Not published",
        "note": (
            "Police Scotland does not publish street-level crime to Police.uk, which "
            "holds only British Transport Police records for Scotland. Any count here "
            "would make the area look far safer than it is, so none is shown."
        ),
    },
    "greater-manchester": {
        "force": "Greater Manchester Police",
        "status": "Not published in full",
        "short": "Incomplete",
        "note": (
            "Greater Manchester Police is publishing only a small part of its recorded "
            "crime to Police.uk at present, a handful of offences a month where other "
            "forces list hundreds. Any count here would make the area look far safer "
            "than it is, so none is shown."
        ),
    },
}


def coverage_gap(district: str | None, country: str | None) -> dict | None:
    """Where Police.uk cannot give a true count, the words to show
    instead: the force, a status for a card, a shorter one for the share
    image, and a sentence for the detail. None everywhere the figures
    are complete."""
    if (country or "").strip() == "Scotland":
        return dict(_GAPS["scotland"])
    if (district or "").strip() in GREATER_MANCHESTER_DISTRICTS:
        return dict(_GAPS["greater-manchester"])
    return None


def gap_summary(gap: dict) -> dict:
    """What every surface receives in place of a count. unpublished keeps
    any reader that predates the rule from printing a figure; incomplete
    carries the words."""
    return {"total": None, "month": None, "by_category": [], "points": [], "points_capped": False,
            "unpublished": True, "incomplete": dict(gap)}


def without_points(result: dict | None) -> dict | None:
    """The same summary with the map layer's points dropped (18 Sep
    2026). Only the report draws them; the area guides keep their
    payload in Postgres for a week across 2,943 districts, and 500
    points each would be tens of megabytes of rows nothing reads."""
    if not isinstance(result, dict) or "points" not in result:
        return result
    slim = dict(result)
    slim["points"] = []
    slim["points_capped"] = False
    return slim


def with_coverage(result: dict | None, district: str | None, country: str | None) -> dict | None:
    """A summary gathered earlier (a warm area guide or comparison) with
    the rule applied at render, so a count cached before 18 Sep 2026 is
    never shown where Police.uk cannot give a true one."""
    gap = coverage_gap(district, country)
    return gap_summary(gap) if gap else result


# When the report may call crime here lower or higher than the area
# around it (18 Sep 2026, first-visitor audit item D3). KT3 4HX read
# "Lower crime than the surrounding area" on 229 crimes against 230, one
# crime in one month, and that sentence added to the score; central York
# compared 2 with 2. The report used to count the categories that were
# lower and higher and call whichever had more, so a difference of one
# in the totals could still read as lower. One rule now decides it for
# every surface of the report that says lower or higher: the score's
# reason, What stands out, the card, the pop-up and its table, the PDF,
# its checklist and the extension's rows. Anything new that sets a crime
# count against another asks compare_counts rather than < or >. The
# district comparison pages (/compare/X/vs/Y, _versus_differences and
# _versus_faqs in main.py) still name the district with fewer crimes on
# any difference, both counts beside it; they were outside this item.
MARGIN_SHARE = 0.10   # of the larger count
MARGIN_CRIMES = 5
FEW_RECORDS = 10      # both counts under this, single figures: no comparison


def compare_counts(here: int | None, area: int | None) -> str | None:
    """Whether a crime count here is lower than, higher than or about the
    same as the count for the area it is compared with.

    "lower" or "higher" only when the two differ by at least 10 per cent
    of the larger count AND by at least 5 crimes; "same" otherwise.
    "few" when both counts are in single figures: Police.uk holds too few
    records for that month for a comparison to mean anything, so none is
    drawn. None when either count is missing.

    229 against 230 is "same", 229 against 300 is "lower", 2 against 2
    is "few".
    """
    if here is None or area is None:
        return None
    if here < FEW_RECORDS and area < FEW_RECORDS:
        return "few"
    difference = abs(here - area)
    if difference >= MARGIN_CRIMES and difference >= MARGIN_SHARE * max(here, area):
        return "lower" if here < area else "higher"
    return "same"


def month_label(month: str | None) -> str:
    """"2026-07" as "July 2026"; empty when there is no month."""
    try:
        year, number = str(month)[:7].split("-")
        return f"{calendar.month_name[int(number)]} {int(year)}"
    except (ValueError, IndexError, TypeError):
        return ""


def versus_area(local: dict | None, district: dict | None) -> dict | None:
    """The address's total against the wider postcode area's, decided by
    compare_counts. None when there is no count here to speak of.

    {"here", "area", "month", "month_label", "area_month_label",
    "verdict"}: verdict is compare_counts' answer, or None when there is
    no area count, or when the two counts are for different months (a
    force that has not published this month is walked back to an earlier
    one, and a July count against a May one is no comparison at all)."""
    if not local or local.get("total") is None:
        return None
    here = local["total"]
    month = local.get("month")
    area = district.get("total") if district else None
    area_month = district.get("month") if district else None
    verdict = None
    if area is not None and (not month or not area_month or month[:7] == area_month[:7]):
        verdict = compare_counts(here, area)
    return {
        "here": here,
        "area": area,
        "month": month,
        "month_label": month_label(month),
        "area_month_label": month_label(area_month),
        "verdict": verdict,
    }


async def summary_for_outcode(outcode: str) -> dict | None:
    """Same crime summary, but centred on the postcode district (e.g.
    'BR6') rather than the exact address - used as a wider-area
    comparison. Not a true local-authority crime rate (no free,
    population-normalized dataset comparable to a point-radius query
    exists) - this is the same ~1 mile radius sample, just centred
    more broadly.

    Kept in the Postgres cache as well as memory (25 Sep 2026). It is
    one answer for every address in the district, and on two cold
    reports that day it was the slowest source of about thirty, 5.3 s
    and 5.2 s, because a busy district's month from Police.uk is a
    megabyte. The memory tier alone is evicted within the hour by the
    crawl. No caller draws its points, so they are not stored; the
    coverage rule is applied before the cache is read, so a district
    taken off or put on the list is answered by today's rule."""
    centroid = await postcodes.outcode_centroid(outcode)
    if centroid is None:
        return None
    district, country = centroid.get("admin_district"), centroid.get("country")
    gap = coverage_gap(district, country)
    if gap:
        return gap_summary(gap)
    key = ("crime_district", outcode.upper())
    cached = await asyncio.to_thread(_cache.get_persistent, key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = without_points(await summary_near(
        centroid["latitude"], centroid["longitude"], district=district, country=country,
    ))
    if result and not result.get("unpublished"):
        await asyncio.to_thread(_cache.set_persistent, key, result)
    return result


async def summary_near(lat: float, lon: float, *, district: str | None, country: str | None) -> dict:
    """district and country are required on purpose: a caller that
    cannot say where the point is would print a Greater Manchester or
    Scottish trickle as a count."""
    gap = coverage_gap(district, country)
    if gap:
        return gap_summary(gap)
    key = _cache.coord_key("crime", lat, lon)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached
    result = await _fetch_summary(lat, lon)
    _cache.set(key, result)
    return result


# How many months to walk back when the latest month has no records.
# Greater Manchester Police published nothing for May or June 2026 while
# other forces were current, so an empty "latest month" is very often a
# publication gap, not a crime-free square mile. Reported to us by a
# reader whose street showed zero (27 Aug 2026).
_WALKBACK_MONTHS = 6


def _previous_months(latest: str, n: int) -> list[str]:
    """['2026-05', '2026-04', ...] going back n months from latest
    ('2026-06-01' or '2026-06')."""
    year, month = int(latest[:4]), int(latest[5:7])
    out = []
    for _ in range(n):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
        out.append(f"{year:04d}-{month:02d}")
    return out


async def _street(client: httpx.AsyncClient, lat: float, lon: float, date: str | None) -> list | None:
    params = {"lat": lat, "lng": lon}
    if date:
        params["date"] = date
    response = await client.get(f"{API_BASE}/crimes-street/all-crime", params=params)
    if response.status_code == 404:
        return []
    response.raise_for_status()
    return response.json()


async def _fetch_summary(lat: float, lon: float) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        records = await _street(client, lat, lon, None)

        if not records:
            # Empty latest month: find the newest month this force
            # actually published, rather than dressing the gap up as
            # zero crime.
            try:
                lu = await client.get(f"{API_BASE}/crime-last-updated")
                latest = (lu.json().get("date") or "")[:7] if lu.status_code == 200 else ""
            except (httpx.HTTPError, ValueError):
                latest = ""
            if latest:
                for month in _previous_months(latest, _WALKBACK_MONTHS):
                    try:
                        records = await _street(client, lat, lon, month)
                    except httpx.HTTPStatusError as exc:
                        # Only a rate-limit is worth waiting out, and
                        # only once. The first version slept 350ms
                        # before every month unconditionally, which
                        # cost 2.1s on exactly the rural districts that
                        # always walk the full six months, and made
                        # every one of their area guides that much
                        # slower to crawl. Any other error means try
                        # the next month, and never means "no crime".
                        if exc.response is not None and exc.response.status_code == 429:
                            await asyncio.sleep(1.0)
                            try:
                                records = await _street(client, lat, lon, month)
                            except httpx.HTTPError:
                                continue
                        else:
                            continue
                    except httpx.HTTPError:
                        continue
                    if records:
                        break

    if not records:
        # Nothing in more than half a year: almost certainly a force
        # that isn't publishing here. total None (never 0) so every
        # surface says "no data" instead of claiming a crime-free area.
        return {"total": None, "month": None, "by_category": [], "points": [], "points_capped": False,
                "unpublished": True}

    counts = Counter(rec.get("category", "unknown") for rec in records)
    by_category = [
        {"category": cat.replace("-", " "), "count": n}
        for cat, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    points, capped = _points(records, lat, lon)

    return {
        "total": len(records),
        "month": records[0]["month"] if records else None,
        "by_category": by_category,
        # The map layer's own data (18 Sep 2026). total stays the count
        # of every record for the month; points may be a capped subset,
        # and points_capped says so rather than letting the map imply
        # that is all the force recorded.
        "points": points,
        "points_capped": capped,
    }


def _points(records: list, lat: float, lon: float) -> tuple[list[dict], bool]:
    """Every record's anonymised point, nearest the address first and
    capped at MAX_POINTS, with whether the cap bit.

    Each point carries the category in the same words by_category uses
    and the chip it belongs to, so the map never has to reproduce the
    grouping. A record whose location Police.uk withheld is skipped:
    a missing point is not a point at (0, 0).
    """
    scored = []
    for rec in records:
        where = rec.get("location") or {}
        try:
            plat, plon = float(where["latitude"]), float(where["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        category = (rec.get("category") or "unknown")
        # Squared degrees: only the ordering matters, and a mile of
        # latitude and a mile of longitude are close enough at these
        # distances for "which 500 are nearest".
        scored.append(((plat - lat) ** 2 + (plon - lon) ** 2, {
            "lat": round(plat, 5), "lon": round(plon, 5),
            "category": category.replace("-", " "), "chip": chip_for(category),
        }))
    scored.sort(key=lambda pair: pair[0])
    return [point for _, point in scored[:MAX_POINTS]], len(scored) > MAX_POINTS
