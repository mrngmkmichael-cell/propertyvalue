"""The ideas of the 22 Sep 2026 brainstorm, built on 25 Sep at Michael's
"do all"."""
import app.main as app_main


# ---- 1. One flat, one link ------------------------------------------------

def test_a_flat_word_run_into_its_number_is_the_same_flat():
    # The live case: 113 Newton Street, M1 1AE, the homepage's own sample,
    # offered "Apartment18 113" beside "Apartment 18, 113".
    certs = [{"address": "Apartment 18, 113, Newton Street", "certificate_number": "A"}]
    sales = [{"address": "APARTMENT18 113 NEWTON STREET", "street": "NEWTON STREET"},
             {"address": "APARTMENT 1 113 NEWTON STREET", "street": "NEWTON STREET"}]
    homes = app_main._postcode_homes(certs, sales)
    assert [h["short"] for h in homes] == ["Apartment 1, 113", "Apartment 18, 113"]
    # Either spelling opens the same record, and never Apartment 1.
    assert app_main._filter_by_address(sales, "Apartment 18") == [sales[0]]
    assert app_main._filter_by_address(sales, "apartment18") == [sales[0]]
    # Where the run-on sale is the only record, the row still reads it
    # with its space and comma.
    only = app_main._postcode_homes([], sales[:1])
    assert [h["short"] for h in only] == ["Apartment 18, 113"]
    # A word that only starts like one is left alone.
    assert app_main._address_words("Unity House 4") == "unity house 4"
    assert app_main._address_words("Flat2A, 9 Mill Lane") == "flat 2a 9 mill lane"


def test_a_saved_flat_typed_run_on_is_the_same_saved_flat():
    from app.watchlist import same_home
    assert same_home("Apartment18 113", "Apartment 18, 113 Newton Street")
    assert not same_home("Apartment18", "Apartment 1")
    assert not same_home("Flat1", "Flat 12")


# ---- 2. The district's crime month is fetched once, not once a report ------

def test_the_district_crime_summary_is_kept_past_the_memory_tier(monkeypatch):
    import asyncio
    from app.services import _cache, crime, postcodes

    async def centroid(outcode):
        return {"latitude": 51.5, "longitude": -0.09, "admin_district": "Southwark", "country": "England"}

    calls = []

    async def fetch(lat, lon):
        calls.append((lat, lon))
        return {"total": 900, "month": "2026-07", "by_category": [], "points": [{"lat": 1, "lng": 2}],
                "points_capped": True}

    stored = {}
    monkeypatch.setattr(postcodes, "outcode_centroid", centroid)
    monkeypatch.setattr(crime, "_fetch_summary", fetch)
    monkeypatch.setattr(_cache, "get_persistent", lambda key, ttl: stored.get(key))
    monkeypatch.setattr(_cache, "set_persistent", lambda key, value: stored.__setitem__(key, value))
    _cache._store.pop(_cache.coord_key("crime", 51.5, -0.09), None)

    first = asyncio.run(crime.summary_for_outcode("se1"))
    assert first["total"] == 900 and first["points"] == [] and first["points_capped"] is False
    assert stored[("crime_district", "SE1")] == first
    # The next report in the district, with the memory tier gone, asks
    # Police.uk nothing.
    _cache._store.pop(_cache.coord_key("crime", 51.5, -0.09), None)
    assert asyncio.run(crime.summary_for_outcode("SE1")) == first
    assert len(calls) == 1


def test_a_district_without_a_true_count_is_answered_by_todays_rule(monkeypatch):
    import asyncio
    from app.services import _cache, crime, postcodes

    async def centroid(outcode):
        return {"latitude": 53.48, "longitude": -2.24, "admin_district": "Manchester", "country": "England"}

    monkeypatch.setattr(postcodes, "outcode_centroid", centroid)
    # A count stored before the rule must not be served past it.
    monkeypatch.setattr(_cache, "get_persistent", lambda key, ttl: {"total": 5, "month": "2026-07"})
    result = asyncio.run(crime.summary_for_outcode("M1"))
    assert result["total"] is None


# ---- 4. A large council-wide swing says what it rests on -------------------

WESTMINSTER = {"name": "City of Westminster", "average_price": 876788, "annual_change_pct": -20.7,
               "period": "2026-07", "provisional": True, "sales_volume": 84, "sales_volume_period": "2026-05"}


def test_a_large_swing_names_its_sales_and_its_first_estimate():
    note = app_main._hpi_swing_note(WESTMINSTER)
    assert note == ("The index for City of Westminster rests on 84 recorded sales in May 2026, the newest month "
                    "with a count. July 2026 is a first estimate, which HM Land Registry revises as more sales "
                    "are registered.")
    assert "—" not in note and "!" not in note
    lead = " ".join(app_main._area_lead("SW1A", {"hpi": {"local_authority": WESTMINSTER}}))
    assert "down 20.7% on a year ago (UK House Price Index). The index for City of Westminster rests on 84" in lead


def test_an_ordinary_move_or_an_old_payload_says_nothing_more():
    assert app_main._hpi_swing_note(dict(WESTMINSTER, annual_change_pct=4.1)) is None
    # Payloads warmed before 25 Sep 2026 carry no count: no sentence, not
    # a guess.
    old = {k: WESTMINSTER[k] for k in ("name", "average_price", "annual_change_pct", "period")}
    assert app_main._hpi_swing_note(old) is None
    # A counted month with no provisional one says the count alone.
    firm = dict(WESTMINSTER, provisional=False)
    assert app_main._hpi_swing_note(firm).endswith("the newest month with a count.")


def test_the_index_reads_the_count_from_the_newest_month_that_has_one():
    import asyncio
    from app.services import hpi

    def row(month, change, volume=None):
        r = {"refMonth": {"value": month}, "averagePrice": {"value": "876788"},
             "percentageAnnualChange": {"value": str(change)}, "label": {"value": "City of Westminster"}}
        if volume is not None:
            r["salesVolume"] = {"value": str(volume)}
        return r

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": {"bindings": [row("2026-07", -20.7), row("2026-06", -24.2), row("2026-05", -20.5, 84)]}}

    class Client:
        async def get(self, *a, **k):
            return Response()

    got = asyncio.run(hpi._latest_for_area(Client(), "City of Westminster"))
    assert got["period"] == "2026-07" and got["provisional"] is True
    assert got["sales_volume"] == 84 and got["sales_volume_period"] == "2026-05"
