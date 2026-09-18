"""The eight ideas of the 18 Sep 2026 morning brainstorm.

The first and largest: Police.uk carries only a trickle for Greater
Manchester (5 records within a mile of central Manchester for July 2026,
where Headingley in Leeds has 493) and only British Transport Police
records for Scotland, and every surface printed that trickle as a count.
The homepage's own sample report, in central Manchester, said "6 crimes
recorded". These pin that no count is shown there and that the words say
why, on every surface that showed one.
"""
import asyncio
import json
import re

from tests.conftest import fake_gather, fake_location


def _forget_html():
    from app.services import _cache
    for key in [k for k in _cache._store if isinstance(k, tuple) and k and k[0] == "anon_html"]:
        _cache._evict(key)


# ---- 1. Crime where Police.uk cannot give a true count ------------------------

def test_police_uk_is_not_asked_where_its_figures_are_incomplete(monkeypatch):
    from app.services import crime

    calls = []

    async def _fetch(lat, lon):
        calls.append((lat, lon))
        return {"total": 493, "month": "2026-07", "by_category": [{"category": "violent crime", "count": 144}]}

    monkeypatch.setattr(crime, "_fetch_summary", _fetch)
    for district in sorted(crime.GREATER_MANCHESTER_DISTRICTS):
        result = asyncio.run(crime.summary_near(53.48, -2.24, district=district, country="England"))
        assert result["total"] is None and result["by_category"] == []
        assert result["incomplete"]["force"] == "Greater Manchester Police"
        assert result["incomplete"]["status"] == "Not published in full"
    scot = asyncio.run(crime.summary_near(55.95, -3.19, district="City of Edinburgh", country="Scotland"))
    assert scot["total"] is None and scot["incomplete"]["status"] == "Not published for Scotland"
    assert calls == []
    # Everywhere else Police.uk is asked as before, Northern Ireland included.
    leeds = asyncio.run(crime.summary_near(53.82, -1.57, district="Leeds", country="England"))
    belfast = asyncio.run(crime.summary_near(54.60, -5.93, district="Belfast", country="Northern Ireland"))
    assert leeds["total"] == 493 and belfast["total"] == 493 and len(calls) == 2


def test_the_ten_boroughs_are_names_the_postcode_data_uses():
    """A misspelt borough would switch the rule off without a sound."""
    from app.services import crime

    with open("app/data/outcodes.json", encoding="utf-8") as fh:
        districts = {o.get("district") for o in json.load(fh)}
    assert len(crime.GREATER_MANCHESTER_DISTRICTS) == 10
    assert crime.GREATER_MANCHESTER_DISTRICTS <= districts


def test_every_crime_lookup_says_where_it_is():
    """summary_near takes district and country as required keywords, so a
    call without them would raise inside a live gather."""
    source = open("app/main.py", encoding="utf-8").read()
    # The rest of each calling line: the arguments hold brackets of their own.
    calls = re.findall(r"crime\.summary_near\((.*)", source)
    assert len(calls) >= 5
    for args in calls:
        assert "district=" in args and "country=" in args, args


def test_a_cached_count_is_replaced_at_render():
    from app.services import crime

    cached = {"total": 6, "month": "2026-07", "by_category": [{"category": "violent crime", "count": 5}]}
    assert crime.with_coverage(cached, "Manchester", "England")["total"] is None
    assert crime.with_coverage(cached, "Leeds", "England") is cached
    assert crime.with_coverage(None, "Leeds", "England") is None


def test_the_crime_comparison_needs_both_sides():
    """An empty side read "Higher" for every category."""
    from app import main as app_main

    here = {"total": 20, "by_category": [{"category": "burglary", "count": 20}]}
    assert app_main._crime_comparison(here, {"total": None, "by_category": []}) == []
    assert app_main._crime_comparison(here, None) == []
    rows = app_main._crime_comparison(here, {"total": 10, "by_category": [{"category": "burglary", "count": 10}]})
    assert rows == [{"category": "burglary", "here": 20, "area": 10, "trend": "higher"}]


def _gap_gather():
    from app.services import crime
    gap = crime.gap_summary(crime.coverage_gap("Manchester", "England"))
    return fake_gather(crime=gap, crime_comparison=[])


