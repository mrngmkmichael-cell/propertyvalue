"""The interactive items of the 16 Sep 2026 first-time visitor audit
(docs/audits/2026-09-16-first-visitor-audit.md): the score's reasons open
their cards, the running-costs answer follows the band the reader picks,
an area guide compares two districts, the comparables page narrows its
sales by year on the table and both maps, and a signed-in return visit
says what changed and opens that group."""
import re

from tests.conftest import fake_gather, fake_location
from tests.test_pages import _signed_in
from tests.test_property_page import _report


def _forget_html():
    """The anonymous-HTML cache is process-wide, so two requests for one
    URL inside a test would otherwise return the first rendering."""
    from app.services import _cache
    for key in [k for k in _cache._store if isinstance(k, tuple) and k and k[0] == "anon_html"]:
        _cache._evict(key)


# ---- 1. The verdict's reasons open their cards ---------------------------

def test_the_scores_reasons_carry_the_card_each_came_from():
    from app.services import overview_score

    out = overview_score.compute({
        "school_landscape": {"good_or_better_pct": 90},
        "hpi": {"local_authority": {"name": "X", "annual_change_pct": 2.0}},
        "flood_zone": {"zone": 3},
        "noise": {"road_db": 70},
    })
    reasons = out["reasons"]
    # The same texts as the sentence, in the same order, each with a card.
    assert [r["text"] for r in reasons["positives"]] == out["positives"]
    assert [r["text"] for r in reasons["concerns"]] == out["concerns"]
    by_text = {r["text"]: r["modal"] for r in reasons["positives"] + reasons["concerns"]}
    assert by_text["Flood risk"] == "modal-flood"
    assert by_text["High noise levels"] == "modal-noise"
    assert any(m == "modal-schools" for m in by_text.values())
    assert any(m == "modal-sold-price-history" for m in by_text.values())
    # Every card a reason can point at exists on the report.
    from pathlib import Path
    template = (Path(__file__).resolve().parent.parent / "app" / "templates" / "property.html").read_text(encoding="utf-8")
    for modal in set(overview_score.CONCERN_MODALS.values()) | {"modal-schools", "modal-epc", "modal-crime", "modal-sold-price-history"}:
        assert f'id="{modal}"' in template, modal


def test_the_report_makes_each_reason_a_button_to_its_card(client, fake_report):
    body = _report(client, fake_report, gather=fake_gather(overview={
        "score": 62, "grade": "Fair",
        "verdict": "Fair overall: 79% of nearby schools rated Outstanding or Good, balanced against 1 thing worth checking (Flood risk).",
        "positives": ["79% of nearby schools rated Outstanding or Good"],
        "concerns": ["Flood risk"],
        "reasons": {
            "positives": [{"text": "79% of nearby schools rated Outstanding or Good", "modal": "modal-schools"}],
            "concerns": [{"text": "Flood risk", "modal": "modal-flood"}],
        },
        "premium_extra_checks": 1,
    }))
    verdict = re.search(r'<p class="overview-score-verdict">(.*?)</p>', body, re.S).group(1)
    assert 'class="verdict-reason" data-modal-target="modal-schools"' in verdict
    assert 'class="verdict-reason" data-modal-target="modal-flood"' in verdict
    assert "Fair overall:" in verdict and "1 thing worth checking" in verdict
    assert 'id="modal-schools"' in body and 'id="modal-flood"' in body
    # A gather from before the reasons existed still reads as a sentence.
    body = _report(client, fake_report)
    assert 'class="verdict-reason"' not in body
    assert "79% of nearby schools rated Outstanding or Good" in body


# ---- 2. The running-costs answer follows the band -----------------------

