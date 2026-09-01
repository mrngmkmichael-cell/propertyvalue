"""Pure-function units: the pieces that have no network and no template,
where a wrong answer is silent rather than a crash."""
import pytest

from app import main as app_main
from app.services import _cache, epc, overview_score, stripe_billing


# ---- _cache: bounded, LRU, expiry-on-read --------------------------------

def test_cache_evicts_least_recently_used(monkeypatch):
    monkeypatch.setattr(_cache, "MAX_ENTRIES", 3)
    _cache._store.clear()
    _cache.set("a", 1)
    _cache.set("b", 2)
    _cache.set("c", 3)
    assert _cache.get("a", 60) == 1  # touching a makes b the oldest
    _cache.set("d", 4)
    assert _cache.get("b", 60) is None
    assert [_cache.get(k, 60) for k in "acd"] == [1, 3, 4]
    assert len(_cache._store) == 3


def test_cache_drops_expired_entry_on_read():
    _cache._store.clear()
    _cache.set("k", "v")
    assert _cache.get("k", ttl_seconds=0) is None
    assert "k" not in _cache._store


def test_cache_stale_while_revalidate_path():
    _cache._store.clear()
    _cache.set("nat", [1])
    assert _cache.get("nat", 0, keep_expired=True) is None
    assert _cache.get_stale("nat") == [1]
    assert _cache.get("nat", 0) is None  # default path drops it
    assert _cache.get_stale("nat") is None


# ---- crawler exclusion from pageviews ------------------------------------

BROWSERS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Mobile Safari/537.36",
]
CRAWLERS = [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "curl/8.4.0", "python-httpx/0.27.2", None, "",
    # The markers added 30 Aug 2026, after a scraper naming none of the
    # older ones logged 6,600 pageviews in a morning. HTTP libraries,
    # headless browsers, and the newer AI/measurement crawlers.
    "Scrapy/2.11.2 (+https://scrapy.org)",
    "Python-urllib/3.12", "aiohttp/3.9.5", "okhttp/4.12.0",
    "axios/1.7.2", "node-fetch/1.0 (+https://github.com/bitinn/node-fetch)",
    "undici", "Java/17.0.1", "Apache-HttpClient/4.5.14 (Java/17)",
    "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/126.0.0.0",
    "Mozilla/5.0 (compatible; meta-externalagent/1.1)",
    "GoogleOther", "Amazonbot/0.1", "PerplexityBot/1.0",
    "Mozilla/5.0 (compatible; DataForSeoBot/1.0; +https://dataforseo.com/dataforseo-bot)",
    "Mozilla/5.0 (compatible; Barkrowler/0.9; +https://babbar.tech/crawler)",
    "Expanse, a Palo Alto Networks company",
]


def test_real_browsers_count_as_visitors():
    assert not any(app_main._is_crawler(ua) for ua in BROWSERS)


def test_crawlers_do_not_count_as_visitors():
    assert all(app_main._is_crawler(ua) for ua in CRAWLERS)


# ---- open-redirect guard -------------------------------------------------

def test_safe_next_only_allows_local_paths():
    assert app_main._safe_next("/premium") == "/premium"
    assert app_main._safe_next("/watchlist?x=1") == "/watchlist?x=1"
    for bad in ("https://evil.example", "//evil.example", "", None, "javascript:alert(1)"):
        assert app_main._safe_next(bad) == "/"


# ---- overview score ------------------------------------------------------

def test_overview_score_rewards_positives_and_penalises_concerns():
    clean = overview_score.compute({
        "school_landscape": {"good_or_better_pct": 90},
        "hpi": {"local_authority": {"name": "X", "annual_change_pct": 2.0}},
    })
    flagged = overview_score.compute({
        "flood_zone": {"zone": 3},
        "noise": {"road_db": 70},
        "deprivation": {"imd_decile": 1},
    })
    assert clean["score"] > flagged["score"]
    assert "Flood risk" in flagged["concerns"]
    assert clean["concerns"] == []
    assert clean["grade"] and flagged["grade"]


def test_overview_score_hides_premium_only_concerns_from_free_users():
    ctx = {"coal_mining": {"present": True}, "air_quality": {"pollutants": [{"times_guideline": 4}]}}
    free = overview_score.compute(ctx, premium_unlocked=False)
    paid = overview_score.compute(ctx, premium_unlocked=True)
    assert free["concerns"] == [] and free["premium_extra_checks"] == 2
    assert len(paid["concerns"]) == 2 and paid["premium_extra_checks"] == 0