def test_a_greater_manchester_report_shows_no_count(client, fake_report):
    fake_report(gather=_gap_gather())
    body = client.get("/property?postcode=M14%205TG").text
    assert "Traceback" not in body
    card = body[body.index('dashboard-card-title">Crime &amp; Safety'):]
    card = card[:card.index("</button>")]
    assert "Not published in full" in card and "crimes recorded" not in card
    modal = body[body.index('id="modal-crime"'):]
    modal = modal[:modal.index("</dialog>")]
    assert "Greater Manchester Police is publishing only a small part" in modal
    assert "None" not in modal


def test_a_report_elsewhere_reads_its_count_and_month_properly(client, fake_report):
    location = fake_location(postcode="SE15 4QN", outcode="SE15")
    location["admin_district"] = "Southwark"
    by_category = [{"category": "violent crime", "count": 400}, {"category": "burglary", "count": 930}]
    fake_report(location=location, gather=fake_gather(
        crime={"total": 1330, "month": "2026-07", "by_category": by_category},
        district_crime={"total": 2104, "month": "2026-07", "by_category": by_category},
    ))
    body = client.get("/property?postcode=SE15%204QN").text
    card = body[body.index('dashboard-card-title">Crime &amp; Safety'):]
    assert "1,330 crimes recorded" in card[:card.index("</button>")]
    modal = body[body.index('id="modal-crime"'):]
    modal = modal[:modal.index("</dialog>")]
    assert "1,330 crimes recorded within ~1 mile in July 2026" in modal
    assert "versus 2,104 in the wider SE15 postcode area" in modal
    assert "2026-07" not in modal


def _guide(client, monkeypatch, outcode, district, country, payload):
    from app import main as app_main

    async def _resolve(code):
        location = fake_location(country=country, postcode=f"{code} 1AA", outcode=code)
        location["admin_district"] = district
        return location, True

    async def _build(code, location, key):
        return dict(payload)

    real_get = app_main._cache.get_persistent
    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    monkeypatch.setattr(app_main, "_build_area_payload", _build)
    monkeypatch.setattr(app_main._cache, "get_persistent",
                        lambda key, ttl: None if isinstance(key, tuple) and key and key[0] == "area_guide" else real_get(key, ttl))
    _forget_html()
    return client.get(f"/area/{outcode}").text


# What a guide warmed before 18 Sep 2026 holds for central Manchester.
_WARM_TRICKLE = {
    "has_data": True,
    "crime": {"total": 6, "month": "2026-07", "by_category": [{"category": "violent crime", "count": 5}]},
}


def test_a_warm_greater_manchester_guide_shows_no_count(client, monkeypatch):
    body = _guide(client, monkeypatch, "M1", "Manchester", "England", _WARM_TRICKLE)
    assert "Traceback" not in body
    section = body[body.index("<h2>Crime</h2>"):]
    section = section[:section.index("</section>")]
    assert "6 crimes recorded" not in section and "6 crime" not in section
    assert "Greater Manchester Police is publishing only a small part" in section
    # The FAQ says the same thing rather than printing the trickle.
    assert "Is M1 safe?" in body and "6 crimes were recorded" not in body


def test_a_guide_elsewhere_keeps_its_count(client, monkeypatch):
    payload = {"has_data": True, "crime": {"total": 1494, "month": "2026-07",
                                           "by_category": [{"category": "violent crime", "count": 144}]}}
    body = _guide(client, monkeypatch, "LS6", "Leeds", "England", payload)
    section = body[body.index("<h2>Crime</h2>"):]
    section = section[:section.index("</section>")]
    assert "1,494 crimes recorded" in " ".join(section.split())
    assert "July 2026" in section and "2026-07" not in section
    assert "1,494 crimes were recorded" in body


def test_a_scottish_guide_no_longer_warns_about_a_figure_it_does_not_show(client, monkeypatch):
    body = _guide(client, monkeypatch, "EH15", "City of Edinburgh", "Scotland", _WARM_TRICKLE)
    assert "Please don't rely on the crime figure" not in body
    assert "Police Scotland does not publish" in body
    assert "6 crimes recorded" not in body


