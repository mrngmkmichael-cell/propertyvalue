"""The seven ideas of the 17 Sep 2026 morning brainstorm.

The largest: the Environment Agency's flood maps stop at the English
border, and an empty answer from them was read as Zone 1, so every area
guide, report and comparison in Wales, Scotland and Northern Ireland said
"Zone 1 (low probability), Environment Agency". These pin that it says
the map does not reach instead, on every surface that showed a zone.
"""
import asyncio
import re

from tests.conftest import fake_gather, fake_location


def _forget_html():
    from app.services import _cache
    for key in [k for k in _cache._store if isinstance(k, tuple) and k and k[0] == "anon_html"]:
        _cache._evict(key)


# ---- 1. Flood outside England ----------------------------------------------

def test_the_ea_flood_services_are_not_asked_outside_england(monkeypatch):
    from app.services import flood_zones, surface_water_risk

    async def _boom(*_a, **_k):
        raise AssertionError("the EA was asked about a point it does not map")

    monkeypatch.setattr(flood_zones, "_fetch_zone", _boom)
    monkeypatch.setattr(surface_water_risk, "_fetch", _boom)
    for country in ("Wales", "Scotland", "Northern Ireland"):
        assert asyncio.run(flood_zones.zone_for(51.47, -3.17, country)) is None
        assert asyncio.run(surface_water_risk.risk_for(51.47, -3.17, country)) is None
        gap = flood_zones.outside_coverage(country)
        assert gap["country"] == country and gap["url"].startswith("https://")
        assert flood_zones.not_mapped_label(country) == f"Not mapped for {country}"
    # England, and a country nobody told us, still ask the EA.
    assert flood_zones.outside_coverage("England") is None
    assert flood_zones.outside_coverage("") is None
    assert flood_zones.outside_coverage(None) is None


def test_a_welsh_report_says_the_flood_map_does_not_reach(client, fake_report):
    gap = {"country": "Wales", "body": "Natural Resources Wales", "map": "Flood Map for Planning",
           "url": "https://flood-map-for-planning.naturalresources.wales/"}
    gather = fake_gather(flood_zone=None, flood_not_covered=gap)
    gather.pop("surface_water_error", None)
    fake_report(location=fake_location(country="Wales", postcode="CF10 1AA", outcode="CF10"), gather=gather)
    body = client.get("/property?postcode=CF10%201AA").text
    assert "Traceback" not in body
    card = body[body.index("dashboard-card-title\">Flood Risk"):]
    card = card[:card.index("</button>")]
    assert "Not mapped for Wales" in card and "Zone 1" not in card
    assert "Natural Resources Wales maps it" in card
    assert "flood-map-for-planning.naturalresources.wales" in body
    surface = body[body.index("dashboard-card-title\">Surface Water Risk"):]
    assert "Not mapped for Wales" in surface[:surface.index("</button>")]
    # The old modal line about Zone 1 being "unmapped" is not shown here.
    modal = body[body.index('id="modal-flood"'):]
    modal = modal[:modal.index("</dialog>")]
    assert "Zone 1 (unmapped)" not in modal and "No active flood warnings" not in modal


def test_an_english_report_is_unchanged(client, fake_report):
    fake_report()
    body = client.get("/property?postcode=M14%205TG").text
    assert "Not mapped for" not in body
    assert "Zone 1 (low probability)" in body


def test_the_pdf_checklist_does_not_call_an_unmapped_point_zone_1():
    from app.services import pdf_checklist

    gap = {"country": "Scotland", "body": "SEPA", "map": "flood maps", "url": "https://map.sepa.org.uk/floodmaps"}
    rows = pdf_checklist.build({"flood_not_covered": gap, "location": {"country": "Scotland"}}, {})
    flood = [r for r in rows if r.get("label", r.get("check", "")) in ("Flood zone, rivers and sea", "Surface water flooding")
             or "Flood zone" in str(r) or "Surface water" in str(r)]
    text = " ".join(str(r) for r in flood)
    assert "Not mapped for Scotland" in text
    assert "Zone 1" not in text