def test_the_running_costs_answer_follows_the_band_the_reader_picks(client, monkeypatch):
    from app import main as app_main
    from app.services import _cache

    ninths = dict(zip("ABCDEFGH", (6, 7, 8, 9, 11, 13, 15, 18)))

    async def _lookup(_postcode):
        return fake_location()

    async def _answer(where, house_number):
        return {
            "postcode": "M14 5TG", "district": "Manchester", "outcode": "M14", "house_number": "",
            "latitude": 53.45, "longitude": -2.22,
            "council_tax": {"authority": "Manchester", "band_d": 2000.0, "year": "2026-27",
                            "bands": {b: round(2000 * n / 9, 2) for b, n in ninths.items()}},
            "energy": {"certificates": 5, "priced": 5, "low": 900, "high": 1500, "median": 1200,
                       "median_potential": 1000, "bands": "C x5"},
            "home": None, "sales": None, "stamp_duty": None, "rent": None, "district_prices": None,
            "area_prices": None, "broadband": None, "flood": None,
            "typical_year": 3200, "energy_figure": 1200, "income_value": 40000,
            "income_la": None, "income_la_name": "", "typical_share_pct": 8.0,
        }

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "_running_costs_for_postcode", _answer)
    _cache._store.clear()
    _cache._bytes = 0
    body = client.get("/running-costs?postcode=M14%205TG").text
    assert 'id="rc-total">' in body and "3,200 a year" in body
    assert 'id="rc-share">8.0</span>%' in body
    assert 'Band <span id="rc-band-label">D</span> council tax' in body
    # Named since 18 Sep 2026 (F4): the picker is a plain form without a script.
    assert '<select id="rc-band" name="band">' in body and body.count('<option value="') == 8
    assert '<option value="D" selected>' in body
    assert 'data-energy="1200"' in body and 'data-income="40000"' in body
    assert "Not Band D? Pick the home" in body and "on the seller" in body
    # Nothing is estimated: the bands in the picker are the council's own.
    assert '"H": 4000.0' in body


def test_no_band_picker_without_a_bill_to_add_it_to(client, monkeypatch):
    from app import main as app_main
    from app.services import _cache

    async def _lookup(_postcode):
        return fake_location()

    async def _answer(where, house_number):
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
    body = client.get("/running-costs?postcode=M14%205TG").text
    assert 'class="rc-band-pick"' not in body and 'id="rc-band"' not in body


# ---- 3. Two districts on an area guide ------------------------------------

_M20 = {
    "local_sales": {"enough_for_median": True, "median": 412500, "count": 63, "low": 150000, "high": 900000},
    "hpi": {"local_authority": {"name": "Manchester", "annual_change_pct": 3.4, "average_price": 260000, "period": "2026-06"}},
    "landscape": {"good_or_better_pct": 84, "total_schools": 61, "radius_miles": 3},
    "flood_zone": {"zone": 1, "label": "Flood Zone 1, low risk"},
    "finance": {"name": "Manchester", "latest_label": "2026-27", "history": [{"label": "2026-27", "band_d": 1978.0, "rise": 4.99}]},
    "crime": {"total": 214, "month": "2026-05", "by_category": [{"category": "Violence and sexual offences"}]},
}


def test_area_figures_line_up_two_districts_row_for_row():
    from app import main as app_main

    rows = app_main._area_figures("M20", _M20)
    assert [r["label"] for r in rows] == [
        "Sold prices around the centre", "Prices on a year ago", "Schools rated Good or Outstanding",
        "Flood zone at the centre", "Band D council tax", "Crimes within about a mile",
    ]
    assert [r["source"] for r in rows] == [
        "HM Land Registry", "UK House Price Index", "Ofsted", "Environment Agency", "MHCLG", "Police.uk",
    ]
    values = [r["value"] for r in rows]
    assert values[0] == "£412,500 median of 63 sales"
    # The month is named, so a guide and a comparison gathered either side
    # of a UK House Price Index release do not look like a contradiction.
    assert values[1] == "+3.4% (Manchester, June 2026)"
    assert values[2] == "84% of 61 within 3 miles"
    assert values[3] == "Flood Zone 1, low risk"
    assert values[4] == "£1,978 a year (Manchester, 2026-27)"
    assert values[5] == "214 in May 2026"
    # A source with nothing for the district says so in words, never a blank.
    assert [r["value"] for r in app_main._area_figures("IV27", {})] == ["Not held"] * 6
    # Outside England the flood row says the EA map does not reach, never Zone 1.
    assert app_main._area_figures("IV27", _M20, "Scotland")[3]["value"] == "Not mapped for Scotland"
    # The district average stands in when the streets around the centre have too few sales.
    thin = app_main._area_figures("IV27", {"local_sales": {"enough_for_median": False, "count": 2},
                                           "hpi": {"local_authority": {"name": "Highland", "average_price": 187000, "annual_change_pct": -1.2}}})
    assert thin[0]["value"] == "£187,000 district average"
    assert thin[1]["value"] == "-1.2% (Highland)"