def test_overview_score_counts_sewage_and_subsidence():
    """Both flag their own card red on the report. Missing from the
    score, a Premium reader saw a red-ringed card the verdict never
    mentioned. Thresholds must match the card statuses exactly."""
    ctx = {
        "sewage_outfalls": [{"spill_count": 25}],
        "clay_risk": {"class_2030": "Probable"},
    }
    paid = overview_score.compute(ctx, premium_unlocked=True)
    assert "Frequent sewage discharges nearby" in paid["concerns"]
    assert "Rising subsidence risk from climate change" in paid["concerns"]
    # Under the card thresholds, neither counts.
    quiet = overview_score.compute(
        {"sewage_outfalls": [{"spill_count": 19}], "clay_risk": {"class_2030": "Unlikely"}},
        premium_unlocked=True,
    )
    assert quiet["concerns"] == []
    # Both are Premium-only, so a free reader gets the count, not the finding.
    free = overview_score.compute(ctx, premium_unlocked=False)
    assert free["concerns"] == [] and free["premium_extra_checks"] == 2


# ---- EPC extension detection --------------------------------------------

def test_detect_extension_flags_large_floor_area_growth():
    history = [
        {"date": "2012-01-01", "total_floor_area": 80},
        {"date": "2022-01-01", "total_floor_area": 100},
    ]
    sig = epc.detect_extension(history)
    assert sig["likely_extended"] and sig["change_pct"] == 25.0


def test_detect_extension_needs_two_measurements():
    assert epc.detect_extension([{"date": "2020-01-01", "total_floor_area": 80}]) is None
    assert epc.detect_extension([]) is None


# ---- billing -------------------------------------------------------------

def test_access_follows_stripe_subscription_status():
    assert stripe_billing.grants_access("active")
    assert stripe_billing.grants_access("trialing")
    for status in ("canceled", "past_due", "unpaid", "incomplete", None, ""):
        assert not stripe_billing.grants_access(status), status


def test_plan_for_price_id_maps_env_price_ids(monkeypatch):
    monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_m")
    monkeypatch.setenv("STRIPE_PRICE_ID_QUARTERLY", "price_q")
    assert stripe_billing.plan_for_price_id("price_m") == "monthly"
    assert stripe_billing.plan_for_price_id("price_q") == "quarterly"
    assert stripe_billing.plan_for_price_id("price_unknown") is None
    assert stripe_billing.plan_for_price_id(None) is None


# ---- the suite itself ----------------------------------------------------

def test_suite_never_uses_the_production_database():
    """conftest points DATABASE_URL at a temp SQLite file before the app
    is imported. If that ever stops being true, every test that hits a
    page would write pageviews into production."""
    import os
    from app import db
    assert os.environ["DATABASE_URL"].startswith("sqlite:///")
    assert db.is_configured()
    assert "neon" not in str(db._get_engine().url)


def test_cache_respects_a_byte_budget(monkeypatch):
    """Entries vary from bytes to megabytes, so a count cap alone let the
    process exceed Render's memory limit. Total size must be bounded."""
    monkeypatch.setattr(_cache, "MAX_BYTES", 10_000)
    monkeypatch.setattr(_cache, "MAX_ENTRIES", 1000)
    _cache._store.clear()
    _cache._bytes = 0
    for i in range(10):
        _cache.set(f"k{i}", "x" * 3000)   # ~3 KB each as JSON
    assert _cache._bytes <= 10_000
    assert len(_cache._store) == 3           # only the newest three fit
    assert _cache.get("k9", 60) is not None and _cache.get("k0", 60) is None


def test_cache_never_holds_an_oversized_entry(monkeypatch):
    monkeypatch.setattr(_cache, "MAX_ENTRY_BYTES", 1000)
    _cache._store.clear()
    _cache._bytes = 0
    _cache.set("huge", "x" * 5000)
    assert _cache.get("huge", 60) is None and _cache._bytes == 0


# ---- crime publication gaps ----------------------------------------------

