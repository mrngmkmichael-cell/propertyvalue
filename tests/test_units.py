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