def test_the_pdf_checklist_names_the_gap():
    from app.services import crime, pdf_checklist

    gap = crime.gap_summary(crime.coverage_gap("Wigan", "England"))
    rows = pdf_checklist.build({"crime": gap}, {})
    text = " ".join(str(r) for r in rows if "Crime" in str(r))
    assert "Not published in full" in text and "None" not in text
    rows = pdf_checklist.build({"crime": {"total": 1330, "month": "2026-07", "by_category": []}}, {})
    text = " ".join(str(r) for r in rows if "Crime" in str(r))
    assert "1,330 recorded in July 2026" in text


def test_the_accuracy_log_carries_the_finding(client):
    body = client.get("/accuracy").text
    assert "Found by our own check" in body
    assert "Greater Manchester Police is publishing only a small part" in body
    assert "or found by our own checks" in body


# ---- 2. District comparisons: ties, and rows that were one home -------------

def _versus(client, monkeypatch, left, right, summaries):
    from app import main as app_main

    async def _resolve(outcode):
        location = fake_location(postcode=f"{outcode} 1AA", outcode=outcode)
        location["admin_district"] = "Leeds"
        return location, True

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


def test_a_tie_names_no_winner():
    from app import main as app_main

    same = {"crime_total": 1330, "local_median": 250000, "imd_decile": 5}
    faqs = dict(app_main._versus_faqs("LS6", "LS7", same, dict(same)))
    assert faqs["Which has less crime, LS6 or LS7?"].startswith("Neither. Both recorded 1,330 crimes")
    assert faqs["Is LS6 or LS7 cheaper?"].startswith("Neither. Homes around both sell for about £250,000")
    assert app_main._versus_differences("LS6", "LS7", same, dict(same)) == []
    faqs = dict(app_main._versus_faqs("LS6", "LS7", same, dict(same, crime_total=2000, local_median=300000)))
    assert faqs["Which has less crime, LS6 or LS7?"].startswith("LS6 recorded fewer crimes in the same period (1,330 in LS6 against 2,000 in LS7")
    assert faqs["Is LS6 or LS7 cheaper?"].startswith("LS6 is the cheaper of the two")


def test_a_district_comparison_does_not_show_one_home_as_the_district(client, monkeypatch):
    from app import main as app_main

    left, right = sorted(["LS6", app_main._neighbour_outcodes("LS6")[0]])
    side = {"local_median": 310000, "local_sales_count": 104, "avg_price": 290000,
            "dwelling_type": "End-terrace house", "floor_area": 97, "year_built": "1991-1995",
            "energy_band": "C", "heating_cost": 820, "crime_total": 494, "imd_decile": 4}
    other = dict(side, energy_band="D", dwelling_type="Mid-floor flat")
    body = _versus(client, monkeypatch, left, right, {left: side, right: other})
    assert "Traceback" not in body
    for gone in ("Dwelling type", "Floor area", "<strong>Built</strong>", "Energy rating",
                 "Heating cost", "End-terrace house", "The most recent EPC we hold",
                 "Average sold price", "290,000"):
        assert gone not in body, gone
    assert "Median home sale nearby" in body and "Crime nearby" in body
    assert "494 recorded" in body
    assert "energy ratings" not in body and "compared on sold prices, energy," not in body


def test_the_address_comparison_keeps_the_home_s_own_certificate():
    """Only district pages lose the rows: for two addresses the certificate
    is each home's own."""
    source = open("app/templates/compare.html", encoding="utf-8").read()
    rows = source[source.index("<strong>Dwelling type</strong>") - 800:source.index("<strong>Dwelling type</strong>")]
    assert "{% if not versus %}" in rows


def test_the_price_answer_uses_the_district_median_only():
    from app import main as app_main

    one_postcode = {"avg_price": 82818}
    district = {"local_median": 256000}
    faqs = dict(app_main._versus_faqs("LS6", "LS7", one_postcode, district))
    assert "Is LS6 or LS7 cheaper?" not in faqs
    assert app_main._versus_differences("LS6", "LS7", one_postcode, district) == []


# ---- 3. A subscriber who paid without a home gets a first step --------------