def test_crime_previous_months_walks_the_calendar():
    from app.services import crime
    assert crime._previous_months("2026-06-01", 3) == ["2026-05", "2026-04", "2026-03"]
    assert crime._previous_months("2026-01", 2) == ["2025-12", "2025-11"]


# ---- report build progress ----------------------------------------------

def test_every_progress_source_is_still_wired_into_the_gather():
    """The building page ticks a source off when its representative
    gather member returns. Rename or drop that member and the source
    would silently never tick, which is how three of them sat at
    "waiting" for a whole build during development."""
    import pathlib
    import re

    from app import main as app_main

    source = pathlib.Path(app_main.__file__).read_text(encoding="utf-8")
    start = source.index("async def _full_property_gather")
    end = source.index("\n# --- Lightweight public JSON API", start)
    gather = source[start:end]

    emitted = set(re.findall(r'_timed\(\s*"([^"]+)"', gather))
    missing = [name for name in app_main.GATHER_SOURCE_LABELS if name not in emitted]
    assert not missing, f"progress sources no longer in the gather: {missing}"


def test_progress_sources_match_the_count_the_page_shows():
    from app import main as app_main

    assert len(app_main.GATHER_SOURCE_ORDER) == len(set(app_main.GATHER_SOURCE_ORDER))
    assert len(app_main.GATHER_SOURCE_ORDER) == len(app_main.GATHER_SOURCE_LABELS)


# ---- area guide local sales ---------------------------------------------

def _sale(amount, ptype, date="2026-06-01", postcode="LS1 1AA", address="1 TEST ST"):
    return {"amount": str(amount), "property_type": ptype, "date": date,
            "postcode": postcode, "address": address}


def test_area_sales_exclude_commercial_transactions(monkeypatch):
    """Land Registry's "other" type is commercial and mixed-use. Left
    in, a city-centre district reported a £22.9m office block inside
    its house-price range."""
    import asyncio
    from app import main as app_main

    async def _nearby(lat, lon):
        return [{"postcode": "M2 4AT", "distance_m": 10}]

    async def _sold(postcodes):
        return [
            _sale(22_933_054, "other"),
            _sale(200_000, "flat-maisonette"),
            _sale(250_000, "flat-maisonette"),
            _sale(300_000, "terraced"),
            _sale(350_000, "detached"),
            _sale(400_000, "semi-detached"),
        ]

    monkeypatch.setattr(app_main, "nearby_postcodes", _nearby)
    monkeypatch.setattr(app_main, "sold_prices_for_postcodes", _sold)
    out = asyncio.run(app_main._outcode_sales(53.0, -1.0))

    assert out["count"] == 5, "the commercial sale must not be counted"
    assert out["high"] == 400_000, "a £22.9m office must not set the top of a house-price range"
    assert out["enough_for_median"] is True
    assert all(s["property_type"] != "other" for s in out["latest"])


def test_area_sales_refuse_to_average_a_tiny_sample(monkeypatch):
    """A median of two sales is a number pretending to be a statistic."""
    import asyncio
    from app import main as app_main

    async def _nearby(lat, lon):
        return [{"postcode": "M2 4AT", "distance_m": 10}]

    async def _sold(postcodes):
        return [_sale(200_000, "flat-maisonette"), _sale(450_000, "flat-maisonette")]

    monkeypatch.setattr(app_main, "nearby_postcodes", _nearby)
    monkeypatch.setattr(app_main, "sold_prices_for_postcodes", _sold)
    out = asyncio.run(app_main._outcode_sales(53.0, -1.0))

    assert out["count"] == 2
    assert out["enough_for_median"] is False
    assert len(out["latest"]) == 2, "the real sales are still shown"


def test_area_sales_return_nothing_when_no_homes_sold(monkeypatch):
    import asyncio
    from app import main as app_main

    async def _nearby(lat, lon):
        return [{"postcode": "M2 4AT", "distance_m": 10}]

    async def _sold(postcodes):
        return [_sale(5_000_000, "other"), _sale(9_000_000, "other")]

    monkeypatch.setattr(app_main, "nearby_postcodes", _nearby)
    monkeypatch.setattr(app_main, "sold_prices_for_postcodes", _sold)
    assert asyncio.run(app_main._outcode_sales(53.0, -1.0)) is None


# ---- locations that never geocoded --------------------------------------

