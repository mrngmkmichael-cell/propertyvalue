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
    # batch D (18 Sep 2026) put the radius and month on the card itself
    assert "1,330 within about a mile, July 2026" in card[:card.index("</button>")]
    modal = body[body.index('id="modal-crime"'):]
    modal = modal[:modal.index("</dialog>")]
    assert "1,330 crimes recorded within about a mile in July 2026" in modal
    assert "against 2,104 in the wider SE15 postcode area" in modal
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


# ---- 5. /admin names who is fetching pages ------------------------------------

def test_an_agent_is_named_by_family_never_by_its_string():
    from app import main as app_main

    family = app_main._agent_family
    assert family("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)") == "Googlebot"
    assert family("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)") == "GPTBot"
    assert family("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/140.0 Safari/537.36") == "Looks like a browser"
    assert family("python-requests/2.32") == "Python script"
    assert family("") == "No user agent" and family(None) == "No user agent"
    assert family("SomeCrawler/1.0 (+crawler)") == "Other bot or script"


def test_a_page_is_counted_by_family_never_by_its_address():
    from app import main as app_main

    page = app_main._page_family
    assert page("/", False) == "/"
    assert page("/area/M1", False) == "/area/…"
    assert page("/compare/M14/vs/M20", False) == "/compare/…"
    assert page("/schools/guide", True) == "/schools/guide?…"
    assert page("/schools/admissions/kent", False) == "/schools/admissions/…"
    assert page("/running-costs", True) == "/running-costs?…"
    assert page("/running-costs/council-tax/leeds", False) == "/running-costs/council-tax/…"


def test_the_counter_keeps_a_day_and_leaves_our_own_checks_out(client, monkeypatch):
    from app import main as app_main

    monkeypatch.setattr(app_main, "_agent_counts", {})
    googlebot = {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}
    client.get("/running-costs", headers=googlebot)
    client.get("/running-costs", headers=dict(googlebot, **{"X-Internal-Check": "1"}))
    client.get("/static/css/style.css", headers=googlebot)
    rows = app_main._agent_summary()["rows"]
    assert [(r["family"], r["total"]) for r in rows] == [("Googlebot", 1)]
    assert rows[0]["pages"] == [("/running-costs", 1)]
    # A day later the hour has left the window.
    later = app_main._agent_summary(now=__import__("time").time() + 25 * 3600)
    assert later["rows"] == [] and later["total"] == 0


def test_admin_shows_who_is_fetching(client, monkeypatch):
    from app import main as app_main

    monkeypatch.setattr(app_main, "_agent_counts", {})
    client.get("/running-costs", headers={"User-Agent": "Mozilla/5.0 (compatible; bingbot/2.0)"})
    monkeypatch.setenv("ADMIN_EMAIL", "boss-agents@example.test")
    client.post("/signup", data={"email": "boss-agents@example.test", "password": "correct horse battery staple"},
                follow_redirects=False)
    body = client.get("/admin").text
    section = body[body.index('id="agents"'):]
    section = section[:section.index("</section>")]
    assert "Who is fetching pages" in section and "Bingbot" in section
    assert "<code>/running-costs</code> 1" in section


# ---- 6. The "official" strip lists only official bodies -----------------------