def test_a_scottish_area_guide_gives_no_flood_zone(client, monkeypatch):
    from app import main as app_main

    async def _resolve(outcode):
        return fake_location(country="Scotland", postcode=f"{outcode} 1AA", outcode=outcode), True

    async def _build(outcode, location, key):
        # What a guide warmed before 17 Sep 2026 holds: the EA's empty answer as Zone 1.
        return {"has_data": True, "flood_zone": {"zone": 1, "label": "Zone 1 (low probability)", "source": None},
                "hpi": {"local_authority": {"name": "City of Edinburgh", "average_price": 303965,
                                            "annual_change_pct": 4.0, "period": "2026-07-01"}}}

    real_get = app_main._cache.get_persistent
    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    monkeypatch.setattr(app_main, "_build_area_payload", _build)
    monkeypatch.setattr(app_main._cache, "get_persistent",
                        lambda key, ttl: None if isinstance(key, tuple) and key and key[0] == "area_guide" else real_get(key, ttl))
    _forget_html()
    body = client.get("/area/EH15").text
    assert "Zone 1" not in body
    assert "Not mapped here for Scotland" in body and "map.sepa.org.uk/floodmaps" in body
    assert "Environment Agency flood map for planning" not in body
    # The FAQ answers the question honestly rather than dropping it.
    assert "Is EH15 at risk of flooding?" in body and "cover England only" in body


def test_a_change_alert_does_not_report_the_correction_as_news():
    from app import main as app_main

    old = {"flood_zone": "Zone 1 (low probability)"}
    assert app_main._snapshot_changes(old, {"flood_zone": "Not mapped for Wales"}) == []
    assert app_main._snapshot_changes(old, {"flood_zone": "Zone 3 (high probability)"}) == [
        "Flood zone changed from Zone 1 (low probability) to Zone 3 (high probability)"
    ]


# ---- 2 and 6. District comparisons ---------------------------------------------

def _versus(client, monkeypatch, left, right, summaries):
    from app import main as app_main

    async def _resolve(outcode):
        return fake_location(postcode=f"{outcode} 1AA", outcode=outcode), True

    async def _summary(postcode, house_number):
        return dict(summaries[postcode.split()[0]])

    async def _sales(lat, lon):
        return None

    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    monkeypatch.setattr(app_main, "_outcode_sales", _sales)
    real_get = app_main._cache.get_persistent
    monkeypatch.setattr(app_main._cache, "get_persistent",
                        lambda key, ttl: None if isinstance(key, tuple) and key and key[0] == "area_vs" else real_get(key, ttl))
    monkeypatch.setattr(app_main._cache, "set_persistent", lambda key, value: None)
    _forget_html()
    return client.get(f"/compare/{left}/vs/{right}").text


def test_a_scottish_comparison_is_out_of_the_index_and_its_flood_row_is_honest(client, monkeypatch):
    from app import main as app_main

    left, right = sorted(["EH15", app_main._neighbour_outcodes("EH15")[0]])
    zone1 = {"flood_zone": "Zone 1 (low probability)", "price_growth_pct": 3.3,
             "price_growth_area": "City of Edinburgh", "price_growth_period": "2026-06-01"}
    body = _versus(client, monkeypatch, left, right, {left: zone1, right: zone1})
    assert '<meta name="robots" content="noindex, follow">' in body
    assert body.count("Not mapped for Scotland") == 2 and "Zone 1" not in body
    # 6: the growth figure names its month.
    assert "+3.3% (City of Edinburgh, June 2026)" in body


def test_an_english_comparison_with_prices_stays_indexable(client, monkeypatch):
    from app import main as app_main

    left, right = sorted(["M20", app_main._neighbour_outcodes("M20")[0]])
    # The district-wide median is the price that counts; one postcode's
    # average inside the district is not the district (18 Sep 2026).
    side = {"flood_zone": "Zone 1 (low probability)", "local_median": 350000, "local_sales_count": 40}
    body = _versus(client, monkeypatch, left, right, {left: side, right: side})
    assert "noindex" not in body.lower()
    assert "Zone 1 (low probability)" in body
    only_one_postcode = {"flood_zone": "Zone 1 (low probability)", "avg_price": 350000}
    body = _versus(client, monkeypatch, left, right, {left: side, right: only_one_postcode})
    assert '<meta name="robots" content="noindex, follow">' in body
    # Without a price on one side it is not a comparison worth indexing.
    body = _versus(client, monkeypatch, left, right, {left: side, right: {"flood_zone": "Zone 1 (low probability)"}})
    assert '<meta name="robots" content="noindex, follow">' in body


def test_guides_and_the_sitemap_only_offer_comparisons_in_england_and_wales():
    from app import main as app_main

    assert app_main._versus_indexable("M20", "M21")
    assert app_main._versus_indexable("CF10", "CF11")
    assert not app_main._versus_indexable("EH15", "EH7")
    assert not app_main._versus_indexable("CA6", "DG16")
    assert not app_main._versus_indexable("BT1", "BT2")