def test_a_location_without_coordinates_is_not_usable():
    """postcodes.io returns terminated postcodes with null latitude and
    longitude. One reaching an area guide took the page down with
    "unsupported operand type(s) for -: float and NoneType", because
    every distance calculation downstream does arithmetic on them.
    Caught on /area/IV51 in production."""
    from app import main as app_main

    assert app_main._usable_location(None) is None
    assert app_main._usable_location({}) is None
    assert app_main._usable_location({"postcode": "IV51 0AE", "latitude": None, "longitude": None}) is None
    assert app_main._usable_location({"postcode": "IV51 0AE", "latitude": 57.4, "longitude": None}) is None

    good = {"postcode": "M1 1AE", "latitude": 53.4, "longitude": -2.2}
    assert app_main._usable_location(good) is good


def test_a_district_of_terminated_postcodes_falls_back_to_its_centroid(monkeypatch):
    """IV51 (Skye) has no live postcode we can find, but the district
    itself still has a centre point, and area-level data is all the
    guide shows. A 404 there would be losing a real page over a
    postcode that happens to have been retired."""
    import asyncio
    from app import main as app_main

    async def _centroid(outcode):
        return {"latitude": 57.47, "longitude": -6.24, "admin_district": "Highland",
                "region": None, "country": "Scotland"}

    async def _nearby(lat, lon, radius_m=1000, limit=100):
        return []

    async def _any_in(outcode):
        return "IV51 0AE"

    async def _lookup(postcode):
        # Terminated: postcodes.io knows the postcode, but not where it was.
        return {"postcode": "IV51 0AE", "latitude": None, "longitude": None,
                "admin_district": None, "region": None, "country": "Scotland"}

    monkeypatch.setattr(app_main, "outcode_centroid", _centroid)
    monkeypatch.setattr(app_main, "nearby_postcodes", _nearby)
    monkeypatch.setattr(app_main, "any_postcode_in_outcode", _any_in)
    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)

    location, approximate = asyncio.run(app_main._resolve_extension_location("IV51"))
    assert approximate is True
    assert location is not None, "a district with a known centre must still render"
    assert location["latitude"] == 57.47 and location["longitude"] == -6.24
    assert location["admin_district"] == "Highland"
    # No LSOA is known, and guessing one would put a real census figure
    # against the wrong neighbourhood.
    assert location["codes"] == {}


# ---- flood warnings: one slow upstream must not hold a report --------

def test_flood_area_lookups_run_together_and_are_capped(monkeypatch):
    """A live report once spent 301 seconds inside warnings_near.

    Every active warning that embeds no coordinates needs its own
    lookup, and those were done one after another inside a loop. During
    a national flood event, when this card matters most, that became
    dozens of sequential requests and the whole page waited on them.
    They now go out together, capped, and share a deadline.
    """
    import asyncio
    import time

    from app.services import _cache, flood

    _cache._store.clear()
    _cache._bytes = 0

    AREAS = 30
    DELAY = 0.05          # each lookup is slow; sequentially that is 1.5s

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            if url.endswith("/id/floods"):
                # Every warning missing its coordinates, the worst case.
                return _Resp({"items": [
                    {"floodAreaID": f"AREA{i}", "severityLevel": 3,
                     "description": f"Warning {i}", "floodArea": {}}
                    for i in range(AREAS)
                ]})
            await asyncio.sleep(DELAY)
            return _Resp({"items": {"lat": 53.8, "long": -1.55}})

    monkeypatch.setattr(flood.httpx, "AsyncClient", lambda *a, **k: _FakeClient())

    started = time.perf_counter()
    warnings = asyncio.run(flood._fetch_national())
    elapsed = time.perf_counter() - started

    assert warnings, "warnings with resolvable areas should still come back"
    # Sequential would be AREAS * DELAY. Concurrent is roughly one DELAY.
    assert elapsed < AREAS * DELAY / 3, (
        f"area lookups took {elapsed:.2f}s for {AREAS} areas; they are not running together"
    )
    assert elapsed < flood._AREA_LOOKUP_BUDGET_S + 1


def test_flood_area_lookups_stop_at_the_cap(monkeypatch):
    """A cap as well as a deadline, so an event with hundreds of
    warnings cannot turn into hundreds of requests."""
    from app.services import flood
    assert flood._MAX_AREA_LOOKUPS <= 50
    assert flood._AREA_LOOKUP_BUDGET_S <= 15


