"""The six ideas of the 30 Sep 2026 brainstorm.

1. Councils the price index spells its own way read their row, and a warm
   guide holding the old empty row asks once more (hpi tests live in
   test_hpi_index_spelling.py).
2. A postcode-only report asks for the house number before the free
   report is spent, and a bare POST spends nothing.
3. Outside England the free report offer says how many Premium checks it
   opens there.
4. The PDF links back to the live report and My properties.
5. The advertised pages are pinned outside the LRU.
6. One name per council on a page.
"""
import asyncio

from app import auth, db
from app import main as app_main
from app.models import PremiumUnlock
from app.services import _cache
from app.services import email as email_service
from sqlalchemy import func, select
from tests.conftest import fake_location
from tests.test_email_verification import _signup


def _unlocks(email):
    with db.get_session() as session:
        user = auth.find_user_by_email(session, email)
        return session.execute(
            select(PremiumUnlock.postcode, PremiumUnlock.house_number).where(PremiumUnlock.user_id == user.id)
        ).all()


# ---- 6. One name per council ---------------------------------------------

def test_every_spelling_of_a_council_reads_as_one_name():
    for given, shown in [
        ("Bristol, City of", "Bristol"), ("Bristol UA", "Bristol"), ("City of Bristol", "Bristol"),
        ("Kingston upon Hull, City of", "Kingston upon Hull"), ("City of Kingston upon Hull", "Kingston upon Hull"),
        ("Herefordshire, County of", "Herefordshire"), ("Glasgow City", "Glasgow"), ("City of Glasgow", "Glasgow"),
        ("York UA", "York"), ("City of Westminster", "Westminster"), ("Manchester", "Manchester"),
        ("Newry, Mourne and Down", "Newry, Mourne and Down"), ("St. Helens", "St. Helens"), ("", ""),
    ]:
        assert app_main._council_name(given) == shown, given


def test_the_city_of_london_keeps_its_name():
    # A different place from London, the region.
    assert app_main._council_name("City of London") == "City of London"


def test_the_guide_lead_names_the_council_once(monkeypatch):
    payload = {
        "hpi": {"local_authority": {"name": "City of Bristol", "average_price": 350000, "annual_change_pct": 2.1,
                                    "period": "2026-06"}},
        "finance": {"name": "Bristol UA", "history": [{"band_d": 2714, "label": "2026-27"}], "latest_label": "2026-27"},
    }
    lead = " ".join(app_main._area_lead("BS7", payload))
    assert "Bristol UA" not in lead and "City of Bristol" not in lead
    assert "Bristol" in lead


# ---- 1. A warm guide with the old empty council row ------------------------

def test_a_warm_guide_asks_again_only_for_a_council_the_index_spells_differently(monkeypatch):
    calls = []

    async def fake_comparison(name, region, country):
        calls.append(name)
        return {"local_authority": {"name": "City of Bristol", "average_price": 1}, "region": None, "country": None}

    monkeypatch.setattr(app_main.hpi, "area_comparison", fake_comparison)
    _cache.drop(("council_index_fix", "Bristol, City of", "South West", "England"))
    bristol = {"admin_district": "Bristol, City of", "region": "South West", "country": "England"}
    leeds = {"admin_district": "Leeds", "region": "Yorkshire and The Humber", "country": "England"}
    empty = {"local_authority": None, "region": None, "country": None}

    fresh = asyncio.run(app_main._council_index_if_missing(empty, bristol))
    assert fresh["local_authority"]["name"] == "City of Bristol"
    # A council the index spells as postcodes.io does had its answer.
    assert asyncio.run(app_main._council_index_if_missing(empty, leeds)) is None
    # A guide that already has its row is left alone.
    assert asyncio.run(app_main._council_index_if_missing(fresh, bristol)) is None
    assert calls == ["Bristol, City of"]
    # Asked once a day, not on every view.
    asyncio.run(app_main._council_index_if_missing(empty, bristol))
    assert calls == ["Bristol, City of"]


# ---- 2 and 3. The free report offer ---------------------------------------

def _open(client, fake_report, postcode, house_number="", country="England"):
    fake_report(location=fake_location(postcode=postcode, outcode=postcode.split()[0], country=country))
    r = client.get("/property", params={"postcode": postcode, "house_number": house_number})
    assert r.status_code == 200
    return r.text