def _subscriber(client, email):
    from app import auth, db
    client.post("/signup", data={"email": email, "password": "correct-horse-battery"}, follow_redirects=True)
    client.post("/login", data={"email": email, "password": "correct-horse-battery"}, follow_redirects=True)
    with db.get_session() as session:
        auth.find_user_by_email(session, email).is_premium = True
        session.commit()


def _save_home(email, postcode="LS6 2DA", house_number="12"):
    from app import auth, db
    from app.models import WatchlistItem
    with db.get_session() as session:
        user = auth.find_user_by_email(session, email)
        session.add(WatchlistItem(user_id=user.id, postcode=postcode, house_number=house_number))
        session.commit()


def test_a_subscriber_with_nothing_saved_is_told_where_to_start(client):
    from app import main as app_main

    _subscriber(client, "first-step@customer.test")
    body = client.get("/premium/success").text
    flat = " ".join(body.split())
    assert "Premium is on: every check on every property is open from now on." in flat
    assert "Start with the home you are weighing up" in body
    assert '<input type="hidden" name="src" value="premium-success">' in body
    assert "premium-success" in app_main.REPORT_SOURCES
    assert f"when one of these happens to it: {app_main.ALERT_TRIGGERS_LIST}. Never on a schedule." in flat
    assert "Choosing on schools?" in body and 'href="/schools/guide"' in body
    assert "Back to search" not in body
    # One postcode box, not the header's as well.
    assert 'id="header-search"' not in body
    # The homepage says the same, once, until something is saved.
    home = client.get("/").text
    assert "<strong>Premium is on.</strong>" in home and "Start with schools" in home


def test_a_subscriber_with_a_saved_home_is_sent_to_it(client):
    _subscriber(client, "has-a-home@customer.test")
    _save_home("has-a-home@customer.test")
    body = " ".join(client.get("/premium/success").text.split())
    assert "Open My properties</a>: the 1 home you saved is open in full now." in body
    assert "Start with the home you are weighing up" not in body
    assert 'id="header-search"' in client.get("/premium/success").text
    assert "<strong>Premium is on.</strong>" not in client.get("/").text


def test_a_free_account_and_a_visitor_see_no_subscriber_prompt(client):
    assert "<strong>Premium is on.</strong>" not in client.get("/").text
    client.post("/signup", data={"email": "free-first@customer.test", "password": "correct-horse-battery"}, follow_redirects=True)
    assert "<strong>Premium is on.</strong>" not in client.get("/").text


# ---- 4. A crawler asking /running-costs for a postcode gets the plain page --

_NOINDEX = '<meta name="robots" content="noindex, follow">'


def _answering(monkeypatch):
    from app import main as app_main
    from app.services import _cache

    calls = []

    async def _lookup(postcode):
        calls.append(postcode)
        return fake_location()

    async def _answer(where, house_number):
        calls.append("answer")
        return {"postcode": "M14 5TG", "district": "Manchester", "outcode": "M14", "house_number": "",
                "latitude": 53.45, "longitude": -2.22, "council_tax": None, "energy": None,
                "home": None, "sales": None, "stamp_duty": None, "rent": None, "district_prices": None,
                "area_prices": None, "broadband": None, "flood": None, "typical_year": None,
                "energy_figure": None, "income_value": None, "income_la": None, "income_la_name": "",
                "typical_share_pct": None}

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "_running_costs_for_postcode", _answer)
    _cache._store.clear()
    _cache._bytes = 0
    return calls


def test_a_crawler_gets_running_costs_without_the_answer(client, monkeypatch):
    calls = _answering(monkeypatch)
    googlebot = {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}
    body = client.get("/running-costs?postcode=M14%205TG", headers=googlebot).text
    assert calls == []
    assert "What it costs to live in M14 5TG" not in body and 'id="checked"' not in body
    assert _NOINDEX in body
    assert 'rel="canonical" href="' in body and '/running-costs"' in body


def test_a_person_still_gets_the_answer_on_a_noindex_url(client, monkeypatch):
    calls = _answering(monkeypatch)
    body = client.get("/running-costs?postcode=M14%205TG").text
    assert calls == ["M14 5TG", "answer"]
    assert "What it costs to live in M14 5TG" in body and _NOINDEX in body
    plain = client.get("/running-costs").text
    assert _NOINDEX not in plain