def test_noise_retries_a_throttled_layer(monkeypatch):
    """DEFRA throttles this endpoint, and a report fires all three
    layers at once. When it answers 403 to every one, all three fail
    together, which noise_near reads as a real outage, and the card
    said "Data unavailable" for an address with perfectly good noise
    data. One retry turns a burst into an answer."""
    import asyncio

    from app.services import noise

    calls = {"n": 0}

    class _Resp:
        def __init__(self, status, payload=None):
            self.status_code = status
            self._payload = payload or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(f"unexpected raise on {self.status_code}")

        def json(self):
            return self._payload

    class _Client:
        async def get(self, url, params=None, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp(403)                     # throttled
            return _Resp(200, {"features": [{"properties": {"GRAY_INDEX": 57.0}}]})

    # Zero the pause rather than patching asyncio.sleep, which the
    # replacement would otherwise call recursively.
    monkeypatch.setattr(noise, "_RETRY_PAUSE_S", 0)
    result = asyncio.run(noise._query_layer(_Client(), "ds", "layer", 53.4, -2.2))

    assert calls["n"] == 2, "a throttled layer should be retried once"
    assert result is not None, "the retry's answer should be used"


def test_noise_does_not_retry_a_real_absence(monkeypatch):
    """A 404 means the layer genuinely has nothing there. Retrying it
    would double every request for no reason."""
    import asyncio

    from app.services import noise

    calls = {"n": 0}

    class _Resp:
        status_code = 404

        def raise_for_status(self):
            raise noise.httpx.HTTPStatusError("not found", request=None, response=None)

        def json(self):
            return {}

    class _Client:
        async def get(self, url, params=None, timeout=None):
            calls["n"] += 1
            return _Resp()

    try:
        asyncio.run(noise._query_layer(_Client(), "ds", "layer", 53.4, -2.2))
    except Exception:
        pass
    assert calls["n"] == 1, "a 404 must not be retried"


def test_hpi_picks_the_area_that_was_asked_for_not_the_one_containing_it():
    """The HPI query matches area labels with CONTAINS, because
    postcodes.io's "Westminster" is the dataset's "City of Westminster".
    That substring match also made "Manchester" match Greater
    Manchester and "York" match six areas at once, so the report showed
    one area's price under another's name and the trend chart drew a
    line zigzagging between different places."""
    from app.services.hpi import _pick_area

    # Exact match wins over a longer area that merely contains it.
    assert _pick_area({"Manchester", "Greater Manchester"}, "Manchester") == "Manchester"
    assert _pick_area(
        {"East Riding of Yorkshire", "North Yorkshire", "South Yorkshire",
         "West Yorkshire", "York", "Yorkshire and The Humber"},
        "York",
    ) == "York"

    # Asking for the larger area still gets the larger area.
    assert _pick_area({"Manchester", "Greater Manchester"}, "Greater Manchester") == "Greater Manchester"

    # The case CONTAINS exists for: no exact label, one real candidate.
    assert _pick_area({"City of Westminster"}, "Westminster") == "City of Westminster"

    # Case and stray whitespace must not decide which area is returned.
    assert _pick_area({"MANCHESTER", "Greater Manchester"}, " manchester ") == "MANCHESTER"

    # Nothing plausible matched: say so rather than returning a wrong area.
    assert _pick_area(set(), "Nowhere") is None
    assert _pick_area({"Greater Manchester"}, "Leeds") is None


def test_hpi_trend_series_never_interleaves_two_areas():
    """One point per month, from one area. Before the fix a Manchester
    trend alternated Manchester and Greater Manchester month by month,
    which the projection was then fitted to."""
    import asyncio
    import httpx

    from app.services import hpi

    rows = []
    for month in ("2026-05", "2026-06"):
        for label, price in (("Manchester", 251250.0), ("Greater Manchester", 240130.0)):
            rows.append({
                "refMonth": {"value": month}, "label": {"value": label},
                "averagePrice": {"value": str(price)},
            })

    class _Response:
        def raise_for_status(self): pass
        def json(self): return {"results": {"bindings": rows}}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _Response()

    real_client = hpi.httpx.AsyncClient
    real_minimum = hpi.MIN_TREND_POINTS
    hpi.httpx.AsyncClient = lambda *a, **k: _Client()
    # The fake supplies two months. Without the lowered minimum this
    # returns None either way, which would pass whether or not the two
    # areas were separated.
    hpi.MIN_TREND_POINTS = 2
    try:
        trend = asyncio.run(hpi.price_trend("Manchester"))
    finally:
        hpi.httpx.AsyncClient = real_client
        hpi.MIN_TREND_POINTS = real_minimum

    assert trend is not None
    assert trend["area_name"] == "Manchester"
    periods = [p["period"] for p in trend["series"]]
    assert periods == sorted(set(periods)), "a month must appear exactly once"
    assert [p["average_price"] for p in trend["series"]] == [251250.0, 251250.0]


def test_seo_title_drops_the_council_name_rather_than_truncating():
    """36 of 80 sampled titles ran past what Google displays, every one
    of them where a long council name met a fixed suffix. Google cuts
    mid-word, so the part that can be spared is dropped whole."""
    from app.main import seo_title

    # Short council: everything fits, nothing is dropped.
    assert seo_title("M14", " Manchester", ": House Prices, Schools") == "M14 Manchester: House Prices, Schools"

    # Long council: the district goes, the outcode and the keywords stay.
    long_one = seo_title("BA2", " Bath and North East Somerset",
                         ": House Prices, Schools, Crime & Flood Risk")
    assert long_one == "BA2: House Prices, Schools, Crime & Flood Risk"
    assert len(long_one) <= 60

    # Nothing optional to give: return it whole rather than mangling it.
    unavoidable = seo_title("A school with a very long name indeed here", "", ": catchment")
    assert unavoidable.endswith(": catchment")

    # Still over even after dropping: drop it anyway. Getting a title
    # from 89 characters to 82 is worth having even though neither
    # clears 60, and the first version kept the long form here, which
    # left 12 school titles at 82-89.
    still_long = seo_title("x" * 50, " middle", ": " + "y" * 30)
    assert " middle" not in still_long
    assert len(still_long) < len("x" * 50 + " middle" + ": " + "y" * 30)


def test_traffic_shape_flags_the_two_real_incidents_and_nothing_normal():
    """The classifier is calibrated on the two floods that actually
    happened, and must stay quiet for every legitimate day so far."""
    from app.main import _traffic_day_shape

    # 30 Aug: 5,075 of 6,661 views on /schools/guide.
    flagged, reason = _traffic_day_shape(6661, "/schools/guide", 5075, 1548, 0)
    assert flagged and "/schools/guide" in reason and "76%" in reason

    # 22 Aug: 932 distinct guides visited once each.
    flagged, reason = _traffic_day_shape(2607, "/", 463, 1900, 40)
    assert flagged and "exactly once" in reason

    # A launch-day spike concentrated on the homepage is people: the
    # real Product Hunt day was 43% homepage WITH signed-in views, so
    # homepage concentration alone must not flag while anyone that day
    # was logged in. The first version flagged the launch, which is the
    # classifier calling the site's best day fake.
    assert _traffic_day_shape(1014, "/", 436, 200, 53) == (False, "")
    # The same concentration with nobody signed in all day is a bot
    # hammering the homepage, and does flag.
    flagged, _ = _traffic_day_shape(1014, "/", 436, 200, 0)
    assert flagged

    # A quiet day can never be flagged, whatever its shape: three
    # clicks on one URL is someone reloading, not a scraper.
    assert _traffic_day_shape(120, "/area/M14", 118, 1, 0) == (False, "")


def test_admin_figures_do_not_count_test_accounts(client):
    """24 accounts read as 24 prospects when 6 were the owner's own or
    test data. Every account figure the admin page shows must exclude
    them, and say how many it excluded."""
    import datetime

    from app import main as app_main
    from app.db import get_session

    client.post("/signup", data={"email": "seed-bot@example.test",
                                 "password": "correct horse battery staple"},
                follow_redirects=False)
    client.post("/signup", data={"email": "genuine.person@realmail.test",
                                 "password": "correct horse battery staple"},
                follow_redirects=False)

    with get_session() as s:
        m = app_main._admin_metrics(s, datetime.datetime.now(datetime.timezone.utc))

    assert m["test_accounts_excluded"] >= 1
    signup_stage = next(st for st in m["funnel_all"] if st["label"] == "Signed up")
    # The genuine signup counts; the example.test one does not.
    with get_session() as s:
        total_rows = len(app_main._test_account_ids(s)) + signup_stage["count"]
        from app.models import User
        from sqlalchemy import select, func
        all_users = s.scalar(select(func.count()).select_from(User))
    assert signup_stage["count"] < all_users
    assert total_rows == all_users

# ---- cache sizing and the memory ceiling ---------------------------------

def test_the_cache_measures_python_cost_not_json_length():
    """The store used to size entries by len(json.dumps(value)). A
    gather result is thousands of small nested dicts and strings, and
    Python charges an object header for every one, so the store held
    about three times what it thought (measured 31 Aug 2026: it claimed
    33 MB while holding 93 MB) and never evicted when it should have.
    A nested structure must now cost more than its JSON text, not
    less."""
    import json

    from app.services import _cache

    value = {f"service_{i}": [{"name": "x" * 8, "value": i, "source": "y" * 8}
                              for _ in range(20)] for i in range(20)}
    assert _cache._approx_size(value) > len(json.dumps(value))


def test_the_cache_evicts_to_stay_inside_its_byte_budget():
    from app.services import _cache

    _cache._store.clear()
    _cache._bytes = 0
    original = _cache.MAX_BYTES
    try:
        _cache.MAX_BYTES = 200_000
        for i in range(80):
            # Distinct strings on purpose. A list built as ["x"] * 40 is
            # forty references to one string and genuinely costs one
            # string, which the sizer is right to count once; sharing
            # like that would make this test measure nothing.
            _cache.set(("big", i), [f"padding-{i}-{j}" * 6 for j in range(40)])
        assert _cache._bytes <= _cache.MAX_BYTES
        assert len(_cache._store) < 80, "nothing was evicted"
        # And the accounting must match what is actually in the store,
        # or the budget drifts and stops meaning anything.
        recomputed = sum(entry[2] for entry in _cache._store.values())
        assert recomputed == _cache._bytes
    finally:
        _cache.MAX_BYTES = original
        _cache._store.clear()
        _cache._bytes = 0


def test_an_oversized_entry_is_never_kept():
    from app.services import _cache

    _cache._store.clear()
    _cache._bytes = 0
    _cache.set("huge", [f"row-{i}-" + "x" * 200 for i in range(30_000)])
    assert "huge" not in _cache._store
    assert _cache._bytes == 0


def test_concurrent_cold_reports_are_capped():
    """Every browser asking for an uncached postcode spawned a full
    ~38-source gather with nothing limiting how many ran at once."""
    from app import main

    assert main._GATHER_CONCURRENCY._value == 4


def test_a_failing_factory_does_not_leave_its_lock_behind():
    """_inflight_locks grew with the key space whenever a gather raised,
    because the pop sat after the block instead of in a finally."""
    import asyncio

    from app import main
    from app.services import _cache

    async def boom():
        raise RuntimeError("upstream down")

    main._inflight_locks.clear()
    _cache._store.clear()
    with pytest.raises(RuntimeError):
        asyncio.run(main._deduped(("k", "leaky"), 60, boom))
    assert main._inflight_locks == {}

def test_no_service_leaks_an_unclosed_http_client():
    """An httpx.AsyncClient that is never closed keeps its connection
    pool and TLS context alive: measured 1 Sep 2026 at ~702 KB each,
    against ~3.7 KB for one closed properly. Three services were
    creating one per call on the report path, leaking ~2.1 MB per
    render, which is what took the live instance to 458 MB of 512.

    Every client must be opened with `async with`, or accepted as a
    parameter from a caller that owns it.
    """
    import pathlib
    import re

    offenders = []
    for f in sorted(pathlib.Path("app").rglob("*.py")):
        for n, line in enumerate(f.read_text(encoding="utf-8").split("\n"), 1):
            if "httpx.AsyncClient(" not in line:
                continue
            if "async with" in line:
                continue
            if re.search(r":\s*httpx\.AsyncClient", line):   # a type annotation
                continue
            offenders.append(f"{f.as_posix()}:{n}: {line.strip()}")
    assert not offenders, "unclosed httpx clients:\n" + "\n".join(offenders)