def test_a_postcode_only_report_asks_for_the_number_before_the_free_report(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    email = "ideas30-postcode@customer.test"
    assert _signup(client, email).status_code == 303
    body = _open(client, fake_report, "GU1 4NQ")
    dialog = body.split('id="use-free-report-dialog"', 1)[1].split("</dialog>", 1)[0]
    assert "Which home at GU1 4NQ is it for?" in dialog
    assert 'name="house_number" required' in dialog
    assert 'type="hidden" name="house_number"' not in dialog
    # The inline offer, for anyone with scripts off, asks the same.
    inline = body.split('id="use-free-report"', 2)[-1].split("</div>", 1)[0]
    assert 'name="house_number" required' in inline

    # A bare POST spends nothing and goes back to the postcode.
    r = client.post("/property/unlock", data={"postcode": "GU1 4NQ", "house_number": ""}, follow_redirects=False)
    assert r.status_code == 303 and "unlocked=1" not in r.headers["location"]
    assert _unlocks(email) == []

    # With the number it opens that home.
    r = client.post("/property/unlock", data={"postcode": "GU1 4NQ", "house_number": "55"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("house_number=55&unlocked=1")
    assert [tuple(u) for u in _unlocks(email)] == [("GU1 4NQ", "55")]


def test_a_numbered_report_keeps_its_one_click_yes(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, "ideas30-numbered@customer.test").status_code == 303
    body = _open(client, fake_report, "SM5 2RF", "4")
    dialog = body.split('id="use-free-report-dialog"', 1)[1].split("</dialog>", 1)[0]
    assert "Use your free full report on SM5 2RF, 4?" in dialog
    assert 'type="hidden" name="house_number" value="4"' in dialog
    assert "Premium checks" not in dialog  # England: no reach line


def test_outside_england_the_offer_says_how_much_it_opens_before_the_yes(client, fake_report, monkeypatch):
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    assert _signup(client, "ideas30-ni@customer.test").status_code == 303
    body = _open(client, fake_report, "BT14 8LP", "85", country="Northern Ireland")
    reach = app_main.premium_reach("Northern Ireland")
    line = app_main.unlock_reach_line(reach)
    assert line == (f"In Northern Ireland that is {reach['reach']} of the {reach['total']} Premium checks: the other "
                    f"{reach['total'] - reach['reach']} read records that do not cover Northern Ireland, and the report says which.")
    dialog = body.split('id="use-free-report-dialog"', 1)[1].split("</dialog>", 1)[0]
    assert line in dialog
    assert line in body.split('id="use-free-report"', 2)[-1].split("</div>", 1)[0]
    assert app_main.unlock_reach_line(None) == ""


# ---- 4. The PDF's way back --------------------------------------------------

def test_the_pdf_links_to_the_live_report_and_my_properties():
    from tests.test_pdf_report import LOCATION, _report, _running_costs
    ctx = app_main._pdf_context(_report(), _running_costs(), LOCATION, "36")
    html = app_main.templates.get_template("pdf_report_full.html").render(ctx)
    live = f'href="https://ukpropertyinsight.co.uk/property?postcode={ctx["postcode_url"]}&amp;house_number=36"'
    # Cover, page 2 and the last page.
    assert html.count(live) == 3
    assert html.count('href="https://ukpropertyinsight.co.uk/watchlist"') == 3
    assert "Live version, updated as sources publish:" not in html


# ---- 5. Pinned keys ----------------------------------------------------------

def test_a_pinned_key_survives_the_store_filling_and_still_expires(monkeypatch):
    key = ("ideas30-pinned", "M1 1AE", "")
    monkeypatch.setattr(_cache, "_PINNED_KEYS", {key})
    monkeypatch.setattr(_cache, "_pinned", {})
    monkeypatch.setattr(_cache, "MAX_ENTRIES", 5)
    _cache.set(key, {"gather": "sample"})
    for i in range(20):
        _cache.set(("ideas30-crawl", i), "page")
    assert _cache.get(key, 3600) == {"gather": "sample"}
    assert key not in _cache._store and _cache.stats()["entries"] <= 5
    assert _cache.get(key, 0) is None  # its caller's TTL still applies
    _cache.set(key, "again")
    _cache.drop(key)
    assert _cache.get(key, 3600) is None


def test_the_advertised_pages_are_the_pinned_ones():
    # conftest clears the app's pins for isolation; the list is read from
    # the module that registers them.
    import inspect
    source = inspect.getsource(app_main)
    for key in ('("property_search_gather", _HERO_SAMPLE_POSTCODE, "")', '("anon_html", "/", "")',
                '("anon_html", "/premium", "")', '("anon_html", "/browser-extension", "")'):
        assert key in source
    assert app_main.SAMPLE_REWARM_S < app_main.PROPERTY_SEARCH_CACHE_TTL_S