def test_a_scottish_guide_does_not_link_its_comparisons(client, monkeypatch):
    from app import main as app_main

    for country, outcode, expect in (("Scotland", "EH15", False), ("England", "M20", True)):
        async def _resolve(o, _country=country):
            return fake_location(country=_country, postcode=f"{o} 1AA", outcode=o), True

        async def _build(o, location, key):
            return {"has_data": True}

        real_get = app_main._cache.get_persistent
        monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
        monkeypatch.setattr(app_main, "_build_area_payload", _build)
        monkeypatch.setattr(app_main._cache, "get_persistent",
                            lambda key, ttl, _g=real_get: None if isinstance(key, tuple) and key and key[0] == "area_guide" else _g(key, ttl))
        _forget_html()
        body = client.get(f"/area/{outcode}").text
        assert ("Weighing two up?" in body) is expect, outcode


# ---- 3. The extension, offered on a first home ----------------------------------

def test_a_first_saved_home_offers_the_extension_and_a_second_does_not(client, fake_report):
    fake_report()
    client.cookies.clear()
    client.post("/signup", data={"email": "first-home@example.test", "password": "correct horse battery staple"},
                follow_redirects=False)
    body = client.get("/property?postcode=M14%205TG").text
    assert 'class="compare-offer extension-offer"' in body
    assert 'href="/browser-extension"' in body
    # The same home again: still the account's only saved home, so the
    # offer is still there. It was gated on the row being created on
    # this visit until 17 Sep 2026, which meant it vanished on the
    # reload after the free unlock, the moment it was earned.
    again = client.get("/property?postcode=M14%205TG").text
    assert 'class="compare-offer extension-offer"' in again
    # A second home is where the comparison takes over: one offer at a
    # time, and this one is the more useful of the two by then.
    fake_report(location=fake_location(postcode="M18 2AA", outcode="M18"))
    second = client.get("/property?postcode=M18%202AA").text
    assert 'class="compare-offer extension-offer"' not in second
    assert 'class="compare-offer" data-animate' in second
    client.cookies.clear()


def test_a_signed_out_report_makes_no_extension_offer(client, fake_report):
    fake_report()
    client.cookies.clear()
    assert 'class="compare-offer extension-offer"' not in client.get("/property?postcode=M14%205TG").text


# ---- 4. Returning accounts at the paywall on /admin -----------------------------

def test_admin_lists_an_account_that_came_back_to_the_paywall(client, monkeypatch):
    import datetime

    from app.db import get_session
    from app.models import PageView, User, WatchlistItem

    monkeypatch.setenv("ADMIN_EMAIL", "boss-walls@example.test")
    client.cookies.clear()
    client.post("/signup", data={"email": "boss-walls@example.test", "password": "correct horse battery staple"},
                follow_redirects=False)
    empty = client.get("/admin").text
    assert "Returning accounts at the paywall" in empty

    now = datetime.datetime.now(datetime.timezone.utc)
    with get_session() as session:
        back = User(email="came-back@example.org", password_hash="x", created_at=now - datetime.timedelta(days=12))
        same_day = User(email="same-day@example.org", password_hash="x", created_at=now - datetime.timedelta(minutes=20))
        session.add_all([back, same_day])
        session.commit()
        wall = now - datetime.timedelta(hours=2)
        session.add_all([
            PageView(path="/property", user_id=back.id, created_at=now - datetime.timedelta(days=12)),
            PageView(path="/paywall", user_id=back.id, created_at=wall),
            PageView(path="/property/comparables", user_id=back.id, created_at=wall + datetime.timedelta(minutes=3)),
            PageView(path="/paywall", user_id=same_day.id, created_at=now - datetime.timedelta(minutes=5)),
            WatchlistItem(user_id=back.id, postcode="OX3 0SG", house_number="7", note=""),
        ])
        session.commit()

    body = client.get("/admin").text
    section = body[body.index('id="returning-walls"'):]
    section = section[:section.index("</section>")]
    assert "came-back@example.org" in section
    assert "7 OX3 0SG" in section and "/property/comparables" in section
    # A paywall on the day an account joined is not a return.
    assert "same-day@example.org" not in section
    client.cookies.clear()


# ---- 5 and 7. Copy -------------------------------------------------------------

def test_the_premium_buttons_say_what_the_click_does(client, monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_test_monthly")
    monkeypatch.setenv("STRIPE_PRICE_ID_QUARTERLY", "price_test_quarterly")
    client.cookies.clear()
    _forget_html()
    body = client.get("/premium").text
    assert "Sign up to subscribe" not in body
    assert "Start free, choose a plan later" in body


def test_literal_page_titles_fit_in_a_search_result(client):
    for path in ("/browser-extension", "/areas", "/methodology"):
        _forget_html()
        title = re.search(r"<title>([^<]*)</title>", client.get(path).text).group(1)
        assert len(title.replace("&amp;", "&")) <= 60, (path, title)