def test_an_area_guide_compares_two_districts_and_stays_out_of_the_index(client, monkeypatch):
    from app import main as app_main

    # Both districts come from the same fake gather: the test is the page
    # around the figures, not the figures. Nothing is written to the
    # persistent cache because the fake build never calls set_persistent.
    async def _resolve(outcode):
        return fake_location(postcode=f"{outcode} 2AA", outcode=outcode), True

    async def _build(outcode, location, key):
        return dict(_M20, has_data=True)

    real_get = app_main._cache.get_persistent

    def _get(key, ttl):
        if isinstance(key, tuple) and key and key[0] == "area_guide":
            return None
        return real_get(key, ttl)

    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    monkeypatch.setattr(app_main, "_build_area_payload", _build)
    monkeypatch.setattr(app_main._cache, "get_persistent", _get)

    _forget_html()
    plain = client.get("/area/AB12").text
    assert 'id="compare"' in plain and 'name="compare"' in plain
    assert "Compare AB12 with another district" in plain
    assert "noindex" not in plain.lower()

    _forget_html()
    body = client.get("/area/AB12?compare=m20").text
    assert "AB12 against M20" in body
    assert '<meta name="robots" content="noindex, follow">' in body
    assert "£412,500 median of 63 sales" in body
    # The fake location sits in Manchester, where Police.uk's trickle is
    # not shown as a count (18 Sep 2026); _area_figures above pins the
    # count and its month for everywhere else.
    assert "214 in May 2026" not in body and "Not published in full" in body
    assert 'href="/area/M20"' in body
    assert '<th scope="col">M20 (Manchester)</th>' in body
    assert "Not held" in body or "median of" in body
    # The canonical stays the guide's own address: the comparison is a view of it.
    assert re.search(r'rel="canonical" href="[^"]*/area/AB12"', body) and 'compare=' not in re.search(r'rel="canonical" href="([^"]*)"', body).group(1)

    for query, words in (("AB12", "That is AB12 itself"), ("not-a-district", "not a postcode district")):
        _forget_html()
        body = client.get(f"/area/AB12?compare={query}").text
        assert words in body, query
        assert "noindex" in body.lower()
        assert 'class="tx-table compare-table"' not in body


# ---- 4. Sales since, on the comparables page ------------------------------

def test_the_comparables_page_narrows_sales_by_year_on_the_table_and_both_maps(client, monkeypatch):
    from app import main as app_main

    async def _lookup(_postcode):
        return fake_location()

    async def _nearby(lat, lon, **kwargs):
        return [{"postcode": "M14 5TG", "distance_m": 0, "latitude": 53.45, "longitude": -2.22}]

    async def _sold(postcodes):
        return [
            {"address": "1 Test Street", "postcode": "M14 5TG", "amount": "250000", "date": "2024-06-01",
             "property_type": "flat", "tenure": "Leasehold"},
            {"address": "3 Test Street", "postcode": "M14 5TG", "amount": "180000", "date": "2015-03-01",
             "property_type": "flat", "tenure": "Leasehold"},
        ]

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "nearby_postcodes", _nearby)
    monkeypatch.setattr(app_main, "sold_prices_for_postcodes", _sold)

    _forget_html()
    body = client.get("/property/comparables?postcode=M14%205TG").text
    assert 'data-date="2024-06-01"' in body and 'data-date="2015-03-01"' in body
    assert 'class="comp-range" role="group" aria-label="Sales since"' in body
    assert body.count("data-years=") == 4 and 'data-years="0" aria-pressed="true"' in body
    assert 'id="comp-range-count"' in body
    assert "function compApplyRange" in body
    # Dev has no key, so the Leaflet branch renders: its pins register with the filter.
    assert "compMarkers.push" in body and "compMap.removeLayer(pin)" in body
    # Production renders the Google branch: change both, or production shows nothing.
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    _forget_html()
    body = client.get("/property/comparables?postcode=M14%205TG").text
    assert "compMarkers.push" in body and "marker.setMap(visible ? map : null)" in body
    assert "compMap.removeLayer" not in body


