"""Test setup. The app reads its configuration from the environment at
import time, so everything here happens BEFORE `app.main` is imported:

- DATABASE_URL points at a throwaway SQLite file, never the real Neon
  database. Local dev and production share one DATABASE_URL in .env,
  and load_dotenv() would otherwise pick it up - a test run would then
  write pageviews and accounts into production.
- Third-party keys are blanked so no test can reach Stripe, Telegram
  or the EPC API by accident. The pages that need upstream data get it
  from the fakes in the tests themselves.
"""
import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="ukpi-test-"), "test.sqlite3")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["SESSION_SECRET"] = "test-secret-not-for-production"
for _key in (
    "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "STRIPE_PRICE_ID_MONTHLY",
    "STRIPE_PRICE_ID_QUARTERLY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "GOOGLE_MAPS_API_KEY", "RENDER",
):
    os.environ[_key] = ""
# The EPC layer must look configured so the property page renders its
# EPC card path, but the token must never be real.
os.environ["EPC_API_TOKEN"] = "test-token"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import main as app_main  # noqa: E402


@pytest.fixture(scope="session")
def client():
    # The startup hook calls db.init_db(), which creates the tables in
    # the SQLite file above.
    with TestClient(app_main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_leaked_session(request):
    """The client is session-scoped for speed, so its cookie jar is
    shared by every test. One test signing in would otherwise leave
    every later test authenticated, which quietly changed what the
    report page rendered and failed two unrelated tests. Each test
    starts signed out."""
    if "client" in request.fixturenames:
        request.getfixturevalue("client").cookies.clear()
    # The anonymous-HTML cache is process-wide too: a page rendered
    # under one test's environment (say, Stripe unconfigured) would be
    # served verbatim to the next test that expects the other.
    from app.services import _cache
    for key in [k for k in _cache._store if isinstance(k, tuple) and k and k[0] in ("anon_html", "sitemap")]:
        _cache._evict(key)
    yield


# ---- Fakes for the property report --------------------------------------
# The report fans out to ~30 upstream services. Tests don't exercise
# those (they have their own units); they exercise that the page renders
# correctly given their results, which is where the template crashes
# have historically been.

def fake_location(country="England", postcode="M14 5TG", outcode="M14"):
    return {
        "postcode": postcode, "outcode": outcode, "country": country,
        "region": "North West", "admin_district": "Manchester",
        "latitude": 53.45, "longitude": -2.22,
        "codes": {"admin_district": "E08000003", "lsoa": "E01005123"},
    }


def fake_gather(**overrides):
    """A minimal but realistic set of gather results: enough unlocked
    data for the highlights strip and keep-exploring tiles to have
    something to say."""
    base = {
        "overview": {
            "score": 75, "grade": "Good",
            "verdict": "Good overall — 79% of nearby schools rated Outstanding or Good.",
            "positives": ["79% of nearby schools rated Outstanding or Good"],
            "concerns": [], "premium_extra_checks": 1,
        },
        "transactions": [
            {"address": "1 Test Street", "postcode": "M14 5TG", "amount": "250000", "date": "2024-06-01"},
        ],
        "postcode_has_transactions": True,
        "hpi": {"local_authority": {"name": "Manchester", "annual_change_pct": 4.1, "average_price": 260000, "period": "2026-06"}},
        "school_landscape": {"total_schools": 12, "good_or_better_pct": 79, "radius_km": 3},
        "schools_total": 12,
        "crime_comparison": [{"category": "Burglary", "trend": "lower"}, {"category": "Violence", "trend": "higher"}, {"category": "Vehicle", "trend": "lower"}],
        "crime": {"total": 120, "month": "2026-06", "by_category": []},
        "certificates": [{"address": "1 Test Street", "rating": "C", "date": "2025-12-16", "certificate_number": "0000"}],
        "postcode_has_certificates": True,
        "property_detail": {
            "dwelling_type": "Flat", "total_floor_area": 61, "habitable_room_count": 3, "year_built": "1996–2002",
            "current_score": 75, "potential_score": 82, "current_band": "C", "potential_band": "B",
            "inspection_date": "2025-12-16", "valid_until": "2035-12-16",
            "heating_cost_current": 1099, "lighting_cost_current": 76, "hot_water_cost_current": 216,
            "heating_cost_potential": None, "lighting_cost_potential": None, "hot_water_cost_potential": None,
        },
        "nearby_sales_count": 40,
        "nearby_latest_sale": {"amount": "310000", "date": "2026-05-01"},
        "age_profile": {"under_25_pct": 62.9},
        "qualification": {"degree_pct": 32.2},
        "housing": {"owned_pct": 15.4},
        "heritage": [{"name": n, "grade": "II", "url": "https://historicengland.org.uk/", "distance_m": 120 + i * 40} for i, n in enumerate("ABCD")],
        "broadband": {"gigabit_pct": 0, "ultrafast_pct": 0, "superfast_pct": 100, "below_uso_pct": 0, "label": "Superfast"},
        "mobile": {"la_name": "Manchester", "coverage_4g_outdoor_all_pct": 100.0, "coverage_4g_indoor_all_pct": 99.8, "no_4g_outdoor_pct": 0, "coverage_5g_outdoor_pct": 96.7},
        "noise": {"road_db": 47.0, "rail_db": None, "airport_db": None, "road_label": "Low", "rail_label": None, "airport_label": None},
        "flood_zone": {"zone": 1, "label": "Zone 1 (low probability)", "source": None},
        "flood_warnings": [],
        "deprivation": {"imd_decile": 3, "la_name": "Manchester"},
        # Always set by the real gather, independent of any service result.
        "catchment_distance_schools": [], "catchment_distance_count": 0, "catchment_distance_any_real": False,
        "google_ratings_configured": False, "routing_configured": False, "amenities_pending": False,
    }
    # The template expects every service to have EITHER a result or an
    # `<name>_error` flag (the real gather guarantees this). Anything the
    # fake doesn't supply above is marked unavailable, which is also a
    # realistic state - upstreams do fail - and the page must survive it.
    for name in (
        "amenities", "price_trend", "household_income", "occupation", "radon", "clay_risk",
        "sewage", "coal_mining", "surface_water", "valuation", "background", "wellbeing",
        "rental", "designations", "food_hygiene", "google_ratings", "orientation",
        "air_quality", "historic_landfill", "catchment",
    ):
        if name not in overrides:
            base[f"{name}_error"] = True
    base.update(overrides)
    return base


@pytest.fixture
def fake_report(monkeypatch):
    """Patch the two network-bound steps of the property page. Returns a
    function the test calls to choose the location/gather it wants."""
    def _install(location=None, gather=None):
        location = location or fake_location()
        gather = gather or fake_gather()

        # /property caches finished HTML and gather results in-process
        # for anonymous viewers; without this, one test's rendered page
        # is served verbatim to the next test's different fake.
        from app.services import _cache
        _cache._store.clear()
        _cache._bytes = 0

        async def _lookup(_postcode):
            return location

        # Parameter names mirror the real _full_property_gather, so a
        # caller passing premium_unlocked by keyword works against the
        # fake exactly as it does against the real one.
        async def _gather(location, house_number, premium_unlocked, wait_for_amenities=False):
            # Mirrors what the real gather always puts in its result.
            return {"location": location, "epc_configured": True, **gather}

        monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
        monkeypatch.setattr(app_main, "_full_property_gather", _gather)
        return location, gather

    return _install
