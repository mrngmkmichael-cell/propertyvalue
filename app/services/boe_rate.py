"""Bank of England Bank Rate history, from the Bank's own Interactive
Statistical Database CSV export (IUDBEDR series) - a real, documented
data export endpoint, not scraping a rendered page. No key required.
"""
import datetime

import httpx

from app.services import _cache

CSV_URL = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
SERIES_CODE = "IUDBEDR"
CACHE_TTL_S = 86400  # the MPC meets ~8 times a year - daily freshness is already generous


async def current_rate() -> dict | None:
    """Returns {"rate": float, "since": "YYYY-MM-DD", "history": [{"date", "rate"}, ...]}
    where history is the sequence of actual rate CHANGES (not one row
    per business day, which is what the raw series looks like) over
    the last decade, oldest first."""
    key = ("boe_rate",)
    cached = _cache.get(key, CACHE_TTL_S)
    if cached is not None:
        return cached

    ten_years_ago = (datetime.date.today() - datetime.timedelta(days=365 * 10)).strftime("%d/%b/%Y")
    params = {
        "csv.x": "yes", "Datefrom": ten_years_ago, "Dateto": "now",
        "SeriesCodes": SERIES_CODE, "UsingCodes": "Y", "CSVF": "TN", "VPD": "Y", "VFD": "N",
    }
    try:
        # Without a browser-like User-Agent, the Bank's edge/WAF returns
        # a 403 for httpx's default UA (confirmed live) - matches the
        # same workaround amenities.py needs for Overpass.
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "curl/8.7.1"}) as client:
            response = await client.get(CSV_URL, params=params)
        response.raise_for_status()
    except httpx.HTTPError:
        return None

    lines = response.text.strip().splitlines()[1:]  # drop the "DATE,IUDBEDR" header
    daily = []
    for line in lines:
        parts = line.split(",")
        if len(parts) != 2:
            continue
        try:
            date = datetime.datetime.strptime(parts[0].strip(), "%d %b %Y").date()
            rate = float(parts[1])
        except ValueError:
            continue
        daily.append((date, rate))
    if not daily:
        return None
    daily.sort(key=lambda d: d[0])

    # Collapse the flat daily series down to just the dates the rate
    # actually changed - a decade of "same as yesterday" rows isn't a
    # history, it's noise.
    history = []
    for date, rate in daily:
        if not history or history[-1]["rate"] != rate:
            history.append({"date": date.isoformat(), "rate": rate})

    result = {"rate": daily[-1][1], "since": history[-1]["date"], "history": history}
    _cache.set(key, result)
    return result