def test_the_official_strip_names_only_official_bodies(client):
    body = client.get("/").text
    strip = body[body.index('class="sources-strip"'):]
    strip = strip[:strip.index("</section>")]
    names = re.findall(r'class="sources-strip-name">([^<]+)<', strip)
    # Two passes of one list make the ticker loop.
    first_pass = names[:len(names) // 2]
    assert first_pass == names[len(names) // 2:]
    assert "OpenStreetMap" not in first_pass and "Defra" in first_pass
    # The figure quoted on the same page is the strip's own length, which
    # since 18 Sep 2026 (first-visitor audit item D6) is OFFICIAL_SOURCES
    # in main.py rather than a typed thirteen.
    from app import main as app_main
    count = len(app_main.OFFICIAL_SOURCES)
    assert len(first_pass) == count and f"{count} official sources" in body
    note = strip[strip.index('class="sources-strip-note"'):]
    assert "apart from what is nearby and which way a home faces" in " ".join(note.split())
    assert "OpenStreetMap, the map its volunteers draw" in " ".join(note.split())


# ---- 7. Dates and counts the way the rest of the site writes them ----------

def _copy_rules():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "copy_rules.py"
    spec = importlib.util.spec_from_file_location("copy_rules_for_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_audit_rule_finds_iso_dates_and_bare_counts_and_nothing_else():
    rules = _copy_rules()
    found = rules.date_and_count_problems
    assert found("patients registered at a GP practice (2026-08-01)") == [("date in ISO form", "2026-08-01")]
    assert found("as of 2026-06, according to") == [("date in ISO form", "2026-06")]
    assert found("1330 crimes recorded") == [("count without a thousands separator", "1330 crimes")]
    # Financial years, council tax years, real prose and separated counts pass.
    for fine in ("every year since 2008-09", "Band D for 2026-27", "rises since 2011-12",
                 "1,330 crimes recorded", "in 2026 sales rose", "3 Jul 2026", "July 2026",
                 "E01032946 is the neighbourhood", "4,965 district comparisons"):
        assert found(fine) == [], fine


def test_a_report_prints_sale_dates_and_the_hpi_month_in_words(client, fake_report):
    fake_report(gather=fake_gather(transactions=[
        {"address": "1 Test Street", "postcode": "M14 5TG", "amount": "250000", "date": "2024-06-01"},
    ]))
    body = client.get("/property?postcode=M14%205TG").text
    assert "1 Jun 2024" in body and ">2024-06-01<" not in body
    rules = _copy_rules()
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
    text = re.sub(r"<[^>]+>", " ", text)
    assert [hit for hit in rules.date_and_count_problems(text) if hit[1].startswith("2024")] == []


def test_the_accuracy_log_dates_its_entries_in_words(client):
    body = client.get("/accuracy").text
    assert "18 Sep 2026" in body and "27 Aug 2026" in body
    assert "&middot; 2026-09-18" not in body


def test_a_guide_answers_with_a_month_not_a_database_field(client, monkeypatch):
    payload = {"has_data": True, "hpi": {"local_authority": {"name": "Leeds", "average_price": 249394,
                                                            "annual_change_pct": 5.9, "period": "2026-06-01"}}}
    body = _guide(client, monkeypatch, "LS6", "Leeds", "England", payload)
    assert "is £249,394 as of June 2026" in body and "as of 2026-06" not in body


def test_the_share_card_counts_the_checks_the_site_counts():
    import pathlib
    from app import main as app_main

    source = pathlib.Path("app/services/og_image.py").read_text(encoding="utf-8")
    # The drawing code, not the docstring that tells its history.
    assert "\"40 CHECKS  ·" not in source
    assert 'f"{check_count} CHECKS  ·  "' in source
    assert "check_count=CHECK_COUNT" in pathlib.Path("app/main.py").read_text(encoding="utf-8")
    assert app_main.CHECK_COUNT == 44


# ---- 8. What Premium covers outside England ----------------------------------

def test_every_premium_check_says_where_its_source_reaches():
    from app import main as app_main

    titles = [title for _, title, _, _ in app_main.PREMIUM_CHECKS]
    assert sorted(app_main.PREMIUM_REACH) == sorted(titles)
    assert app_main.premium_reach("England") is None and app_main.premium_reach(None) is None
    wales = app_main.premium_reach("Wales")
    assert (wales["reach"], wales["total"]) == (10, 15)
    assert wales["missing"] == ["Sewage Discharge", "Historic Contamination", "School Catchment Areas",
                                "Development Nearby", "Health Services"]
    assert app_main.premium_reach("Scotland")["reach"] == 7
    assert app_main.premium_reach("Northern Ireland")["reach"] == 4
    # A name with its own comma is never read as two checks.
    assert "Health, Relationships & Social Grade; Development Nearby" in app_main.premium_reach_sentence("Scotland")


def test_the_three_england_only_sources_are_not_asked_elsewhere():
    from app import main as app_main

    async def _boom():
        raise AssertionError("asked about a place its source does not cover")

    for country in ("Wales", "Scotland", "Northern Ireland"):
        assert asyncio.run(app_main._in_england_only(country, _boom, [])) == []
        assert asyncio.run(app_main._in_england_only(country, _boom)) is None

    async def _answer():
        return {"status": "clear"}

    assert asyncio.run(app_main._in_england_only("England", _answer)) == {"status": "clear"}


def _welsh():
    location = fake_location(country="Wales", postcode="CF63 4AA", outcode="CF63")
    location["admin_district"] = "Vale of Glamorgan"
    return location


def test_the_wall_names_what_premium_cannot_read_in_wales(client, fake_report):
    fake_report(location=_welsh())
    body = " ".join(client.get("/property?postcode=CF63%204AA").text.split())
    assert "In Wales, 10 of the 15 Premium checks have a source to read." in body
    assert "Development Nearby and Health Services draw on records that do not cover Wales." in body
    fake_report()
    assert "Premium checks have a source to read" not in client.get("/property?postcode=M14%205TG").text


def test_a_welsh_subscriber_is_told_not_covered_rather_than_none(client, fake_report):
    _subscriber(client, "cf63-subscriber@customer.test")
    gather = fake_gather(sewage=None, sewage_outfalls=[], historic_landfill=None, health=None)
    fake_report(location=_welsh(), gather=gather)
    body = client.get("/property?postcode=CF63%204AA").text
    for title in ("Sewage Discharge", "Historic Contamination", "Health Services"):
        card = body[body.index(f'dashboard-card-title">{title}'):]
        card = card[:card.index("</button>")]
        assert "Not covered in Wales" in card, title
    for gone in ("No outfalls found nearby", "No GP practice within 3 km"):
        assert gone not in body
    assert "This is not a finding that there is no former landfill here." in body
    assert "This is not a finding that there are no overflows here." in body
    assert "This is not a finding that there is no practice nearby." in body


def test_the_pdf_says_not_covered_and_never_calls_no_answer_good():
    from app.services import pdf_checklist

    rows = pdf_checklist.build({"location": {"country": "Wales"}, "sewage_outfalls": [],
                                "historic_landfill": None, "health": None}, {})
    by_check = {r["check"]: r for r in rows}
    for check in ("Storm overflows nearby", "Historic landfill", "GP practices and A&E"):
        assert by_check[check]["result"] == "Not covered in Wales", check
        assert by_check[check]["status"] == "neutral"
    # In England, a landfill check with no answer is "Not available", not "None nearby".
    rows = pdf_checklist.build({"location": {"country": "England"}, "historic_landfill": None}, {})
    assert {r["check"]: r for r in rows}["Historic landfill"]["result"] == "Not available"


def test_premium_says_where_each_check_reaches(client, monkeypatch):
    from app import main as app_main

    # The check list shows once billing is configured, as in production.
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_m")
    monkeypatch.setenv("STRIPE_PRICE_ID_QUARTERLY", "price_q")
    monkeypatch.delenv("STRIPE_PRICE_ID_PASS", raising=False)
    _forget_html()
    body = " ".join(client.get("/premium").text.split())
    assert "Council admissions data &middot; England only" in body
    assert "UK House Price Index &middot; All four nations" in body
    assert "I am buying outside England. What does Premium cover there?" in body
    assert app_main.premium_reach_summary() in body.replace("&amp;", "&")
