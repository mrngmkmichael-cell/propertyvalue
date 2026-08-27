"""Pure-function units: the pieces that have no network and no template,
where a wrong answer is silent rather than a crash."""
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
