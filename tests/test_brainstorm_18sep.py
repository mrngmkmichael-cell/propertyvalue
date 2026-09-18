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