# ---- 5. A return visit opens on what changed ------------------------------

def test_the_report_summary_reads_the_same_fields_the_watchlist_compares():
    from app import main as app_main

    summary = app_main._summary_from_report(fake_gather(), "M14 5TG", "")
    assert summary["postcode"] == "M14 5TG" and summary["house_number"] == ""
    assert summary["tx_count"] == 1 and summary["avg_price"] == 250000
    assert summary["flood_zone"] == "Zone 1 (low probability)"
    assert summary["crime_total"] == 120
    assert summary["epc_date"] == "2025-12-16"
    assert summary["price_growth_pct"] == 4.1 and summary["price_growth_area"] == "Manchester"
    # A source that failed on this render leaves its key out, and the
    # comparison then says nothing about it rather than reporting a change.
    failed = app_main._summary_from_report({"tx_error": True, "transactions": []}, "M14 5TG", "")
    assert "tx_count" not in failed and "avg_price" not in failed
    assert app_main._snapshot_changes(summary, failed) == []
    assert app_main._snapshot_changes(failed, summary) == []


def test_the_group_that_opens_follows_the_first_change():
    from app import main as app_main

    assert app_main._group_for_changes(["Average sold price changed from £1 to £2"]) == "cat-value-market"
    assert app_main._group_for_changes(["Flood zone changed from Zone 1 to Zone 3"]) == "cat-risk-safety"
    assert app_main._group_for_changes(["Recorded crime nearby up by 9 since last checked"]) == "cat-risk-safety"
    assert app_main._group_for_changes(["A new energy certificate was lodged, often a sign the property is being prepared for sale"]) == "cat-property-condition"
    assert app_main._group_for_changes(["Area house-price trend flipped: prices are growing again (+1.2% YoY)"]) == "cat-value-market"
    assert app_main._group_for_changes([]) == ""


def test_a_return_visit_says_what_changed_and_opens_that_group(client, fake_report):
    _signed_in(client, "return-visit@example.com")
    try:
        first = _report(client, fake_report)
        assert 'class="since-visit"' not in first
        assert 'data-open-group=""' in first
        assert "root.getAttribute('data-open-group')" in first

        # Later, a new sale that moves the average: Value & Market opens.
        later = fake_gather(transactions=[
            {"address": "1 Test Street", "postcode": "M14 5TG", "amount": "250000", "date": "2024-06-01"},
            {"address": "1 Test Street", "postcode": "M14 5TG", "amount": "290000", "date": "2026-08-01"},
        ])
        second = _report(client, fake_report, gather=later)
        assert "Since you last looked:" in second
        assert "1 new sold price recorded here since you last looked" in second
        assert "Average sold price changed from £250,000 to £270,000" in second
        assert 'data-open-group="cat-value-market"' in second

        # The snapshot moved on, so the same data a third time has nothing to say.
        third = _report(client, fake_report, gather=later)
        assert 'class="since-visit"' not in third
        assert 'data-open-group=""' in third

        # A flood zone change opens Risk & Safety.
        flooded = fake_gather(transactions=later["transactions"],
                              flood_zone={"zone": 3, "label": "Zone 3 (high probability)", "source": "river"})
        fourth = _report(client, fake_report, gather=flooded)
        assert "Flood zone changed from Zone 1 (low probability) to Zone 3 (high probability)" in fourth
        assert 'data-open-group="cat-risk-safety"' in fourth
    finally:
        client.cookies.clear()


def test_a_first_visit_and_an_anonymous_visit_carry_no_change_line(client, fake_report):
    body = _report(client, fake_report)
    assert 'class="since-visit"' not in body
    assert 'data-open-group=""' in body
