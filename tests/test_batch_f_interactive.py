"""Batch F of the 17 Sep 2026 first-visitor audit: the interactive tools
(docs/audits/2026-09-17-first-visitor-audit.md, "How the site could be
more interactive"). Each section pins one tool. The owner's brief: he
loves interactive, and conversion here is a return visit, so each tool
gives a buyer a reason to come back with their own homes in hand."""
import datetime
import json
import pathlib
import re

from sqlalchemy import select

from app import main as app_main
from tests.conftest import fake_location
from tests.test_pages import _signed_in

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ==== F1. Is the asking price in line? On the comparables page ============
# /property/comparables worked its median, range, "sits above" sentence and
# bar out once over every sale on the page, commercial "Other" sales among
# them, and the year buttons never moved a figure. Type chips, a tenure
# toggle and the year buttons now choose the rows every figure describes,
# an asking price is placed among them with its stamp duty, the price
# travels in the address, and a signed-in buyer can keep it with a saved
# home, where My properties and the comparison show it. The interactions
# are checked in a browser; these pin what the server renders and stores.

F1_TODAY = datetime.date.today()


def _ago(days: int) -> str:
    return (F1_TODAY - datetime.timedelta(days=days)).isoformat()


def _f1_sales():
    """Eleven sales: five semis at the home's own postcode (12 Test Road
    among them), two detached, a terrace, two flats and one commercial
    sale Land Registry files as Other."""
    def sale(address, postcode, amount, days, ptype, tenure):
        return {"address": address, "postcode": postcode, "amount": str(amount), "date": _ago(days),
                "property_type": ptype, "tenure": tenure, "new_build": False}
    here, near = "M14 5TG", "M14 5TH"
    return [
        sale("10 TEST ROAD", here, 400000, 100, "semi-detached", "Freehold"),
        sale("12 TEST ROAD", here, 500000, 200, "semi-detached", "Freehold"),
        sale("14 TEST ROAD", here, 600000, 3 * 365 + 20, "semi-detached", "Freehold"),
        sale("16 TEST ROAD", here, 700000, 7 * 365, "semi-detached", "Leasehold"),
        sale("18 TEST ROAD", here, 800000, 15 * 365, "semi-detached", "Freehold"),
        sale("1 FAR LANE", near, 900000, 365, "detached", "Freehold"),
        sale("3 FAR LANE", near, 1200000, 6 * 365, "detached", "Freehold"),
        sale("5 FAR LANE", near, 350000, 550, "terraced", "Freehold"),
        sale("FLAT 1 FAR COURT", near, 250000, 50, "flat-maisonette", "Leasehold"),
        sale("FLAT 2 FAR COURT", near, 300000, 4 * 365, "flat-maisonette", "Leasehold"),
        sale("GOALS SOCCER CENTRE WYVERN ESTATE", near, 2700000, 365, "other", "Freehold"),
    ]


def _f1_install(monkeypatch, country="England"):
    from app.services import _cache

    async def _lookup(_postcode):
        return fake_location(country=country)

    async def _nearby(lat, lon, **kwargs):
        return [{"postcode": "M14 5TG", "distance_m": 0, "latitude": 53.45, "longitude": -2.22},
                {"postcode": "M14 5TH", "distance_m": 300, "latitude": 53.452, "longitude": -2.221}]

    async def _sold(postcodes):
        return _f1_sales()

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "nearby_postcodes", _nearby)
    monkeypatch.setattr(app_main, "sold_prices_for_postcodes", _sold)
    for key in (app_main._comparables_page_key(53.45, -2.22), app_main._comparables_key(53.45, -2.22)):
        _cache._evict(key)


def _f1_page(client, monkeypatch, query="", country="England"):
    _f1_install(monkeypatch, country)
    r = client.get(f"/property/comparables?postcode=M14%205TG{query}")
    assert r.status_code == 200
    return r.text.replace("\r\n", "\n")


def _flat(text):
    return " ".join(text.split())


def _rows(body):
    table = body.split('id="comparables-table"', 1)[1].split("</table>", 1)[0]
    return re.findall(r"<tr data-date=\"([^\"]*)\"( hidden)?>(.*?)</tr>", table, re.S)


def _tool(body):
    """The tool's own markup, from its heading to the table."""
    return body.split('id="asking"', 1)[1].split('id="comparables-table"', 1)[0]


def test_f1_each_sale_carries_the_type_tenure_price_and_date_the_script_reads(client, monkeypatch):
    body = _f1_page(client, monkeypatch)
    rows = _rows(body)
    assert len(rows) == 11, "every sale is in the page, with or without JavaScript"
    by_address = {re.search(r'data-st="sub">([^<]+)', inner).group(1): (date, hidden, inner)
                  for date, hidden, inner in rows}
    date, hidden, inner = by_address["12 TEST ROAD"]
    assert date == _ago(200) and not hidden
    assert 'data-label="Type" data-type="semi-detached"' in inner
    assert 'data-label="Tenure" data-tenure="freehold"' in inner
    assert 'data-st="lead" data-price="500000">£500,000<' in inner
    # Land Registry's flat-maisonette is the Flat chip.
    assert 'data-type="flat"' in by_address["FLAT 1 FAR COURT"][2]
    assert 'data-tenure="leasehold"' in by_address["FLAT 1 FAR COURT"][2]
    # The commercial sale is Other, and Other starts unticked, so its row
    # starts hidden and the figures leave it out.
    date, hidden, inner = by_address["GOALS SOCCER CENTRE WYVERN ESTATE"]
    assert hidden and 'data-type="other"' in inner and 'data-price="2700000"' in inner
    # The chips, the toggle and the one line about Other.
    tool = _tool(body)
    for t in ("detached", "semi-detached", "terraced", "flat"):
        assert f'name="type" value="{t}" checked>' in tool, t
    assert 'name="type" value="other"><span>Other</span>' in tool
    assert "Land Registry's Other covers commercial and unusual sales, so it starts unticked." in tool
    assert 'name="tenure" value="any" checked><span>Any</span>' in tool
    assert 'name="tenure" value="freehold"><span>Freehold</span>' in tool
    assert '<form class="comp-filters" id="comp-filters" method="get" action="/property/comparables#asking">' in tool
    # Without JavaScript the form still says no type when none is ticked.
    assert '<input type="hidden" name="type" value="">' in tool


def test_f1_the_figures_describe_the_chosen_sales_not_all_of_them(client, monkeypatch):
    body = _flat(_f1_page(client, monkeypatch))
    # Ten homes: the £2.7m commercial sale is out of the median and range.
    assert ('Median: <span id="comp-median">£600,000</span></strong><span id="comp-median-words">, '
            'the middle of the 10 nearby sold prices for houses and flats.</span>') in body
    # No house number, so the postcode's newest sale places the line.
    assert "£400,000 sits above 30% of the 10 nearby sold prices for houses and flats, from £250,000 to £1,200,000." in body
    assert "£2,700,000" not in body.split('id="comparables-table"')[0].split('id="asking"')[1]
    assert 'All 11 sales' not in body and "10 of 11 sales" in body


def test_f1_a_known_home_starts_on_its_own_type(client, monkeypatch):
    body = _flat(_f1_page(client, monkeypatch, "&house_number=12"))
    tool = _tool(body)
    assert 'name="type" value="semi-detached" checked>' in tool
    assert 'name="type" value="detached"><span>' in tool and 'name="type" value="flat"><span>' in tool
    assert "Semi-detached is ticked to match this home's own recorded sale." in tool
    assert '£500,000 (for "12") sits above 20% of the 5 nearby sold prices for semi-detached houses, from £400,000 to £800,000.' in body
    assert '<span id="comp-median">£600,000</span>' in body
    # The line and the reference sentence share one wording: the script
    # takes its lead from the page rather than typing the words again.
    assert 'data-lead="£500,000 (for &quot;12&quot;) sits above"' in body
    assert "sits above" not in body.split("const comparablePoints", 1)[1].split("</script>", 1)[0]


def test_f1_choices_in_the_address_are_worked_out_on_the_server(client, monkeypatch):
    # Leasehold homes of every kind: the semi at 16 and the two flats.
    body = _flat(_f1_page(client, monkeypatch, "&tenure=leasehold"))
    assert ", the middle of the 3 nearby sold prices for leasehold houses and flats.</span>" in body
    assert '<span id="comp-median">£300,000</span>' in body
    # Semis in the last two years: 10 and 12 Test Road.
    body = _flat(_f1_page(client, monkeypatch, "&house_number=12&years=2"))
    assert ", the middle of the 2 nearby sold prices for semi-detached houses in the last 2 years.</span>" in body
    assert 'data-years="2" aria-pressed="true"' in body
    assert sum(1 for _, hidden, _ in _rows(body)) == 11
    assert sum(1 for _, hidden, _ in _rows(body) if not hidden) == 2
    # An empty type choice is no type, not the defaults: nothing to place.
    body = _flat(_f1_page(client, monkeypatch, "&type="))
    assert '<p class="notice" id="comp-empty">No recorded sale here matches these choices' in body
    assert all(hidden for _, hidden, _ in _rows(body))


def test_f1_a_year_button_that_would_keep_every_sale_is_hidden(client, monkeypatch):
    # Both flats sold within five years, so Last 10 and Last 5 would change
    # nothing; asking for 5 reads as all years.
    body = _f1_page(client, monkeypatch, "&type=flat&years=5")
    assert re.search(r'data-years="10" aria-pressed="false" hidden>', body)
    assert re.search(r'data-years="5" aria-pressed="false" hidden>', body)
    assert re.search(r'data-years="2" aria-pressed="false">', body)
    assert 'data-years="0" aria-pressed="true">' in body
    assert "2 of 11 sales" in body
    # One sale is one sale, not "the middle of the 1".
    body = _flat(_f1_page(client, monkeypatch, "&type=flat&years=2&price=850000"))
    assert '<span id="comp-median-words">, from the one nearby sold price for flats in the last 2 years.</span>' in body
    assert "£850,000 is above the one nearby sold price for flats in the last 2 years, £250,000." in body
    # With every type ticked no button covers all 11, so all four show.
    body = _f1_page(client, monkeypatch, "&type=detached&type=semi-detached&type=terraced&type=flat&type=other")
    assert body.count(" hidden>All years") == 0 and not re.search(r"data-years=\"\d+\" aria-pressed=\"\w+\" hidden", body)
    assert "All 11 sales" in body


def test_f1_an_asking_price_is_placed_among_the_chosen_sales_with_its_stamp_duty(client, monkeypatch):
    body = _flat(_f1_page(client, monkeypatch, "&house_number=12&price=%C2%A3650%2C000"))
    tool = _tool(body)
    # The box shows the price as it was typed on the listing.
    assert 'id="comp-price" name="price"' in tool and 'value="650,000" aria-describedby="comp-ask-hint"' in tool
    assert ("£650,000 is above 3 of the 5 nearby sold prices for semi-detached houses; "
            "the middle of those 5 is £600,000.") in tool
    assert 'id="comp-ask-marker" style="left: 60%;"' in tool
    # Stamp duty from the running-costs row's own function and bands.
    sd = {k: app_main._stamp_duty(650000, **kw) for k, kw in
          (("standard", {}), ("first", {"first_time": True}), ("additional", {"additional": True}))}
    assert sd == {"standard": 22500, "first": None, "additional": 55000}
    assert '<strong id="comp-sdlt-standard">£22,500</strong>' in tool
    # Above the first-time ceiling a first-time buyer pays the same.
    assert '<strong id="comp-sdlt-first">£22,500</strong>' in tool
    assert '<span class="comp-sdlt-note" id="comp-sdlt-first-note">no relief above £500,000</span>' in tool
    assert '<strong id="comp-sdlt-additional">£55,000</strong>' in tool
    assert "HMRC rates from April 2025" in tool
    # Under it, the relief applies.
    tool = _tool(_flat(_f1_page(client, monkeypatch, "&house_number=12&price=450000")))
    assert '<strong id="comp-sdlt-first">£7,500</strong>' in tool and '<strong id="comp-sdlt-standard">£12,500</strong>' in tool
    assert 'id="comp-sdlt-first-note" hidden>' in tool
    # Above or below every chosen sale, it says so plainly.
    tool = _flat(_tool(_f1_page(client, monkeypatch, "&house_number=12&price=900000")))
    assert "£900,000 is above all 5 nearby sold prices for semi-detached houses; the middle of those 5 is £600,000." in tool
    tool = _flat(_tool(_f1_page(client, monkeypatch, "&house_number=12&price=300000")))
    assert "£300,000 is below all 5 nearby sold prices for semi-detached houses" in tool
    tool = _flat(_tool(_f1_page(client, monkeypatch, "&house_number=12&price=400000")))
    assert "£400,000 is level with the lowest of the 5 nearby sold prices" in tool
    # A rank among recorded sales, never an estimate.
    for word in ("worth", "valuation", "estimate", "—", "!"):
        assert word not in re.sub(r"<[^>]+>", " ", tool), word
    # Unreadable prices are ignored rather than guessed at.
    tool = _tool(_flat(_f1_page(client, monkeypatch, "&price=lots")))
    assert 'id="comp-ask-result" hidden>' in tool and 'id="comp-sdlt" hidden>' in tool


def test_f1_wales_names_its_own_tax_rather_than_working_out_sdlt(client, monkeypatch):
    body = _flat(_f1_page(client, monkeypatch, "&price=650000", country="Wales"))
    tool = _tool(body)
    assert "Wales sets its own tax on buying: Land Transaction Tax, Welsh Revenue Authority." in tool
    assert "comp-sdlt-standard" not in tool
    assert "var COMP_SDLT = null;" in body


def test_f1_the_script_reads_the_sites_own_stamp_duty_bands(client, monkeypatch):
    body = _f1_page(client, monkeypatch, "&price=650000")
    script = body.split("var COMP_SDLT = ", 1)[1].split(";\n", 1)[0]
    rates = json.loads(script)
    assert rates == json.loads(json.dumps(app_main._sdlt_rates_for_page()))
    assert rates["bands"][-1] == [None, app_main.SDLT_BANDS[-1][1]]
    assert [b[0] for b in rates["bands"][:-1]] == [b[0] for b in app_main.SDLT_BANDS[:-1]]
    # No second copy of a threshold or a rate typed into the page.
    source = (ROOT / "app" / "templates" / "comparables.html").read_text(encoding="utf-8")
    for figure in ("125000", "250000", "925000", "1500000", "300000", "500000", "0.02", "0.05", "0.12"):
        assert figure not in source, figure
    # Python's round() sends a half to the even pound; the script matches it.
    assert "function compPyRound(x)" in body and "r % 2 !== 0" in body


def test_f1_the_price_travels_in_the_address_share_link_and_running_costs_link(client, monkeypatch):
    body = _f1_page(client, monkeypatch, "&house_number=12&price=650000&years=2")
    tool = _tool(body)
    share = "https://testserver/property/comparables?postcode=M14+5TG&amp;house_number=12&amp;price=650000&amp;years=2"
    assert f'data-copy-link="{share}"' in tool
    assert ('href="/running-costs?postcode=M14%205TG&amp;house_number=12&amp;price=650000"' in tool)
    # The script keeps the address, the share row and the links current.
    script = body.split("function compCarry(s, years) {", 1)[1].split("\n    }\n", 1)[0]
    assert "history.replaceState(" in script and "try {" in script
    assert "data-copy-link" in script and "comp-rc-link" in script and "comp-login-link" in script
    # A signed-out reader is offered the way to keep it, back to this view.
    assert ('href="/login?next=/property/comparables%3Fpostcode%3DM14%2B5TG%26house_number%3D12'
            '%26price%3D650000%26years%3D2%23asking">Log in</a>') in tool


def test_f1_both_maps_filter_pins_by_the_row_they_belong_to(client, monkeypatch):
    body = _f1_page(client, monkeypatch)
    assert "comparablePoints.forEach(function (c, i) {" in body
    assert "compMarkers.push({ i: i, date: c.date, show: function (visible) { if (visible) pin.addTo(compMap); else compMap.removeLayer(pin); } });" in body
    assert "compMarkers.forEach(function (m) { m.show(!!compVisible[m.i]); });" in body
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    body = _f1_page(client, monkeypatch)
    assert "compMarkers.push({ i: i, date: c.date, show: function (visible) { marker.setMap(visible ? map : null); } });" in body


def test_f1_running_costs_works_stamp_duty_out_on_the_price_in_the_address(client, monkeypatch):
    from app.services import _cache

    answer = {
        "postcode": "M14 5TG", "district": "Manchester", "outcode": "M14", "house_number": "",
        "latitude": 53.45, "longitude": -2.22, "council_tax": None, "energy": None, "home": None,
        "sales": None, "rent": None, "district_prices": None, "area_prices": None, "broadband": None,
        "flood": None, "typical_year": None, "energy_figure": None, "income_value": None,
        "income_la": None, "income_la_name": "", "typical_share_pct": None,
        "stamp_duty": {"price": 300000, "basis": "the middle of the postcode's recent sales",
                       "standard": 5000, "first_time": 0, "additional": 20000},
    }
    country = {"value": "England"}

    async def _lookup(_postcode):
        return fake_location(country=country["value"])

    async def _answer(where, house_number):
        return answer

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "_running_costs_for_postcode", _answer)
    _cache._store.clear()
    _cache._bytes = 0
    body = _flat(client.get("/running-costs?postcode=M14%205TG").text)
    assert "on £300,000, the middle of the postcode&#39;s recent sales" in body

    body = _flat(client.get("/running-costs?postcode=M14%205TG&price=650000").text)
    assert "on £650,000, the asking price" in body
    assert "<strong>£22,500</strong> for a buyer moving home; no first-time relief above &pound;500,000, £55,000 for an additional property" in body
    assert 'data-copy-link="https://testserver/running-costs?postcode=M14%205TG&amp;price=650000"' in body
    # The cached answer serves everyone else unchanged.
    assert answer["stamp_duty"]["price"] == 300000
    assert "on £300,000" in _flat(client.get("/running-costs?postcode=M14%205TG").text)
    # An unreadable price leaves the row on the home's own basis.
    assert "on £300,000" in _flat(client.get("/running-costs?postcode=M14%205TG&price=abc").text)
    # Wales names its own tax on the asking price.
    country["value"] = "Wales"
    _cache._store.clear()
    body = _flat(client.get("/running-costs?postcode=M14%205TG&price=650000").text)
    assert "on £650,000, the asking price" in body and "Land Transaction Tax, Welsh Revenue Authority" in body


def _f1_user_id(email):
    from app import db
    from app.models import User
    with db.get_session() as session:
        return session.scalar(select(User.id).where(User.email == email))


def _f1_saved_row(item_id):
    from app import db
    from app.models import SavedAskingPrice
    with db.get_session() as session:
        row = session.get(SavedAskingPrice, item_id)
        return None if row is None else (row.user_id, row.postcode, row.house_number, row.price)


def _f1_save_home(client, email, house_number):
    from app import watchlist
    _signed_in(client, email)
    r = client.post("/watchlist/save", data={"postcode": "M14 5TG", "house_number": house_number},
                    follow_redirects=False)
    assert r.status_code == 303
    uid = _f1_user_id(email)
    item = watchlist.find_in(watchlist.list_items(uid), "M14 5TG", house_number)
    return uid, item["id"]


def test_f1_a_saved_asking_price_round_trips_for_its_own_account(client, monkeypatch):
    async def _summary(postcode, house_number):
        return {"postcode": postcode, "house_number": house_number, "admin_district": "Manchester"}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    uid, item_id = _f1_save_home(client, "f1-asking@example.com", "112")

    page = _f1_page(client, monkeypatch, "&house_number=112")
    tool = _tool(page)
    assert '<form class="comp-save" id="comp-save" method="post" action="/watchlist/asking-price">' in tool
    assert f'name="item_id" value="{item_id}"' in tool
    assert "Type the asking price above to save it with this home." in tool

    back = "/property/comparables?postcode=M14+5TG&house_number=112&price=650000"
    r = client.post("/watchlist/asking-price", data={"item_id": item_id, "price": "£650,000", "next": back},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == back
    assert _f1_saved_row(item_id) == (uid, "M14 5TG", "112", 650000)

    # Back on the page with no price in the address, the kept one is there.
    tool = _flat(_tool(_f1_page(client, monkeypatch, "&house_number=112")))
    assert 'value="650,000" aria-describedby="comp-ask-hint"' in tool and 'id="comp-save-price" value="650000"' in tool
    assert "£650,000 is saved with this home, on My properties and in the side-by-side comparison." in tool
    assert 'id="comp-save-btn" hidden>' in tool
    assert '<button type="submit" class="comp-save-clear" name="clear" value="1">Remove the saved price</button>' in tool

    # My properties shows it, with the way back to where it sits.
    mine = _flat(client.get("/watchlist").text)
    assert "Asking price <strong>£650,000</strong>." in mine
    assert "/property/comparables?postcode=M14%205TG&amp;house_number=112&amp;price=650000" in mine

    # The side-by-side comparison leads with it.
    compare = _flat(client.get(f"/watchlist/compare?item_ids={item_id}").text)
    assert "<td><strong>Asking price you saved</strong></td>" in compare
    assert "£650,000" in compare.split("Asking price you saved", 1)[1].split("</tr>", 1)[0]

    # And every-check comparison heads the home's column with it.
    async def _rows(postcode, house_number):
        return {"postcode": postcode, "house_number": house_number, "admin_district": "Manchester",
                "rows": {}, "order": []}

    monkeypatch.setattr(app_main, "_all_unlocked", lambda user_id, items: True)
    monkeypatch.setattr(app_main, "_compare_rows", _rows)
    full = _flat(client.get(f"/watchlist/compare/full?item_ids={item_id}").text)
    assert "Asking price you saved: <strong>£650,000</strong>" in full

    # Clearing keeps the row and empties it.
    r = client.post("/watchlist/asking-price", data={"item_id": item_id, "clear": "1", "next": back},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _f1_saved_row(item_id) == (uid, "M14 5TG", "112", None)
    assert "Asking price <strong>" not in _flat(client.get("/watchlist").text)


def test_f1_an_asking_price_is_refused_for_anyone_else(client, monkeypatch):
    owner, item_id = _f1_save_home(client, "f1-owner@example.com", "114")
    client.cookies.clear()

    # Signed out: sent to log in, with the way back, and nothing written.
    back = "/property/comparables?postcode=M14+5TG&house_number=114"
    r = client.post("/watchlist/asking-price", data={"item_id": item_id, "price": "650000", "next": back},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=%2Fproperty%2Fcomparables")
    assert _f1_saved_row(item_id) is None

    # Another account: refused, nothing written.
    _signed_in(client, "f1-stranger@example.com")
    r = client.post("/watchlist/asking-price", data={"item_id": item_id, "price": "650000", "next": back},
                    follow_redirects=False)
    assert r.status_code == 403
    assert _f1_saved_row(item_id) is None
    # Nor does the stranger's comparables page offer to save onto it.
    assert 'id="comp-save"' not in _f1_page(client, monkeypatch, "&house_number=114")

    # The owner: an unreadable or over-long price is refused, a next that
    # leaves the page goes to My properties instead.
    client.cookies.clear()
    _signed_in(client, "f1-owner@example.com")
    r = client.post("/watchlist/asking-price", data={"item_id": item_id, "price": "a lot"}, follow_redirects=False)
    assert r.status_code == 400 and _f1_saved_row(item_id) is None
    r = client.post("/watchlist/asking-price", data={"item_id": item_id, "price": "9" * 50}, follow_redirects=False)
    assert r.status_code == 422 and _f1_saved_row(item_id) is None
    r = client.post("/watchlist/asking-price", data={"item_id": item_id, "price": "650000",
                                                     "next": "https://evil.example/property/comparables?x"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/watchlist"
    assert _f1_saved_row(item_id) == (owner, "M14 5TG", "114", 650000)


def test_f1_a_row_left_by_a_removed_home_shows_nowhere():
    """No foreign key to watchlist_items (removing a home must not be
    refused), so a read matches every row to a live home of the same
    account at the same address, and nothing is saved onto a home that
    does not exist."""
    from app import asking_prices, db
    from app.models import SavedAskingPrice
    with db.get_session() as session:
        session.add(SavedAskingPrice(item_id=987654, user_id=987, postcode="M14 5TG", house_number="9", price=650000))
        session.commit()
    kept = {"id": 987654, "postcode": "M14 5TG", "house_number": "9"}
    assert asking_prices.for_items(987, [kept])[987654]["price"] == 650000
    # The same id on another home, or another account, reads nothing.
    assert asking_prices.for_items(987, [{**kept, "house_number": "11"}]) == {}
    assert asking_prices.for_items(988, [kept]) == {}
    assert asking_prices.save(987, 987654, 700000) is False
    assert _f1_saved_row(987654) == (987, "M14 5TG", "9", 650000)


def test_f1_the_servers_rules_are_the_scripts():
    from app.services import comparables_view as cv

    assert cv.parse_price("£650,000") == 650000
    assert cv.parse_price(" 650000.00 ") == 650000
    assert cv.parse_price("650k") is None and cv.parse_price("999") is None
    assert cv.parse_price("100000001") is None and cv.parse_price("9" * 21) is None
    assert cv.parse_price("٦٥٠٠٠٠") is None, "ASCII digits only, as the script reads them"
    assert cv.half_up(12.5) == 13 and cv.half_up(66.6) == 67
    assert cv.years_cutoff(2, datetime.date(2028, 2, 29)) == "2026-03-01"
    assert cv.years_cutoff(0, F1_TODAY) is None
    assert cv.row_type("flat-maisonette") == "flat" and cv.row_type(None) == "other"
    assert cv.row_tenure("Leasehold") == "leasehold" and cv.row_tenure("") == ""
    present = {"detached", "semi-detached", "terraced", "flat", "other"}
    assert cv.qualifier(list(cv.HOME_TYPES), "any", 0, present) == " for houses and flats"
    assert cv.qualifier(["detached", "semi-detached"], "any", 0, present) == " for detached and semi-detached houses"
    assert cv.qualifier(["semi-detached", "flat"], "freehold", 5, present) == " for freehold semi-detached houses and flats in the last 5 years"
    assert cv.qualifier(["flat"], "leasehold", 0, {"flat"}) == " sold leasehold"
    assert cv.qualifier(list(cv.TYPES), "any", 2, present) == " in the last 2 years"
    # The script repeats each of these rules; its source says so in the same terms.
    source = (ROOT / "app" / "templates" / "comparables.html").read_text(encoding="utf-8")
    for rule in ("prices[Math.floor(n / 2)]", "function compHalfUp(n) { return Math.floor(n + 0.5); }",
                 "d.setFullYear(d.getFullYear() - years);", "/^([0-9]+)(?:\\.[0-9]{1,2})?$/",
                 "if (houses.length === COMP_HOUSES.length) nouns.push('houses');",
                 "' sold ' + s.tenure", "' in the last ' + years + ' years'"):
        assert rule in source, rule


def test_f1_the_tool_is_styled_from_tokens_and_the_lamp_needs_no_rule():
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    block = css.split("/* Is the asking price in line? on the comparables page", 1)[1]
    block = block.split("/* Show 25 more, on the comparables page", 1)[0]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", block), "a colour outside the tokens"
    # 16px in the price box, or iOS zooms the page; motion yields to the reader.
    assert re.search(r"\.comp-ask-row input \{[^}]*font-size: var\(--text-base\);", block)
    assert "@media (prefers-reduced-motion: reduce)" in block
    assert not re.search(r"theme-dark[^{]*comp-(ask|chip|sdlt|save|choice|note|figures|ref)", css)


# ==== F2. A viewing checklist you can tick ================================
# /property/checklist said "open it on your phone while you are standing
# there", and every box on it was a drawn square, aria-hidden. Each item is
# now a real checkbox inside a label that wraps its words, with a line for
# what the seller or agent said and a Follow up mark; the page keeps every
# change on the device, and signed in, posts it to the account, reads the
# account's first, and My properties and the comparisons show "Viewed: N of
# M checked, K to follow up". An item from a locked check is on the page
# only for a home the reader has opened, as before. The script's device
# storage and its posting are checked in a browser; these pin what the
# server renders, stores and refuses.

F2_SAID = "Boiler fitted 2019, serviced last March"


def _f2_page(client, house_number, **params):
    query = {"postcode": "M14 5TG", **({"house_number": house_number} if house_number else {}), **params}
    r = client.get("/property/checklist", params=query)
    assert r.status_code == 200
    return r.text.replace("\r\n", "\n")


def _f2_items(body):
    """Each item on the list: key -> its markup."""
    return dict(re.findall(r'<li class="checklist-item" data-check-item="([^"]+)">(.*?)</li>', body, re.S))


def _f2_form(keys, ticked=(), follow=(), said=None, house_number="", postcode="M14 5TG"):
    data = {"postcode": postcode, "house_number": house_number, "k": list(keys), "c": list(ticked), "f": list(follow)}
    for key, text in (said or {}).items():
        data[f"a_{key}"] = text
    return data


def _f2_row(email, house_number):
    from app import db
    from app.models import ViewingChecklistTicks
    uid = _f1_user_id(email)
    with db.get_session() as session:
        row = session.scalar(select(ViewingChecklistTicks).where(
            ViewingChecklistTicks.user_id == uid, ViewingChecklistTicks.house_number == house_number))
        return None if row is None else {
            "postcode": row.postcode, "items": json.loads(row.items), "checked": row.checked,
            "total": row.total, "follow_up": row.follow_up, "answered": row.answered}


def _f2_gather(**overrides):
    from tests.conftest import fake_gather
    return fake_gather(flood_zone={"zone": 3, "label": "Zone 3 (high probability)", "source": None},
                       noise={"road_db": 71, "rail_db": None, "airport_db": None}, **overrides)


def test_f2_every_item_is_a_real_checkbox_inside_the_label_that_holds_its_words(client, fake_report):
    from app.services import viewing_checklist
    fake_report(gather=_f2_gather())
    body = _f2_page(client, "221")
    items = _f2_items(body)

    # The flood prompt, the report's questions and the five asked at every
    # viewing, each once, each a checkbox valued with its own key.
    assert "look-flood" in items and "look-noise" in items and "every-water-pressure" in items
    ask = viewing_checklist.question_key(
        "Has the property ever flooded, and has any flood insurance claim been made?")
    assert ask in items
    assert len(re.findall(r'data-check-item="', body)) == len(items) >= 12
    for key, li in items.items():
        assert f'<input type="hidden" name="k" value="{key}">' in li
        tick = li.split('<label class="checklist-tick">', 1)[1].split("</label>", 1)[0]
        assert f'<input type="checkbox" class="checklist-check" name="c" value="{key}">' in tick
        assert '<span class="checklist-heading' in tick, "the words are inside the label"
        # A line for what they said, labelled, and a Follow up mark.
        assert f'<label class="checklist-reply-label" for="said-{key}">What they said</label>' in li
        assert re.search(rf'<input type="text" class="checklist-said" id="said-{key}" name="a_{key}" maxlength="200" value=""', li)
        assert f'<input type="checkbox" name="f" value="{key}"><span>Follow up</span>' in li
    assert "Tide marks" in items["look-flood"] and "Signs of past water" in items["look-flood"]
    assert 'class="checklist-box"' not in body, "no drawn boxes left"

    # Signed out: nothing to post to, so no form, and the count waits for
    # the script that keeps it up to date.
    flat = _flat(body)
    assert '<div class="checklist-form" id="checklist-form" data-home="M14 5TG|221" data-account="0"' in body
    assert 'action="/property/checklist/save"' not in body
    assert '<div class="checklist-count" id="checklist-count" hidden>' in body
    assert f"0 of {len(items)} checked, 0 to follow up" in body
    assert "&ldquo;To follow up&rdquo; counts those." in body
    assert ("Your ticks and answers are kept on this phone or computer. "
            '<a href="/login?next=/property/checklist%3Fpostcode%3DM14%25205TG%26house_number%3D221">Log in</a>') in flat
    # Printing is untouched: the button, and nothing on the list is screen-only.
    assert 'onclick="window.print()">Print this checklist</button>' in body
    section = body.split('id="checklist-form"', 1)[1].split('<h2>Notes</h2>', 1)[0]
    assert "no-print" not in section.replace('class="no-print checklist-save-row"', "")


def test_f2_the_account_keeps_the_ticks_and_the_page_opens_on_them(client, fake_report):
    fake_report(gather=_f2_gather())
    email = "f2-ticker@example.com"
    _signed_in(client, email)
    body = _f2_page(client, "223")
    keys = list(_f2_items(body))
    assert ('<form class="checklist-form" id="checklist-form" method="post" action="/property/checklist/save" '
            'data-home="M14 5TG|223" data-account="1" data-saved-at="0">') in body
    assert '<button type="submit" class="checklist-save" id="checklist-save">Save to my account</button>' in body

    # The script's post: JSON back, with the counts over the page's items.
    ticked, follow = ["look-flood", "every-the-boiler"], ["every-the-boiler"]
    r = client.post("/property/checklist/save",
                    data=_f2_form(keys, ticked, follow, {"every-the-boiler": F2_SAID}, "223"))
    assert r.status_code == 200
    answer = r.json()
    assert answer["ok"] and (answer["checked"], answer["total"], answer["follow_up"]) == (2, len(keys), 1)
    assert answer["updated_ms"] > 0
    row = _f2_row(email, "223")
    assert row["postcode"] == "M14 5TG" and (row["checked"], row["follow_up"], row["answered"]) == (2, 1, 1)
    assert row["items"]["every-the-boiler"] == {"c": True, "f": True, "a": F2_SAID}
    assert row["items"]["look-noise"] == {"c": False, "f": False, "a": ""}

    # Back on the page, the account's ticks are in the boxes before any script.
    body = _f2_page(client, "223")
    items = _f2_items(body)
    assert '<input type="checkbox" class="checklist-check" name="c" value="look-flood" checked>' in items["look-flood"]
    assert '<input type="checkbox" class="checklist-check" name="c" value="look-noise">' in items["look-noise"]
    boiler = items["every-the-boiler"]
    assert f'value="{F2_SAID}"' in boiler and '<input type="checkbox" name="f" value="every-the-boiler" checked>' in boiler
    assert '<div class="checklist-count" id="checklist-count">' in body
    assert f"2 of {len(keys)} checked, 1 to follow up" in body
    saved_at = int(re.search(r'data-saved-at="(\d+)"', body).group(1))
    assert saved_at == answer["updated_ms"]

    # Without a script, the list's own button posts the same form, the
    # browser asks for a page, and comes back to the list.
    r = client.post("/property/checklist/save", data=_f2_form(keys, ["look-noise"], (), None, "223"),
                    headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/property/checklist?postcode=M14+5TG&house_number=223&saved=1#checklist"
    assert _f2_row(email, "223")["checked"] == 1
    assert "Saved to your account. My properties shows how far you got." in _f2_page(client, "223", saved="1")

    # A house number with the street added is the same home, as on My properties.
    assert f"1 of {len(keys)} checked" in _f2_page(client, "223 Test Road")


def test_f2_the_save_refuses_the_signed_out_other_accounts_and_anything_malformed(client, fake_report):
    fake_report(gather=_f2_gather())
    owner = "f2-owner@example.com"
    _signed_in(client, owner)
    keys = list(_f2_items(_f2_page(client, "225")))
    r = client.post("/property/checklist/save", data=_f2_form(keys, ["look-flood"], (), {"look-flood": "Dry"}, "225"))
    assert r.status_code == 200
    client.cookies.clear()

    # Signed out: refused, to log in with the way back for a browser, and nothing written.
    r = client.post("/property/checklist/save", data=_f2_form(keys, keys, keys, None, "227"))
    assert r.status_code == 401 and r.json()["error"] == "sign_in"
    r = client.post("/property/checklist/save", data=_f2_form(keys, keys, keys, None, "227"),
                    headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=%2Fproperty%2Fchecklist%3Fpostcode%3DM14%2B5TG%26house_number%3D227"
    from app import db
    from app.models import ViewingChecklistTicks
    with db.get_session() as session:
        assert session.scalar(select(ViewingChecklistTicks).where(ViewingChecklistTicks.house_number == "227")) is None

    # Another account sees none of the owner's ticks on the same home, and
    # its own save lands on its own row, never the owner's.
    stranger = "f2-stranger@example.com"
    _signed_in(client, stranger)
    body = _f2_page(client, "225")
    listed = body.split('id="checklist-form"', 1)[1].split("<h2>Notes</h2>", 1)[0]
    assert ' checked>' not in listed and 'value="Dry"' not in listed and 'data-saved-at="0"' in body
    r = client.post("/property/checklist/save", data=_f2_form(keys, keys, (), None, "225"))
    assert r.status_code == 200 and r.json()["checked"] == len(keys)
    assert _f2_row(owner, "225")["checked"] == 1 and _f2_row(owner, "225")["items"]["look-flood"]["a"] == "Dry"
    assert _f2_row(stranger, "225")["checked"] == len(keys)

    # Another site, another encoding, an oversized body or a bad field: refused, nothing written.
    before = _f2_row(stranger, "225")
    good = _f2_form(keys, (), (), None, "225")
    assert client.post("/property/checklist/save", data=good, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/property/checklist/save", data=good, headers={"Origin": "http://testserver"}).status_code == 200
    before = _f2_row(stranger, "225")
    assert client.post("/property/checklist/save", json={"k": keys}).status_code == 415
    big = client.post("/property/checklist/save", content=b"k=" + b"a" * 70000,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert big.status_code == 413
    for bad in (
        _f2_form([], (), (), None, "225"),                                   # no items named
        _f2_form(["Look Flood"], (), (), None, "225"),                        # not a key
        _f2_form([f"item-{i}" for i in range(81)], (), (), None, "225"),     # more than a page holds
        _f2_form(keys, (), (), {keys[0]: "x" * 201}, "225"),                  # more than one line
        _f2_form(keys, (), (), None, "225", postcode="NOT A POSTCODE"),
        _f2_form(keys, (), (), None, "9" * 40),
    ):
        r = client.post("/property/checklist/save", data=bad)
        assert r.status_code == 400, bad
    assert _f2_row(stranger, "225") == before


def test_f2_my_properties_and_both_comparisons_show_how_far_the_viewing_got(client, fake_report, monkeypatch):
    from app import watchlist

    async def _summary(postcode, house_number):
        return {"postcode": postcode, "house_number": house_number, "admin_district": "Manchester"}

    async def _rows(postcode, house_number):
        return {"postcode": postcode, "house_number": house_number, "admin_district": "Manchester",
                "rows": {}, "order": []}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    monkeypatch.setattr(app_main, "_compare_rows", _rows)
    monkeypatch.setattr(app_main, "_all_unlocked", lambda user_id, items: True)
    fake_report(gather=_f2_gather())
    email = "f2-viewer@example.com"
    _signed_in(client, email)
    # Saved as typed on My properties; the checklist has the looked-up postcode.
    for house in ("229", "Flat 3"):
        assert client.post("/watchlist/save", data={"postcode": "m145tg", "house_number": house},
                           follow_redirects=False).status_code == 303
    uid = _f1_user_id(email)
    ids = [watchlist.find_in(watchlist.list_items(uid), "m145tg", h)["id"] for h in ("229", "Flat 3")]

    keys = list(_f2_items(_f2_page(client, "229")))
    r = client.post("/property/checklist/save", data=_f2_form(
        keys, keys[:7], [keys[0], keys[-1]], {keys[1]: "No claims, per the agent"}, "229"))
    assert r.status_code == 200 and r.json()["checked"] == 7

    mine = _flat(client.get("/watchlist").text)
    line = f"Viewed: 7 of {len(keys)} checked, 2 to follow up."
    assert line in mine
    assert ('<a href="/property/checklist?postcode=m145tg&amp;house_number=229">Open its viewing checklist</a>'
            in mine)
    # The home with nothing ticked is offered the list instead.
    assert ('<a href="/property/checklist?postcode=m145tg&amp;house_number=Flat%203">Viewing checklist</a> '
            "to tick off on your phone at the viewing.") in mine
    assert mine.count("Viewed: ") == 1

    compare = _flat(client.get(f"/watchlist/compare?item_ids={ids[0]}&item_ids={ids[1]}").text)
    row = compare.split("<td><strong>Viewing checklist</strong></td>", 1)[1].split("</tr>", 1)[0]
    assert f"7 of {len(keys)} checked, 2 to follow up" in row and "Not started." in row

    full = _flat(client.get(f"/watchlist/compare/full?item_ids={ids[0]}").text)
    assert f"Viewing checklist: 7 of {len(keys)} checked, 2 to follow up" in full

    # "flat 3" on the checklist is the saved "Flat 3", word by word.
    r = client.post("/property/checklist/save", data=_f2_form(keys, keys[:1], (), None, "flat 3"))
    assert r.status_code == 200
    assert f"Viewed: 1 of {len(keys)} checked, 0 to follow up." in _flat(client.get("/watchlist").text)


def test_f2_a_locked_checks_item_is_there_only_on_a_home_the_reader_has_opened(client, fake_report):
    from app import auth, db
    from app.services import viewing_checklist
    fake_report(gather=_f2_gather(coal_mining={"present": True, "area_name": None}))
    coal_look = "look-coal-mining"
    coal_ask = viewing_checklist.question_key("Order a CON29M coal mining search.")

    email = "f2-locked@example.com"
    _signed_in(client, email)
    body = _f2_page(client, "231")
    items = _f2_items(body)
    assert coal_look not in body and coal_ask not in body
    assert "Movement in the structure" not in body and "Order a CON29M coal mining search." not in body
    assert "1 more came from checks that open with a full report" in _flat(body)

    # A tick and an answer posted for the locked item are the reader's own
    # words, kept, but the item never renders for them, and the counts are
    # of the page's items alone.
    r = client.post("/property/checklist/save", data=_f2_form(
        list(items) + [coal_look], [coal_look], (), {coal_look: "Mine shaft in the garden"}, "231"))
    assert r.status_code == 200 and r.json()["total"] == len(items) + 1
    body = _f2_page(client, "231")
    assert "Mine shaft in the garden" not in body and coal_look not in body
    r = client.post("/property/checklist/save", data=_f2_form(list(items), (), (), None, "231"))
    assert (r.json()["checked"], r.json()["total"]) == (0, len(items))
    assert _f2_row(email, "231")["items"][coal_look]["a"] == "Mine shaft in the garden"

    # Opened, the item and its question are on the list, with what was kept.
    with db.get_session() as session:
        user = auth.find_user_by_email(session, email)
        user.is_premium, user.plan = True, "monthly"
        session.commit()
    body = _f2_page(client, "231")
    items = _f2_items(body)
    assert coal_look in items and coal_ask in items
    assert "Movement in the structure" in items[coal_look]
    assert 'value="Mine shaft in the garden"' in items[coal_look]


def test_f2_the_store_keeps_its_rules(monkeypatch):
    from app import checklist_ticks as ct

    assert ct.canonical_postcode(" m145tg ") == "M14 5TG" and ct.canonical_postcode("M14") is None
    assert ct.clean_house("  Flat 3,   66 Road ") == "Flat 3, 66 Road" and ct.clean_house("9" * 33) is None
    items = ct.parse_form([("k", "look-flood"), ("k", "ask-0123456789"), ("k", "look-flood"),
                           ("c", "look-flood"), ("c", "not-on-the-page"), ("f", "ask-0123456789"),
                           ("a_look-flood", "  Tide mark\n by the\tdoor\x00 ")])
    assert items == {"look-flood": {"c": True, "f": False, "a": "Tide mark by the door"},
                     "ask-0123456789": {"c": False, "f": True, "a": ""}}
    assert ct.parse_form([("c", "look-flood")]) is None

    # Keyed by account and home; an account's cap on homes stops a new one.
    first = ct.save(9101, "M14 5TG", "7", items)
    assert (first["checked"], first["total"], first["follow_up"], first["answered"]) == (1, 2, 1, 1)
    assert ct.load(9101, "M14 5TG", "7")["items"]["look-flood"]["a"] == "Tide mark by the door"
    assert ct.load(9102, "M14 5TG", "7") is None and ct.load(9101, "M14 5TG", "9") is None
    # A later page without an item keeps its words, uncounted.
    again = ct.save(9101, "M14 5TG", "7", {"every-parking": {"c": True, "f": False, "a": ""}})
    assert (again["checked"], again["total"]) == (1, 1)
    kept = ct.load(9101, "M14 5TG", "7")["items"]
    assert set(kept) == {"every-parking", "look-flood", "ask-0123456789"}
    monkeypatch.setattr(ct, "MAX_HOMES", 1)
    assert ct.save(9101, "M14 5TG", "8", items) is None
    assert ct.save(9101, "M14 5TG", "7", items) is not None, "its own home still saves"
    # Matched to saved homes by postcode as typed and house number word by word.
    homes = [{"id": 1, "postcode": "m14 5tg", "house_number": "7"},
             {"id": 2, "postcode": "M145TG", "house_number": "11"}]
    assert list(ct.for_items(9101, homes)) == [1] and ct.for_items(9102, homes) == {}


def test_f2_the_script_keeps_to_the_device_and_this_site():
    source = (ROOT / "app" / "templates" / "viewing_checklist.html").read_text(encoding="utf-8")
    script = source.split("{% block extra_scripts %}", 1)[1]
    # Every storage call sits in a function whose callers catch what it throws.
    assert script.count("localStorage.") == 3
    assert "var all = JSON.parse(localStorage.getItem(STORE) || '{}');" in script
    for fn in ("function readMine() {\n        try {", "function writeMine() {\n        try {",
               "function markSaved() {\n        try {"):
        assert fn in script.replace("\r\n", "\n"), fn
    assert script.count("readAll()") == 4, "defined once, and only ever called inside those three"
    # It posts to the page's own form and nowhere else.
    assert "fetch(root.action, {" in script and "navigator.sendBeacon(root.action," in script
    assert not re.search(r"https?://", script)
    # "To follow up" is the Follow up ticks, the rule the page states.
    assert "if (x.f.checked) follow++;" in script
    assert "' checked, ' + follow + ' to follow up'" in script


def test_f2_the_list_is_styled_from_tokens_and_the_lamp_needs_no_rule():
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8").replace("\r\n", "\n")
    block = css.split("/* The viewing checklist you can tick (18 Sep 2026", 1)[1]
    block = block.split("/* Print: ink on paper", 1)[0]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", block), "a colour outside the tokens"
    # Tap targets: the item, the answer line and the Follow up chip.
    for rule in (".checklist-tick {", ".checklist-said {", ".checklist-follow {"):
        assert re.search(re.escape(rule) + r"[^}]*min-height: 2\.75rem;", block), rule
    assert re.search(r"\.checklist-said \{[^}]*font-size: var\(--text-base\);", block)
    assert "@media (prefers-reduced-motion: reduce)" in block
    # Print keeps the ticks and the answers: nothing on the list is hidden there.
    printed = block.split("@media print {", 1)[1].split("\n}\n", 1)[0]
    assert "display: none" not in printed and ".checklist-said" in printed
    assert not re.search(r"theme-dark[^{]*checklist", css)


# ==== F3. Homes you looked at, on every school page =======================
# A school page checked one typed postcode at a time. Under its checker it
# now lists the homes this device opened (read by the page's script from
# the "Pick up where you left off" list and measured through
# /api/school-readings) and, signed in, the homes in My properties
# (rendered by the server), each with its distance and Likely, Borderline
# or Unlikely, each opening its report. The shortlist puts every saved home
# against every saved school. Every reading comes from _school_reading, the
# page's own ?check= answer, so none can disagree with it. The script's
# device list is checked in a browser; these pin the endpoint, what the
# server renders and the grid.

F3_URN = 990301            # Fernbrook Academy: 1.5 miles published
F3_NOFIG_URN = 990302      # Meadowbank Primary School: on the register, no published distance
F3_SLUG = "fernbrook-academy"
F3_POINTS = {
    "LN1 1AA": (53.2412, -0.54),   # 0.77 miles: Likely
    "LN1 2BB": (53.2497, -0.54),   # 1.36 miles: Borderline
    "LN2 3CC": (53.2705, -0.54),   # 2.8 miles: Unlikely
}


def _f3_seed():
    from app import db
    from app.models import School, SchoolAdmissionRadius
    with db.get_session() as session:
        if session.get(School, F3_URN) is None:
            session.add(School(urn=F3_URN, name="Fernbrook Academy", phase="Secondary",
                               type_name="Academy converter", postcode="LN1 1ZZ",
                               latitude=53.23, longitude=-0.54, ofsted_rating=2, ofsted_rating_label="Good"))
            session.add(SchoolAdmissionRadius(urn=F3_URN, last_distance_miles=1.5, academic_year="2025",
                                              source_authority="Lincolnshire"))
        if session.get(School, F3_NOFIG_URN) is None:
            session.add(School(urn=F3_NOFIG_URN, name="Meadowbank Primary School", phase="Primary",
                               type_name="Community school", postcode="LN1 3ZZ",
                               latitude=53.23, longitude=-0.56))
        session.commit()


def _f3_lookup(monkeypatch, calls=None):
    """The geocoder, stubbed: the three postcodes above, anything else unknown."""
    async def _lookup(raw):
        if calls is not None:
            calls.append(raw)
        compact = raw.replace(" ", "").upper()
        for postcode, (lat, lon) in F3_POINTS.items():
            if postcode.replace(" ", "") == compact:
                return {"postcode": postcode, "latitude": lat, "longitude": lon, "admin_district": "Lincoln"}
        return None

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)


def _f3_page(client, check=""):
    r = client.get(f"/school/{F3_URN}/{F3_SLUG}" + (f"?check={check}" if check else ""))
    assert r.status_code == 200
    return r.text.replace("\r\n", "\n")


def _f3_page_verdict(client, postcode):
    """The page's own ?check= answer: level, label and the distance it prints."""
    body = _f3_page(client, postcode.replace(" ", "+"))
    level = re.search(r'<div class="admission-verdict admission-verdict-(\w+)" id="verdict">', body).group(1)
    label = re.search(r'<span class="admission-verdict-label">([^<]+)</span>', body).group(1)
    miles = re.search(r"<strong>" + re.escape(postcode) + r"</strong> is <strong>([\d.]+) miles</strong>", body).group(1)
    return level, label, miles


def _f3_readings(client, postcodes, urn=F3_URN, **kwargs):
    return client.post("/api/school-readings", json={"urn": urn, "postcodes": postcodes}, **kwargs)


def test_f3_the_endpoint_gives_the_page_checkers_own_reading(client, monkeypatch):
    _f3_seed()
    _f3_lookup(monkeypatch)
    r = _f3_readings(client, ["ln11aa", "LN1 2BB", " ln2 3cc ", "LN9 9ZZ"])
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    data = r.json()
    assert (data["urn"], data["miles"], data["no_limit"]) == (F3_URN, 1.5, False)
    got = data["readings"]
    # One reading per postcode asked, in order, each as the page gives it.
    assert [g["query"] for g in got] == ["LN1 1AA", "LN1 2BB", "LN2 3CC", "LN9 9ZZ"]
    assert [g.get("level") for g in got] == ["likely", "borderline", "unlikely", None]
    for g in got[:3]:
        assert g["status"] == "ok" and g["area"] == "Lincoln"
        assert _f3_page_verdict(client, g["query"]) == (g["level"], g["label"], g["distance_label"])
    assert [g["distance_label"] for g in got[:3]] == ["0.77", "1.36", "2.8"]
    assert got[0]["why"] == "Comfortably inside the distance the school admitted from last time"
    assert got[3] == {"query": "LN9 9ZZ", "status": "not_found"}

    # The shared helper is the one both call.
    school = app_main._school_labels(app_main.schools_db.admission_point(F3_URN))
    where = {"postcode": "LN1 2BB", "latitude": 53.2497, "longitude": -0.54}
    assert app_main._school_reading(school, where)["label"] == got[1]["label"]


def test_f3_the_endpoint_refuses_anything_it_should_not_measure(client, monkeypatch):
    _f3_seed()
    calls = []
    _f3_lookup(monkeypatch, calls)
    # A malformed postcode is answered without a lookup; a repeat is looked up once.
    r = _f3_readings(client, ["not a postcode", "LN1 1AA", "ln1 1aa", "X" * 40])
    assert r.status_code == 200
    got = r.json()["readings"]
    assert [g["status"] for g in got] == ["invalid", "ok", "ok", "invalid"]
    assert got[0] == {"query": None, "status": "invalid"}
    assert calls == ["LN1 1AA"]

    assert _f3_readings(client, ["LN1 1AA"] * (app_main.SCHOOL_HOMES_MAX + 1)).json()["error"] == "too_many"
    assert _f3_readings(client, ["LN1 1AA"] * app_main.SCHOOL_HOMES_MAX).status_code == 200
    for payload in ({"urn": str(F3_URN), "postcodes": ["LN1 1AA"]}, {"urn": True, "postcodes": ["LN1 1AA"]},
                    {"urn": F3_URN, "postcodes": []}, {"urn": F3_URN, "postcodes": "LN1 1AA"},
                    {"urn": F3_URN, "postcodes": [7]}, ["LN1 1AA"]):
        assert client.post("/api/school-readings", json=payload).status_code == 400, payload
    # A school with no published distance has no page and no readings.
    assert _f3_readings(client, ["LN1 1AA"], urn=F3_NOFIG_URN).status_code == 404
    assert _f3_readings(client, ["LN1 1AA"], urn=1).status_code == 404
    # Only from this site, only JSON, and only so much of it.
    assert _f3_readings(client, ["LN1 1AA"], headers={"Origin": "https://evil.example"}).status_code == 403
    assert _f3_readings(client, ["LN1 1AA"], headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post("/api/school-readings", data={"urn": F3_URN, "postcodes": "LN1 1AA"}).status_code == 415
    big = client.post("/api/school-readings", content=b'{"urn": 990301, "postcodes": ["' + b"A" * 3000 + b'"]}',
                      headers={"Content-Type": "application/json"})
    assert big.status_code == 413
    assert client.post("/api/school-readings", content=b"{not json",
                       headers={"Content-Type": "application/json"}).status_code == 400


def test_f3_signed_out_the_list_waits_for_the_devices_homes(client, monkeypatch):
    _f3_seed()
    _f3_lookup(monkeypatch)
    body = _f3_page(client)
    flat = _flat(body)
    # Empty and hidden in the HTML (the anonymous cache keeps this), with
    # the offer to sign up waiting for the script to show it.
    assert (f'<div class="school-homes" id="school-homes" data-urn="{F3_URN}" '
            f'data-max="{app_main.SCHOOL_HOMES_MAX}" hidden>') in body
    assert '<li class="school-home"' not in body
    assert ('<p class="lx-recent-note" id="school-homes-signup" hidden><a href="/signup?next=/school/990301/'
            'fernbrook-academy">Sign up</a> and the homes you open are kept in My properties') in flat
    assert "Opened on this device and kept on it only: just their postcodes are sent, to this site" in flat
    assert ("Each measured from its postcode's centre against the 1.5 miles Lincolnshire published for 2025, "
            "as the checker measures.") in flat
    # Under the checker, before anything else on the page.
    assert body.index('id="check-postcode-top"') < body.index('id="school-homes"') < body.index("<h2>Will an address get in?</h2>")
    assert body.count('id="school-homes"') == 1


def test_f3_signed_in_the_school_page_lists_saved_homes_with_their_readings(client, monkeypatch):
    _f3_seed()
    _f3_lookup(monkeypatch)
    email = "f3-parent@example.com"
    _signed_in(client, email)
    # Saved as typed. "301" and "301 Test Road" at one postcode are one
    # home, the newer kept; a postcode that is not found says so.
    for postcode, house in (("ln11aa", "301"), ("LN2 3CC", "303"), ("LN9 9ZZ", "305"), ("ln1 1aa", "301 Test Road")):
        assert client.post("/watchlist/save", data={"postcode": postcode, "house_number": house},
                           follow_redirects=False).status_code == 303
    body = _f3_page(client)
    flat = _flat(body)
    assert f'<div class="school-homes" id="school-homes" data-urn="{F3_URN}" data-max="8">' in body
    assert body.count('<li class="school-home"') == 3
    assert body.count('data-pc="LN1 1AA"') == 1
    assert ('<li class="school-home" data-pc="LN1 1AA" data-hn="301 Test Road"> '
            '<a class="school-home-link" href="/property?postcode=LN1+1AA&amp;house_number=301+Test+Road"> '
            '<span class="school-home-address">301 Test Road, LN1 1AA</span> '
            '<span class="school-home-area">Lincoln</span> '
            '<span class="school-home-miles">0.77 mi</span> '
            '<span class="table-verdict table-verdict-likely" '
            'title="Comfortably inside the distance the school admitted from last time">Likely</span>') in flat
    unlikely = flat.split('data-hn="303"', 1)[1].split("</li>", 1)[0]
    assert '<span class="school-home-miles">2.8 mi</span>' in unlikely and ">Unlikely</span>" in unlikely
    assert "303, LN2 3CC" in unlikely
    missing = flat.split('data-hn="305"', 1)[1].split("</li>", 1)[0]
    assert '<span class="school-home-none">Postcode not found</span>' in missing and "table-verdict" not in missing
    # Each reading is the page checker's own.
    assert _f3_page_verdict(client, "LN2 3CC")[1:] == ("Unlikely", "2.8")
    assert ('From <a href="/watchlist">My properties</a>. <a href="/schools/shortlist">Your school shortlist</a> '
            "puts every saved home against every school you save.") in flat
    assert 'id="school-homes-signup"' not in body

    # With an answer on screen the checker is the verdict's last row and
    # the map carries the answer's pin, so the list follows the map.
    answered = _f3_page(client, "LN1+2BB")
    assert answered.index('id="verdict"') < answered.index('id="school-page-map"') < answered.index('id="school-homes"')
    assert answered.index('id="school-homes"') < answered.index("<strong>This is not a catchment area.</strong>")
    assert answered.count('id="school-homes"') == 1 and answered.count('<li class="school-home"') == 3

    # Another account sees none of these homes.
    client.cookies.clear()
    _signed_in(client, "f3-stranger@example.com")
    other = _f3_page(client)
    assert '<li class="school-home"' not in other and 'id="school-homes" data-urn="990301" data-max="8" hidden>' in other


def test_f3_the_shortlist_puts_saved_homes_against_saved_schools(client, monkeypatch):
    _f3_seed()
    _f3_lookup(monkeypatch)
    email = "f3-grid@example.com"
    _signed_in(client, email)
    # No schools yet: no grid.
    assert 'id="homes-grid"' not in client.get("/schools/shortlist").text
    # Columns follow the shortlist table above them, newest saved first.
    for urn in (F3_NOFIG_URN, F3_URN):
        client.post("/schools/shortlist/save", data={"urn": str(urn), "next": "/schools/shortlist"})
    # Schools but no homes: the grid says how it fills.
    empty = _flat(client.get("/schools/shortlist").text)
    assert "No saved homes yet. Every report you open is kept in" in empty

    for postcode, house in (("LN1 2BB", "311"), ("ln2 3cc", "313")):
        client.post("/watchlist/save", data={"postcode": postcode, "house_number": house}, follow_redirects=False)
    body = client.get("/schools/shortlist").text.replace("\r\n", "\n")
    grid = _flat(body.split('id="homes-grid"', 1)[1].split("</section>", 1)[0])
    assert "<h2>Your saved homes against these schools</h2>" in grid
    assert '<table class="tx-table shortlist-grid" data-stack>' in grid
    head = grid.split("<thead>", 1)[1].split("</thead>", 1)[0]
    assert head.count('<th class="shortlist-grid-school">') == 2
    assert f'<a href="/school/{F3_URN}/{F3_SLUG}">Fernbrook Academy</a>' in head
    assert '<span class="school-table-type">Admitted from 1.5 mi, 2025</span>' in head
    assert 'Meadowbank Primary School <span class="school-table-type">No published distance</span>' in head

    rows = re.findall(r"<tr> (<td data-st=\"title\".*?)</tr>", grid.split("<tbody>", 1)[1])
    assert len(rows) == 2
    by_home = {re.search(r'<a href="[^"]*">([^<]+)</a>', r).group(1): r for r in rows}
    assert set(by_home) == {"311, LN1 2BB", "313, LN2 3CC"}
    for label, postcode in (("311, LN1 2BB", "LN1 2BB"), ("313, LN2 3CC", "LN2 3CC")):
        cells = re.findall(r'<td data-label="([^"]+)">(.*?)</td>', by_home[label])
        assert [name for name, _ in cells] == ["Fernbrook Academy", "Meadowbank Primary School"]
        # The published school: the reading the school's own page gives.
        level, word, miles = _f3_page_verdict(client, postcode)
        assert (f'<span class="table-verdict table-verdict-{level}" ') in cells[0][1]
        assert f">{word}</span> <span class=\"shortlist-grid-mi\">{miles} mi</span>" in cells[0][1]
        # The school without a figure: its distance, and the gap in words.
        assert re.search(r'<span class="shortlist-grid-mi">[\d.]+ mi</span> <span class="shortlist-grid-none">'
                         r"No published admission distance to read it against</span>", cells[1][1])
        assert "table-verdict" not in cells[1][1]
    assert ">Borderline</span>" in by_home["311, LN1 2BB"] and ">Unlikely</span>" in by_home["313, LN2 3CC"]
    assert '<a href="/property?postcode=LN1+2BB&amp;house_number=311">' in by_home["311, LN1 2BB"]
    assert "Department for Education register" in grid and "postcodes.io" in grid

    # Signed out, the shortlist is still a log-in.
    client.cookies.clear()
    assert client.get("/schools/shortlist", follow_redirects=False).status_code == 303


def test_f3_a_home_saved_twice_is_one_home_and_the_list_is_capped():
    items = [{"postcode": "ln1 1aa", "house_number": "12 Test Road"}, {"postcode": "LN11AA", "house_number": "12"},
             {"postcode": "LN1 1AA", "house_number": "12A"}, {"postcode": "LN1 1AA", "house_number": ""}]
    homes = app_main._distinct_saved_homes(items)
    assert [h["house_number"] for h in homes] == ["12 Test Road", "12A", ""]
    many = [{"postcode": "LN1 1AA", "house_number": str(n)} for n in range(20)]
    assert len(app_main._distinct_saved_homes(many)) == app_main.SCHOOL_HOMES_MAX


def test_f3_the_script_keeps_the_list_on_the_device_and_asks_only_this_site():
    source = (ROOT / "app" / "templates" / "school_admission.html").read_text(encoding="utf-8").replace("\r\n", "\n")
    script = source.split("/* Homes you looked at (18 Sep 2026, first-visitor audit F3)", 1)[1].split("</script>", 1)[0]
    # Two storage calls, each inside a try.
    assert script.count("localStorage.") == 2
    assert "try {\n        entries = JSON.parse(localStorage.getItem('uki-recent') || '[]');\n    } catch (e) { return; }" in script
    assert "try { localStorage.removeItem('uki-recent'); } catch (e) {}" in script
    # Postcodes to this site's endpoint and nowhere else; house numbers stay.
    assert "fetch('/api/school-readings', {" in script
    assert "body: JSON.stringify({ urn: URN, postcodes: postcodes })" in script
    assert not re.search(r"https?://", script)
    # Built as text, never as markup.
    assert "innerHTML" not in script and "textContent = text" in script


def test_f3_the_list_and_grid_are_styled_from_tokens_and_the_lamp_needs_no_rule():
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8").replace("\r\n", "\n")
    block = css.split("/* Homes you looked at, under a school page's checker", 1)[1]
    block = block.split(".shortlist-grid-cell {", 1)[0]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", block), "a colour outside the tokens"
    assert re.search(r"\.school-home-link \{[^}]*min-height: 2\.75rem;", block)
    assert re.search(r"\.school-homes \.lx-recent-clear \{[^}]*min-height: 2rem;", block)
    assert re.search(r"\.shortlist-grid-home a,\n\.shortlist-grid-school a \{[^}]*min-height: 2rem;", block)
    assert "@media (prefers-reduced-motion: reduce) {\n    .school-home-new { animation: none; }" in block
    assert not re.search(r"theme-dark[^{]*(school-home|shortlist-grid)", css)


# ==== F4. A band picker that updates everything ===========================
# /running-costs: picking Band F changed the lead's total and share and left
# the table on Band D (its council tax row and "A typical year, owning"),
# the choice was not in the address, so the share row sent Band D, and the
# energy figure after the certificate's recommended improvements, already
# on the page, could not be used. Every figure that follows the band or the
# energy choice now comes from one function on the server, for ?band= and
# ?energy=, which the script repeats; both travel in the address and the
# share links; and a signed-in buyer can save the band with a home, which
# the page then opens on and the report's running-costs line uses. The live
# updates are checked in a browser; these pin what the server renders,
# stores and refuses.

F4_NINTHS = dict(zip("ABCDEFGH", (6, 7, 8, 9, 11, 13, 15, 18)))
F4_CT = {"authority": "Manchester", "slug": "manchester", "year": "2026-27", "band_d": 1800.0, "nation": "England",
         "bands": {b: round(1800 * n / 9, 2) for b, n in F4_NINTHS.items()},
         "basis": "Band D is the authority's published average area charge (MHCLG)."}
F4_ENERGY = {"certificates": 6, "priced": 6, "low": 700, "high": 1400, "median": 974, "median_potential": 671,
             "bands": "C x4, D x2"}


def _f4_answer(home=None, energy=F4_ENERGY, income=40000, council=F4_CT):
    """The running-costs answer, as _running_costs_for_postcode shapes it:
    Band D £1,800, the postcode's middle energy estimate £974 now and £671
    after the certificates' recommended improvements, income £40,000."""
    figure = (home or {}).get("energy_now") or (energy or {}).get("median")
    return {
        "postcode": "M14 5TG", "district": "Manchester", "outcode": "M14", "house_number": "",
        "latitude": 53.45, "longitude": -2.22, "council_tax": council, "energy": energy, "home": home,
        "sales": None, "stamp_duty": None, "rent": None, "district_prices": None, "area_prices": None,
        "broadband": None, "flood": None, "energy_figure": figure,
        "typical_year": round(council["band_d"] + figure) if council and figure else None,
        "income_value": income, "income_la": None, "income_la_name": "", "typical_share_pct": None,
    }


def _f4_install(monkeypatch, answer):
    from app.services import _cache

    async def _lookup(_postcode):
        return fake_location()

    async def _answer(where, house_number):
        return {**answer, "house_number": house_number}

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    monkeypatch.setattr(app_main, "_running_costs_for_postcode", _answer)
    _cache._store.clear()
    _cache._bytes = 0


def _f4_page(client, query=""):
    r = client.get(f"/running-costs?postcode=M14%205TG{query}")
    assert r.status_code == 200
    return _flat(r.text)


def _f4_band(item_id):
    from app import db
    from app.models import SavedAskingPrice
    with db.get_session() as session:
        row = session.get(SavedAskingPrice, item_id)
        return None if row is None else (row.user_id, row.band, row.price)


def test_f4_a_band_in_the_address_moves_every_figure_that_depends_on_it(client, monkeypatch):
    from urllib.parse import quote
    _f4_install(monkeypatch, _f4_answer())
    # Band D, as every reader without a choice gets it: 1,800 + 974.
    body = _f4_page(client)
    assert '<strong id="rc-total">£2,774 a year</strong>, <span id="rc-share">6.9</span>%' in body
    assert '<strong data-rc-ct>£1,800</strong> a year at Band <span data-rc-band>D</span>' in body
    assert ('<strong data-rc-year>£2,774</strong> a year, <span data-rc-month>£231</span> a month over twelve '
            'months, <span data-rc-share>6.9</span>% of the typical household income here') in body

    # Band F in the address: 2,600 + 974 in the lead, the council tax row and the typical year.
    body = _f4_page(client, "&band=F")
    assert ('<strong id="rc-total">£3,574 a year</strong>, <span id="rc-share">8.9</span>% of the typical '
            'household income here. That is Band <span id="rc-band-label">F</span> council tax at Manchester') in body
    assert '<strong data-rc-ct>£2,600</strong> a year at Band <span data-rc-band>F</span>' in body
    assert 'Band <span data-rc-band>F</span> council tax plus the middle energy estimate' in body
    assert ('<strong data-rc-year>£3,574</strong> a year, <span data-rc-month>£298</span> a month over twelve '
            'months, <span data-rc-share>8.9</span>% of the typical household income here') in body
    assert '<option value="F" selected>' in body and '<option value="D" selected>' not in body
    assert '<th class="num rc-band-chosen" data-band="F">Band F</th>' in body
    assert '<th class="num" data-band="D">Band D</th>' in body
    # Nothing on the page is left on Band D, and every band's bill is still the council's own.
    assert "£2,774" not in body and "£231" not in body and "at Band D" not in body
    assert all(f'<td class="num">{app_main._format_gbp(v)}</td>' in body for v in F4_CT["bands"].values())

    # A lower-case band is the band; anything else opens on Band D, and is not repeated.
    assert '<option value="F" selected>' in _f4_page(client, "&band=f")
    for odd in ("Z", "I", "", "FF", "<b>"):
        page = _f4_page(client, "&band=" + quote(odd))
        assert '<option value="D" selected>' in page and "£2,774 a year" in page, odd
        assert "&lt;b&gt;" not in page, odd
    # Wales has a Band I, and it is a band there.
    _f4_install(monkeypatch, _f4_answer(council=dict(F4_CT, bands={**F4_CT["bands"], "I": 4200.0})))
    body = _f4_page(client, "&band=I")
    assert '<option value="I" selected>' in body and '<strong id="rc-total">£5,174 a year</strong>' in body


def test_f4_energy_after_the_improvements_is_used_in_every_total(client, monkeypatch):
    _f4_install(monkeypatch, _f4_answer())
    body = _f4_page(client)
    assert "<legend>Energy</legend>" in body
    assert ('<label class="rc-chip"><input type="radio" name="energy" value="now" checked>'
            "<span>today's estimate, £974</span></label>") in body
    assert ('<label class="rc-chip"><input type="radio" name="energy" value="improved">'
            "<span>after the certificates&#39; recommended improvements, £671</span></label>") in body
    assert '<span data-rc-energy="improved" hidden> after the certificates&#39; recommended improvements</span>' in body

    # 1,800 + 671.
    body = _f4_page(client, "&energy=improved")
    assert '<strong id="rc-total">£2,471 a year</strong>, <span id="rc-share">6.2</span>%' in body
    assert ('the middle EPC energy estimate for the postcode<span data-rc-energy="improved"> after the '
            "certificates&#39; recommended improvements</span>, and nothing else") in body
    assert '<strong data-rc-year>£2,471</strong> a year, <span data-rc-month>£206</span> a month' in body
    assert 'value="improved" checked>' in body
    # The council tax row does not move with the energy figure.
    assert '<strong data-rc-ct>£1,800</strong> a year at Band <span data-rc-band>D</span>' in body

    # Both together: 2,600 + 671.
    body = _f4_page(client, "&band=F&energy=improved")
    assert '<strong id="rc-total">£3,271 a year</strong>, <span id="rc-share">8.2</span>%' in body
    assert ('<strong data-rc-year>£3,271</strong> a year, <span data-rc-month>£273</span> a month over twelve '
            'months, <span data-rc-share>8.2</span>%') in body
    # The energy row still gives both of the EPC's figures, as published.
    assert "£974</strong> a year is the middle of 6 homes" in body
    assert "£671 after the certificates' recommended improvements" in body

    # A home's own certificate: its own two figures, 2,600 + 1,584.
    home = {"address": "12, Test Road", "energy_now": 2192, "energy_potential": 1584, "band": "E"}
    _f4_install(monkeypatch, _f4_answer(home=home))
    body = _f4_page(client, "&house_number=12&band=F&energy=improved")
    assert '<strong id="rc-total">£4,184 a year</strong>, <span id="rc-share">10.5</span>%' in body
    assert ("this home's own EPC energy estimate<span data-rc-energy=\"improved\"> after the certificate&#39;s "
            "recommended improvements</span>") in body
    assert "<span>after the certificate&#39;s recommended improvements, £1,584</span>" in body

    # No improvement that lowers it: no toggle, and today's figure whatever the address says.
    _f4_install(monkeypatch, _f4_answer(energy=dict(F4_ENERGY, median_potential=974)))
    body = _f4_page(client, "&energy=improved")
    assert '<input type="radio" name="energy"' not in body and "<span data-rc-energy" not in body
    assert '<strong id="rc-total">£2,774 a year</strong>' in body


def test_f4_the_choice_travels_in_the_share_links_and_the_plain_form(client, monkeypatch):
    import html
    from urllib.parse import unquote
    _f4_install(monkeypatch, _f4_answer())
    base = "https://testserver/running-costs?postcode=M14%205TG"
    assert f'data-copy-link="{base}"' in _f4_page(client)
    body = _f4_page(client, "&band=F")
    assert f'data-copy-link="{base}&amp;band=F"' in body
    wa = re.search(r'href="https://wa.me/\?text=([^"]*)"', body).group(1)
    assert unquote(html.unescape(wa)).endswith(f"{base}&band=F")
    mail = re.search(r'href="mailto:\?subject=[^"]*&body=([^"]*)"', body).group(1)
    assert unquote(html.unescape(mail)).endswith(f"{base}&band=F")

    body = _f4_page(client, "&house_number=12&price=650000&band=G&energy=improved")
    assert f'data-copy-link="{base}&amp;house_number=12&amp;price=650000&amp;band=G&amp;energy=improved"' in body
    # The script's copy of where the page is, and what it shares.
    place = json.loads(html.unescape(re.search(r"data-place='([^']*)'", body).group(1)))
    assert place == {"postcode": "M14 5TG", "house": "12", "price": 650000, "base": "https://testserver",
                     "kept": "", "shareText": "What it costs to live in M14 5TG: council tax, energy, prices, "
                                              "rent, stamp duty, from the official sources"}
    # Without a script the picker is a plain form for the same page.
    assert '<form class="rc-band-pick" id="rc-pick" method="get" action="/running-costs" autocomplete="off"' in body
    assert '<input type="hidden" name="postcode" value="M14 5TG">' in body
    assert '<input type="hidden" name="house_number" value="12">' in body
    assert '<input type="hidden" name="price" value="650000">' in body
    assert '<select id="rc-band" name="band">' in body
    assert '<button type="submit" class="rc-pick-go" id="rc-pick-go">Show these figures</button>' in body
    # And what that form sends is honoured as it stands: Band D and today's energy add nothing.
    assert f'data-copy-link="{base}&amp;band=G"' in _f4_page(client, "&band=G&energy=now")
    assert f'data-copy-link="{base}"' in _f4_page(client, "&band=D&energy=now")


def test_f4_the_script_does_the_servers_sums_and_stays_on_this_site():
    source = (ROOT / "app" / "templates" / "running_costs.html").read_text(encoding="utf-8").replace("\r\n", "\n")
    script = source.split("/* The band picker and the energy toggle (18 Sep 2026", 1)[1].split("</script>", 1)[0]
    for rule in ("var year = Math.round(amount + used);", "gbp(Math.round(year / 12))",
                 "(Math.round(1000 * year / income) / 10).toFixed(1)", "gbp(Math.round(amount))",
                 "if (band !== 'D' || (place.kept && !share)) q.push('band=' + encodeURIComponent(band));",
                 "if (energy === 'improved') q.push('energy=improved');",
                 "history.replaceState(history.state, '', own + window.location.hash);",
                 "if (go) go.hidden = true;"):
        assert rule in script, rule
    # Nothing is kept on the device or sent anywhere; the share links are the only other address.
    assert "localStorage" not in script and "fetch(" not in script and "innerHTML" not in script
    assert set(re.findall(r"https?://[^'\"/]+", script)) == {"https://wa.me"}

    # The server's side of the same sums.
    assert app_main.RC_DEFAULT_BAND == "D"
    choice = app_main._running_costs_choice(_f4_answer(), "f", "improved")
    assert (choice["band"], choice["energy"], choice["year"], choice["month"], choice["share"]) == ("F", "improved", 3271, 273, 8.2)
    # Halves up, as Math.round: £1,840.50 and £974 is £2,815.
    half = _f4_answer(council=dict(F4_CT, bands=dict(F4_CT["bands"], B=1840.5)))
    assert app_main._running_costs_choice(half, "B")["year"] == 2815
    assert (app_main._running_costs_path("M14 5TG", "Flat 2", 650000, choice)
            == "/running-costs?postcode=M14%205TG&house_number=Flat%202&price=650000&band=F&energy=improved")
    plain = app_main._running_costs_choice(_f4_answer(), "D")
    assert app_main._running_costs_path("M14 5TG", "", None, plain) == "/running-costs?postcode=M14%205TG"
    assert app_main._running_costs_path("M14 5TG", "", None, plain, kept="F") == "/running-costs?postcode=M14%205TG&band=D"
    assert app_main._running_costs_path("M14 5TG", "", None, plain, share=True, kept="F") == "/running-costs?postcode=M14%205TG"


def test_f4_a_saved_band_opens_the_page_and_sets_the_reports_line(client, monkeypatch, fake_report):
    from app.services import council_tax
    from tests.conftest import fake_gather
    uid, item_id = _f1_save_home(client, "f4-band@example.com", "140")
    _f4_install(monkeypatch, _f4_answer())
    body = _f4_page(client, "&house_number=140")
    assert '<form class="rc-save" id="rc-save" method="post" action="/watchlist/council-tax-band">' in body
    assert f'<input type="hidden" name="item_id" value="{item_id}">' in body
    assert '<button type="submit" class="rc-save-btn" id="rc-save-btn">Save Band D with this home</button>' in body
    assert "Its report then gives this band's bill." in body and "Remove the saved band" not in body

    back = "/running-costs?postcode=M14%205TG&house_number=140&band=F"
    r = client.post("/watchlist/council-tax-band", data={"item_id": item_id, "band": "F", "next": back},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == back
    assert _f4_band(item_id) == (uid, "F", None)

    # Back with no band in the address: the saved one, in every figure.
    body = _f4_page(client, "&house_number=140")
    assert '<option value="F" selected>' in body and '<strong id="rc-total">£3,574 a year</strong>' in body
    assert '<strong data-rc-ct>£2,600</strong> a year at Band <span data-rc-band>F</span>' in body
    assert "Band F is saved with this home, and its report gives that band's bill." in body
    assert 'id="rc-save-btn" hidden>Save Band F with this home</button>' in body
    assert '<button type="submit" class="rc-save-clear" name="clear" value="1">Remove the saved band</button>' in body
    assert f'data-copy-link="https://testserver/running-costs?postcode=M14%205TG&amp;house_number=140&amp;band=F"' in body
    # Band D asked for in the address wins, and stays in the address so a
    # reload does not jump back; the partner's link needs no band.
    body = _f4_page(client, "&house_number=140&band=D")
    assert '<option value="D" selected>' in body and "£2,774 a year" in body
    assert "Band F is saved with this home now." in body
    assert ('<input type="hidden" name="next" id="rc-save-next" '
            'value="/running-costs?postcode=M14%205TG&amp;house_number=140&amp;band=D">') in body
    assert 'data-copy-link="https://testserver/running-costs?postcode=M14%205TG&amp;house_number=140"' in body

    # The asking price and the band share the home's row and never clear each other.
    r = client.post("/watchlist/asking-price", data={"item_id": item_id, "price": "650000", "next": "/watchlist"},
                    follow_redirects=False)
    assert r.status_code == 303 and _f4_band(item_id) == (uid, "F", 650000)

    # The report's running-costs line gives the saved band's bill, and its link opens on it.
    ct = council_tax.for_district("E08000003")
    fake_report(gather=fake_gather(council_tax=ct))
    line = _flat(client.get("/property?postcode=M14+5TG&house_number=140").text)
    line = line.split('id="running-costs"', 1)[1].split("</div>", 1)[0]
    assert (f"<strong>{app_main._format_gbp(ct['bands']['F'])}</strong> a year council tax at Band F, "
            f"the band you saved for this home, {ct['authority']}") in line
    assert 'href="/running-costs?postcode=M14%205TG&amp;house_number=140&amp;band=F"' in line
    assert "at Band D" not in line

    # Removing it keeps the row and the price; the page and the report are on Band D again.
    r = client.post("/watchlist/council-tax-band", data={"item_id": item_id, "band": "F", "clear": "1", "next": back},
                    follow_redirects=False)
    assert r.status_code == 303 and _f4_band(item_id) == (uid, None, 650000)
    line = _flat(client.get("/property?postcode=M14+5TG&house_number=140").text)
    line = line.split('id="running-costs"', 1)[1].split("</div>", 1)[0]
    assert f"<strong>{app_main._format_gbp(ct['band_d'])}</strong> a year council tax at Band D" in line
    assert "&amp;band=" not in line
    body = _f4_page(client, "&house_number=140")
    assert '<option value="D" selected>' in body and "Its report then gives this band's bill." in body


def test_f4_a_band_is_refused_for_anyone_else_and_anything_malformed(client, monkeypatch):
    from urllib.parse import quote
    owner, item_id = _f1_save_home(client, "f4-owner@example.com", "142")
    back = "/running-costs?postcode=M14%205TG&house_number=142&band=F"
    url = "/watchlist/council-tax-band"
    for data in ({"item_id": item_id, "band": "Z", "next": back}, {"item_id": item_id, "band": "", "next": back},
                 {"item_id": item_id, "band": "FF", "next": back}, {"item_id": "12x", "band": "F", "next": back},
                 {"item_id": "9" * 13, "band": "F", "next": back}, {"item_id": "", "band": "F", "next": back}):
        assert client.post(url, data=data, follow_redirects=False).status_code == 400, data
    assert client.post(url, data={"item_id": item_id, "band": "F", "next": "/running-costs?" + "x" * 3000},
                       follow_redirects=False).status_code == 413
    assert client.post(url, data={"item_id": item_id, "band": "F", "next": back},
                       headers={"Origin": "https://elsewhere.example"}, follow_redirects=False).status_code == 403
    assert client.post(url, json={"item_id": item_id, "band": "F"}, follow_redirects=False).status_code == 415
    assert _f4_band(item_id) is None
    # Anywhere but a running-costs page goes back to My properties.
    for elsewhere in ("//elsewhere.example/running-costs?x", "https://elsewhere.example/", "/running-costs?a b",
                      "/property?postcode=M14%205TG"):
        r = client.post(url, data={"item_id": item_id, "band": "f", "next": elsewhere}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/watchlist", elsewhere
    assert _f4_band(item_id) == (owner, "F", None)

    # Signed out: to log in, with the way back, and nothing written.
    client.cookies.clear()
    r = client.post(url, data={"item_id": item_id, "band": "G", "next": back}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login?next=" + quote(back, safe="")
    _f4_install(monkeypatch, _f4_answer())
    body = _f4_page(client, "&house_number=142&band=F")
    login = "/login?next=" + quote("/running-costs?postcode=M14%205TG&house_number=142&band=F", safe="/")
    assert f'<a id="rc-login-link" href="{login}">Log in</a> to save the band with a home in My properties.' in body
    assert 'id="rc-save"' not in body

    # Another account: refused, nothing written, and its own page neither
    # offers to save onto the home nor opens on the owner's band.
    _signed_in(client, "f4-stranger@example.com")
    r = client.post(url, data={"item_id": item_id, "band": "G", "next": back}, follow_redirects=False)
    assert r.status_code == 403 and _f4_band(item_id) == (owner, "F", None)
    body = _f4_page(client, "&house_number=142")
    assert 'id="rc-save"' not in body and '<option value="D" selected>' in body
    assert ('open <a href="/property?postcode=M14%205TG&amp;house_number=142">its report</a> first: '
            "that puts it in My properties.") in body


def test_f4_a_row_taken_over_by_another_home_keeps_nothing_of_the_last_one(client):
    from app import asking_prices, db
    from app.models import SavedAskingPrice
    uid, item_id = _f1_save_home(client, "f4-reuse@example.com", "144")
    home = {"id": item_id, "postcode": "M14 5TG", "house_number": "144"}

    def stale(price, band):
        # A row left by a removed home whose id this home now holds.
        with db.get_session() as session:
            row = session.get(SavedAskingPrice, item_id) or SavedAskingPrice(item_id=item_id)
            row.user_id, row.postcode, row.house_number, row.price, row.band = uid + 1000, "M14 5TG", "9", price, band
            session.add(row)
            session.commit()

    stale(500000, "G")
    assert asking_prices.band_for(uid, home) is None and asking_prices.for_items(uid, [home]) == {}
    assert asking_prices.saved_home(uid, "M14 5TG", "144")["band"] is None
    assert asking_prices.save(uid, item_id, 650000) is True
    assert _f4_band(item_id) == (uid, None, 650000)
    stale(500000, None)
    assert asking_prices.save_band(uid, item_id, "F") is True
    assert _f4_band(item_id) == (uid, "F", None)
    assert asking_prices.band_for(uid, home) == "F" and asking_prices.saved_home(uid, "M14 5TG", "144")["band"] == "F"
    # Not a band, or not this account's home: nothing written.
    assert asking_prices.save_band(uid, item_id, "Z") is False
    assert asking_prices.save_band(uid + 1, item_id, "G") is False
    assert asking_prices.save_band(uid, 987649, "G") is False
    assert _f4_band(item_id) == (uid, "F", None)


def test_f4_the_picker_is_styled_from_tokens_and_the_lamp_needs_no_rule():
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8").replace("\r\n", "\n")
    block = css.split("/* The band picker moves every figure, and the energy toggle beside it", 1)[1]
    block = block.split("/* Sales since, on the comparables page", 1)[0]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", block), "a colour outside the tokens"
    # Tap targets at least 44 by 36px, and the select 16px on a phone.
    assert re.search(r"\.rc-chip span \{[^}]*min-width: 2\.75rem;[^}]*min-height: 2\.25rem;", block)
    assert ".rc-band-pick select { min-height: 2.25rem; }" in block
    assert ".rc-band-pick .rc-pick-go,\n.rc-save .rc-save-btn { min-height: 2.75rem; }" in block
    assert re.search(r"\.rc-save \.rc-save-clear:hover \{[^}]*min-height: 2\.25rem;", block)
    assert "@media (max-width: 600px) {\n    .rc-band-pick select { font-size: var(--text-base); }" in block
    assert not re.search(r"theme-dark[^{]*\.rc-", css)
# ---- The review of 20 September 2026 -------------------------------------


def test_f1_a_sale_with_no_price_is_hidden_with_the_rest(monkeypatch):
    """A row whose amount is missing used to stay in the table while the
    figures said nothing matched, so the page showed a row under "nothing
    to place a price among"."""
    from app.services import comparables_view

    rows = [
        {"amount": 400000, "property_type": "Detached", "tenure": "Freehold", "date": _ago(30)},
        {"amount": None, "property_type": "Detached", "tenure": "Freehold", "date": _ago(40)},
    ]
    import datetime as _dt

    view = comparables_view.build(rows, {"types": ["detached"], "tenure": "any", "years": 0},
                                  today=_dt.date.today())
    assert view["shown"] == [True, False]
    assert view["count"] == 1


def test_f3_the_school_page_says_when_it_shows_only_the_newest_saved_homes(client, monkeypatch):
    """Nine saved homes, eight rows: the page says so, as the shortlist does."""
    _f3_seed()
    _f3_lookup(monkeypatch)
    _signed_in(client, "f3-capped@example.com")
    for n in range(9):
        assert client.post("/watchlist/save",
                           data={"postcode": "LN1 1AA", "house_number": f"{401 + n} Test Road"},
                           follow_redirects=False).status_code == 303
    flat = _flat(_f3_page(client))
    assert "Your 8 most recently saved homes, of 9." in flat
    assert "My properties</a> has them all." in flat


# ==== F5. Several years of admission distances ============================
# Every school page says in so many words that the distance moves each
# year, and then showed one year, because school_admission_radii is keyed
# on urn alone. Four councils publish a column per year (Haringey,
# Bristol, Bexley, Solihull) and the importer read those columns and threw
# all but the newest away. They now have their own table: the map draws
# any year a council published, a checked postcode is answered against all
# of them, the shortlist email can name the year before, and the nearby
# table's "varies" is gone. Nothing is worked out. Every year on the page
# is a figure a council published, and a column the document does not date
# is dropped rather than guessed at.

F5_URN = 990801            # Larkfield Primary School: three published years
F5_SLUG = "larkfield-primary-school"
F5_ONE_URN = 990802        # Marsden High School: one published year, the ordinary case
F5_ONE_SLUG = "marsden-high-school"
F5_NEARBY_URN = 990803     # next door to Larkfield, its council's label "varies"
F5_FIXTURES = ROOT / "tests" / "fixtures" / "admission_radii"
F5_POINTS = {
    "HD7 1AA": (53.6350, -1.85),   # 0.35 miles from Larkfield: inside all three years
    "HD7 2BB": (53.6503, -1.85),   # 1.4 miles: inside the widest published year only
}


def _f5_importer():
    """scripts/import_admission_radii.py, loaded by path the way the other
    script tests load theirs. Importing it reaches nothing: every URL sits
    inside a fetch function, and no fetch function is called here."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "import_admission_radii_for_test", ROOT / "scripts" / "import_admission_radii.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _f5_table(name):
    """A saved fixture as pdfplumber hands a table over: a list of rows of
    cells, an empty cell as None and "\\n" as a line break inside one."""
    rows = []
    for line in (F5_FIXTURES / name).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        rows.append([(cell.replace("\\n", "\n") or None) for cell in line.split("|")])
    return rows


def _f5_seed():
    from app import db
    from app.models import School, SchoolAdmissionRadius, SchoolAdmissionRadiusYear

    with db.get_session() as session:
        if session.get(School, F5_URN) is None:
            session.add(School(urn=F5_URN, name="Larkfield Primary School", phase="Primary",
                               type_name="Community school", postcode="HD7 1ZZ",
                               latitude=53.63, longitude=-1.85, ofsted_rating=2, ofsted_rating_label="Good"))
            # The single-figure row a multi-year council gives: "varies",
            # because its document is a table of years, not one year.
            session.add(SchoolAdmissionRadius(urn=F5_URN, last_distance_miles=0.9, academic_year="varies",
                                              source_authority="Kirklees"))
            for year, miles in (("2026", 0.9), ("2025", 1.2), ("2024", 1.75)):
                session.add(SchoolAdmissionRadiusYear(urn=F5_URN, academic_year=year,
                                                      last_distance_miles=miles, source_authority="Kirklees"))
        if session.get(School, F5_ONE_URN) is None:
            session.add(School(urn=F5_ONE_URN, name="Marsden High School", phase="Secondary",
                               type_name="Academy converter", postcode="HD7 6ZZ",
                               latitude=53.60, longitude=-1.92))
            session.add(SchoolAdmissionRadius(urn=F5_ONE_URN, last_distance_miles=2.4, academic_year="2025/26",
                                              source_authority="Kirklees"))
        if session.get(School, F5_NEARBY_URN) is None:
            session.add(School(urn=F5_NEARBY_URN, name="Slaithwaite Junior School", phase="Primary",
                               type_name="Community school", postcode="HD7 1YY",
                               latitude=53.633, longitude=-1.853))
            session.add(SchoolAdmissionRadius(urn=F5_NEARBY_URN, last_distance_miles=1.1, academic_year="varies",
                                              source_authority="Kirklees"))
        session.commit()


def _f5_lookup(monkeypatch):
    async def _lookup(raw):
        compact = raw.replace(" ", "").upper()
        for postcode, (lat, lon) in F5_POINTS.items():
            if postcode.replace(" ", "") == compact:
                return {"postcode": postcode, "latitude": lat, "longitude": lon, "admin_district": "Kirklees"}
        return None

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)


def _f5_page(client, query=""):
    r = client.get(f"/school/{F5_URN}/{F5_SLUG}" + (f"?{query}" if query else ""))
    assert r.status_code == 200
    return r.text.replace("\r\n", "\n")


def _f5_years_held(urn):
    from app.services import schools_db

    return {y["academic_year"]: y["miles"] for y in schools_db.admission_years(urn)}


def test_f5_the_four_multi_year_parsers_return_every_year_the_document_names():
    """Each parser still returns exactly the figure it returned before,
    and now hands back every other year its own document carries."""
    imp = _f5_importer()

    bristol = imp._bristol_rows(_f5_table("bristol_furthest_distance.txt"))
    assert [r["school_name"] for r in bristol] == ["Ashton Gate Primary School", "Bishop Road Primary School"]
    # Unchanged: the most recent column that parses, converted from km.
    assert round(bristol[0]["last_distance_miles"], 6) == round(0.8 / 1.60934, 6)
    assert sorted(bristol[0]["years"]) == ["2020", "2022", "2024", "2026"]
    assert bristol[0]["years"]["2026"] == bristol[0]["last_distance_miles"]
    assert round(bristol[0]["years"]["2020"], 6) == round(0.5 / 1.60934, 6)
    # "D", "N" and the overlapping-text glitch are years with no usable
    # figure, so they are not years here at all.
    assert "2021" not in bristol[0]["years"] and "2023" not in bristol[0]["years"]
    # A school whose newest column is "D" keeps its own newest real year.
    assert bristol[1]["years"]["2025"] == bristol[1]["last_distance_miles"]
    assert "2026" not in bristol[1]["years"]

    solihull = imp._solihull_rows(_f5_table("solihull_reception.txt"))
    assert [r["school_name"] for r in solihull] == ["Alderbrook School", "Balsall Common Primary School"]
    assert solihull[0]["last_distance_miles"] == 1.85
    assert solihull[0]["years"] == {"2025": 1.85, "2024": 2.01}
    # "N/A" in the newest year still falls back to the next year down.
    assert solihull[1]["last_distance_miles"] == 1.42
    assert solihull[1]["years"] == {"2024": 1.42, "2023": 1.5}

    bexley = imp._bexley_rows(_f5_table("bexley_secondary.txt"))
    # A school that offered every applicant a place in the last column is
    # still no record at all, years or no years.
    assert [r["school_name"] for r in bexley] == ["Beths Grammar School", "Townley Grammar School"]
    assert bexley[0]["last_distance_miles"] == 2.5
    assert bexley[0]["years"] == {"2017": 3.1, "2019": 2.9, "2020": 2.7, "2022": 2.5}
    assert bexley[1]["years"]["2022"] == bexley[1]["last_distance_miles"] == 1.75

    haringey = imp._haringey_rows((F5_FIXTURES / "haringey_primary.html").read_text(encoding="utf-8"))
    assert [r["school_name"] for r in haringey] == ["Alexandra Primary School", "Coldfall Primary School"]
    assert haringey[0] == {"school_name": "Alexandra Primary School", "last_distance_miles": 0.512,
                           "years": {"2024": 0.512, "2023": 0.48, "2022": 0.6}}
    # "All" and "N/A" are skipped in the years as they are in the figure.
    assert haringey[1]["years"] == {"2025": 0.345, "2024": 0.4, "2022": 0.38}


def test_f5_a_column_the_document_does_not_date_is_never_labelled():
    """The label has to come out of the document, as the figure does. A
    column with no year in its header is dropped, not guessed at."""
    imp = _f5_importer()
    assert imp._year_label("2024/25") == "2024/25"
    assert imp._year_label("2024-25") == "2024/25"
    assert imp._year_label("Sept 2026") == "2026"
    assert imp._year_label("@ 1 July 2022") == "2022"
    # Relative only against the dated column in the same header row.
    assert imp._year_label("5 years ago", 2022) == "2017"
    for cell in ("", None, "varies", "Distance (miles)", "5 years ago", "Places offered"):
        assert imp._year_label(cell) == "", cell
    # Every label a parser can produce is one the site reads as a year.
    for cell in ("2024/25", "2024-25", "Sept 2026", "@ 1 July 2022"):
        assert app_main._year_label(imp._year_label(cell)) == imp._year_label(cell)

    # Bexley's booklet without its dated column: the same figure as ever,
    # and not one labelled year.
    table = _f5_table("bexley_secondary.txt")
    table[0][-1] = "Most recent"
    rows = imp._bexley_rows(table)
    assert rows[0]["last_distance_miles"] == 2.5 and rows[0]["years"] == {}
    # A grouped header names its year once per pair of columns, and the
    # school-name column never inherits the year of the column after it.
    assert imp._grouped_year_labels(["School", "2025 offers", "Distance", "2024 offers", "Distance"]) == [
        "", "2025", "2025", "2024", "2024"]

    # One registry name has a comma in it, and the multi-year import is
    # run on named councils, so --only has to survive "Bristol, City of".
    known = {t[0] for t in imp._AUTHORITIES}
    assert "Bristol, City of" in known and {"Haringey", "Bexley", "Solihull"} <= known
    assert imp._only_from_argv(["x"], known) is None
    assert imp._only_from_argv(["x", "--only", "Bristol, City of"], known) == ["Bristol, City of"]
    assert imp._only_from_argv(["x", "--only", "Essex,Hounslow"], known) == ["Essex", "Hounslow"]
    assert imp._only_from_argv(["x", "--years", "--only", "Haringey", "--only", "Bristol, City of"], known) == [
        "Haringey", "Bristol, City of"]
    assert imp._only_from_argv(["x", "--only"], known) == []


def test_f5_the_years_go_to_the_new_table_only_and_never_delete(client, monkeypatch):
    """The new code path writes school_admission_radius_years and nothing
    else, upserts on (urn, academic_year), and leaves a year this year's
    document has stopped carrying exactly where it was."""
    from app import db
    from app.models import School, SchoolAdmissionRadius, SchoolAdmissionRadiusYear, SchoolDetail

    # Never anything but the throwaway SQLite database.
    assert str(db._get_engine().url).startswith("sqlite")
    imp = _f5_importer()
    assert str(imp._get_engine().url).startswith("sqlite")

    with db.get_session() as session:
        if session.get(School, 990811) is None:
            session.add(School(urn=990811, name="Thornbank Primary School", phase="Primary",
                               type_name="Community school", postcode="BS7 1ZZ",
                               latitude=51.48, longitude=-2.58))
            session.add(SchoolDetail(urn=990811, local_authority="Testshire"))
            session.commit()

    fetched = [{"school_name": "Thornbank Primary School", "last_distance_miles": 0.9,
                "years": {"2026": 0.9, "2025": 1.1, "2024": 0.0}}]
    monkeypatch.setattr(imp, "_AUTHORITIES", [("Testshire", "varies", lambda: [dict(r) for r in fetched])])

    # Without the list, build_records behaves exactly as it always has.
    with db.get_session() as session:
        plain = imp.build_records(session)
        years = []
        with_years = imp.build_records(session, years=years)
    assert plain == with_years == [{"urn": 990811, "academic_year": "varies",
                                    "last_distance_miles": 0.9, "source_authority": "Testshire"}]
    # A year whose figure is not a figure (the 0.0 a council writes for
    # "no place was decided on distance") is dropped here too.
    assert sorted(y["academic_year"] for y in years) == ["2025", "2026"]
    assert all(y["urn"] == 990811 and y["source_authority"] == "Testshire" for y in years)

    imp.load_years_into_db(years)
    imp.load_years_into_db(years)  # idempotent: a second run changes nothing
    with db.get_session() as session:
        held = session.scalars(
            select(SchoolAdmissionRadiusYear).where(SchoolAdmissionRadiusYear.urn == 990811)
        ).all()
        assert {h.academic_year: h.last_distance_miles for h in held} == {"2026": 0.9, "2025": 1.1}
        # The single-figure table is untouched by this path.
        assert session.get(SchoolAdmissionRadius, 990811) is None

    # This year's document drops 2025 and moves 2026: the dropped year
    # stays, because the council has not unpublished it.
    fetched[0]["years"] = {"2026": 0.95}
    with db.get_session() as session:
        again = []
        imp.build_records(session, years=again)
    imp.load_years_into_db(again)
    assert _f5_years_held(990811) == {"2026": 0.95, "2025": 1.1}


def test_f5_the_page_steps_through_the_years_and_draws_the_one_asked_for(client, monkeypatch):
    _f5_seed()
    _f5_lookup(monkeypatch)
    body = _f5_page(client)
    flat = _flat(body)

    # Three years, newest first, each a real link carrying ?year=, so the
    # choice works with no script at all and can be shared.
    pick = _flat(body.split('class="school-year-pick"', 1)[1].split("</div>", 1)[0])
    assert re.findall(r'data-year="([^"]+)" data-miles="([^"]+)"', pick) == [
        ("2026", "0.9"), ("2025", "1.2"), ("2024", "1.75")]
    assert pick.count(f'href="/school/{F5_URN}/{F5_SLUG}?year=') == 3
    assert pick.count('aria-current="true"') == 1
    assert 'data-year="2026" data-miles="0.9" data-no-limit="no" aria-current="true"' in pick
    assert "3 years published by Kirklees, newest first." in flat

    # The newest year is what the map draws and what the caption says,
    # and it is the figure the page's own tile carries.
    assert '<span id="school-map-miles">0.9</span> miles' in flat
    assert '<span id="school-map-year">in 2026</span>' in flat
    assert "miles: 0.9, noLimit: false" in flat
    assert '<span class="score-tile-value">0.9 mi</span>' in body

    # Asking for another year moves the circle, its caption and the map's
    # own label, on the server, with no script involved.
    older = _f5_page(client, "year=2024")
    older_flat = _flat(older)
    assert '<span id="school-map-miles">1.75</span> miles' in older_flat
    assert '<span id="school-map-year">in 2024</span>' in older_flat
    assert "miles: 1.75, noLimit: false" in older_flat
    assert 'aria-label="Map of Larkfield Primary School and the 1.75 mile admission distance, 2024"' in older
    assert 'data-year="2024" data-miles="1.75" data-no-limit="no" aria-current="true"' in _flat(older)
    # A year this council never published falls back to the newest one.
    for asked in ("year=2019", "year=%3Cscript%3E", "year="):
        assert '<span id="school-map-miles">0.9</span> miles' in _flat(_f5_page(client, asked))

    # One published year says so and offers nothing to step through.
    one = client.get(f"/school/{F5_ONE_URN}/{F5_ONE_SLUG}")
    assert one.status_code == 200
    assert "One published year so far: 2025/26, from Kirklees." in _flat(one.text)
    assert 'class="school-year-pick"' not in one.text

    # Both map branches redraw the same circle from the same stepper.
    assert body.count("window.schoolYearStepper(function (miles") == 1
    assert "function drawRing(" in body

    # A year is the same page for everyone, so a crawler walking the
    # stepper's links is served them from the anonymous cache like any
    # other school page; a checked postcode never is, and the canonical
    # link stays the year-less page whichever year is drawn.
    assert f'rel="canonical" href="https://testserver/school/{F5_URN}/{F5_SLUG}">' in older
    assert client.get(f"/school/{F5_URN}/{F5_SLUG}?year=2025").headers.get("x-anon-cache") == "miss"
    assert client.get(f"/school/{F5_URN}/{F5_SLUG}?year=2025").headers.get("x-anon-cache") == "hit"
    for _ in range(2):
        assert client.get(f"/school/{F5_URN}/{F5_SLUG}?check=HD7+1AA&year=2025").headers.get("x-anon-cache") is None


def test_f5_a_checked_postcode_is_answered_across_every_published_year(client, monkeypatch):
    _f5_seed()
    _f5_lookup(monkeypatch)
    # 0.35 miles: comfortably inside 0.9, 1.2 and 1.75.
    flat = _flat(_f5_page(client, "check=HD7+1AA"))
    assert "<strong>Likely in all 3 published years.</strong>" in flat
    assert '<span class="school-year-run-year">2026</span> <span class="school-year-run-label">Likely</span>' in flat
    assert flat.count('class="school-year-run school-year-run-likely"') == 3
    assert '<span class="school-year-run-miles">1.75 mi</span>' in flat

    # 1.4 miles: outside 2026's 0.9 and 2025's 1.2, inside 2024's 1.75.
    body = _f5_page(client, "check=HD7+2BB")
    flat = _flat(body)
    assert "<strong>Unlikely in 2 of the last 3 published years.</strong>" in flat
    assert flat.count('class="school-year-run school-year-run-unlikely"') == 2
    assert flat.count('class="school-year-run school-year-run-likely"') == 1
    # The most recent year is the reading the verdict itself gives, so
    # the two can never disagree.
    assert '<div class="admission-verdict admission-verdict-unlikely" id="verdict">' in body
    # The checked postcode travels with the year links.
    assert f'href="/school/{F5_URN}/{F5_SLUG}?check=HD7%202BB&amp;year=2024#school-page-map"' in body

    # One published year says nothing across years: "1 of the last 1" is
    # not an answer.
    one = client.get(f"/school/{F5_ONE_URN}/{F5_ONE_SLUG}?check=HD7+1AA")
    assert one.status_code == 200 and "published years" not in _flat(one.text)

    # Worked out again here, from the page's own verdict bands.
    years = app_main._published_years(
        {"academic_year": "varies", "miles": 0.9},
        [{"academic_year": y, "miles": m} for y, m in (("2024", 1.75), ("2026", 0.9), ("2025", 1.2))])
    assert [y["year"] for y in years] == ["2026", "2025", "2024"]
    across = app_main._readings_across_years(
        {"latitude": 53.63, "longitude": -1.85}, {"latitude": 53.6503, "longitude": -1.85}, years)
    assert [(r["year"], r["level"]) for r in across["rows"]] == [
        ("2026", "unlikely"), ("2025", "unlikely"), ("2024", "likely")]
    assert (across["matched"], across["total"], across["all"]) == (2, 3, False)
    # One year is no across-years answer at all.
    assert app_main._readings_across_years({"latitude": 53.63, "longitude": -1.85},
                                           {"latitude": 53.6503, "longitude": -1.85}, years[:1]) is None


def test_f5_varies_no_longer_reaches_the_nearby_schools_table(client, monkeypatch):
    """The table printed the raw field, so a school whose council
    publishes several years read "1.1 mi varies", as if the distance
    itself varied."""
    _f5_seed()
    _f5_lookup(monkeypatch)
    body = _f5_page(client)
    table = body.split('id="nearby-schools"', 1)[1].split("</table>", 1)[0]
    assert "Slaithwaite Junior School" in table and "1.1 mi" in table
    assert "varies" not in table
    # A real year is still printed beside its distance.
    assert "Marsden High School" in table and "2025/26" in table
    assert app_main._year_label("2025/26") == "2025/26" and app_main._year_label("varies") == ""


def test_f5_the_stepper_is_styled_from_tokens_and_the_lamp_needs_no_rule():
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    block = css.split("/* Several published years (18 Sep 2026", 1)[1].split("\n.catchment-badge-mid", 1)[0]
    assert "#" not in block, "a colour that is not a token"
    # The chosen year is marked with aria-current, never by colour alone.
    assert "a[aria-current]" in block
    # Every year is at least 44 by 32px to tap.
    assert "min-width: 2.75rem;" in block and "min-height: 2rem;" in block
    assert "body.theme-dark .school-year" not in css


def test_f5_the_alert_email_names_the_previous_published_year(client, monkeypatch):
    """When the council's own previous year is held as well as the new
    one, the email names it: that is a published figure against a
    published figure, which "was" (what we recorded last time) is not."""
    from app import db
    from app.models import School, SchoolAdmissionRadius, SchoolAdmissionRadiusYear
    from app.services import email as email_service

    urn = 990821
    with db.get_session() as session:
        if session.get(School, urn) is None:
            session.add(School(urn=urn, name="Kirkburton Academy", phase="Secondary",
                               type_name="Academy converter", postcode="HD8 1ZZ",
                               latitude=53.62, longitude=-1.70))
            session.add(SchoolAdmissionRadius(urn=urn, last_distance_miles=1.4, academic_year="2025",
                                              source_authority="Kirklees"))
            session.add(SchoolAdmissionRadiusYear(urn=urn, academic_year="2025", last_distance_miles=1.4,
                                                  source_authority="Kirklees"))
            session.commit()

    _signed_in(client, "f5-alerts@example.com")
    client.post("/schools/shortlist/save", data={"urn": str(urn), "next": "/schools/shortlist"})
    client.post("/schools/shortlist/alerts", data={"enabled": "on"})
    client.cookies.clear()

    sent = []

    async def _send(to, subject, html):
        sent.append((to, subject, html))
        return True

    monkeypatch.setattr(email_service, "send_email", _send)
    monkeypatch.setattr(email_service, "is_configured", lambda: True)
    monkeypatch.setenv("ALERTS_CRON_SECRET", "s3cret")
    headers = {"x-alerts-secret": "s3cret"}
    client.post("/internal/send-admission-updates", headers=headers)   # first sighting only records

    # The council republishes, and the year before it is on file.
    with db.get_session() as session:
        row = session.get(SchoolAdmissionRadius, urn)
        row.last_distance_miles, row.academic_year = 1.1, "2026"
        session.add(SchoolAdmissionRadiusYear(urn=urn, academic_year="2026", last_distance_miles=1.1,
                                              source_authority="Kirklees"))
        session.commit()
    client.post("/internal/send-admission-updates", headers=headers)
    mine = [m for m in sent if m[0] == "f5-alerts@example.com"]
    assert len(mine) == 1
    html = mine[0][2]
    assert "Now admits from <strong>1.1 miles</strong> (2026)" in html
    assert "Was 1.4 miles (2025)." in html
    assert "Kirklees also publishes <strong>1.4 miles</strong> for 2025." in html
    assert "never on a schedule" in html

    # A school with no year on file says nothing extra.
    assert app_main._admission_update_email_html(
        [{"urn": urn, "slug": "x", "name": "Kirkburton Academy", "miles": 1.1, "academic_year": "2026",
          "was_miles": 1.4, "was_year": "2025"}], "https://example.test/schools/shortlist",
    ).count("also publishes") == 0


def test_f5_the_years_lookup_is_one_statement_for_the_whole_list(client):
    """A page that lists saved schools must not pay a round trip each."""
    from sqlalchemy import event

    from app import db
    from app.services import schools_db

    _f5_seed()
    engine = db._get_engine()
    statements = []

    def _seen(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _seen)
    try:
        got = schools_db.admission_years_for([F5_URN, F5_ONE_URN, F5_NEARBY_URN])
    finally:
        event.remove(engine, "before_cursor_execute", _seen)
    assert len([s for s in statements if "school_admission_radius_years" in s]) == 1, statements
    # Newest first, and a school with no row is absent rather than given
    # a year it does not have.
    assert [y["academic_year"] for y in got[F5_URN]] == ["2026", "2025", "2024"]
    assert F5_ONE_URN not in got and F5_NEARBY_URN not in got
    assert schools_db.admission_years(F5_ONE_URN) == []
    assert schools_db.admission_years_for([]) == {}


# ==== F6. Where two chosen schools are both in reach ======================
# A family with two children, or one child and a second preference, had to
# hold two school pages open and intersect two circles by eye. ?with= puts
# a second school's published distance on the same map and lists the
# postcode districts whose centre falls inside both, cheapest first on the
# same Land Registry medians the page's own "within reach" table ranks on.
# Nothing here is worked out: the distances are councils' figures, the
# medians are recorded sales, and a budget only hides the rows above it. A
# school with no published distance cannot be added, and the page says so.

F6_A_URN = 990851          # Trent Bridge Primary, at NG1's centre, 2.8 miles
F6_A_SLUG = "trent-bridge-primary-school"
F6_B_URN = 990852          # Sneinton Dale Academy, at NG3's centre, 2.1 miles
F6_B_SLUG = "sneinton-dale-academy"
F6_NONE_URN = 990853       # Colwick Free School: on the register, no council figure
F6_ALSO_URN = 990854       # Sneinton Park Primary: shares a word with F6_B, miles away
# NG1's and NG3's real centres, so the districts are measured off the same
# app/data/outcodes.json every other page reads.
F6_A_POINT = (52.9548, -1.1484)
F6_B_POINT = (52.9697, -1.1273)
# Inside 2.8 miles of A: NG1 0.00, NG7 1.03, NG3 1.35, NG2 1.46, NG80 2.64,
# NG90 2.66, NG4 2.77, NG8 2.79. Inside 2.1 of B: NG3 0.00, NG1 1.35,
# NG4 1.65, NG5 2.04, NG7 2.05. Inside both: NG1, NG7, NG3, NG4.
F6_BOTH = {"NG1", "NG7", "NG3", "NG4"}
F6_MEDIANS = {"NG1": 320000, "NG7": 240000, "NG3": 275000,
              # In reach of A alone, and the cheapest of the lot: it must
              # never reach the paired table.
              "NG2": 150000,
              # In reach of B alone.
              "NG5": 199000}
# NG4 is inside both and has no median: named, never ranked.


def _f6_seed():
    from app import db
    from app.models import School, SchoolAdmissionRadius

    with db.get_session() as session:
        if session.get(School, F6_A_URN) is None:
            session.add(School(urn=F6_A_URN, name="Trent Bridge Primary School", phase="Primary",
                               type_name="Community school", postcode="NG1 5AA",
                               latitude=F6_A_POINT[0], longitude=F6_A_POINT[1],
                               ofsted_rating=2, ofsted_rating_label="Good"))
            session.add(SchoolAdmissionRadius(urn=F6_A_URN, last_distance_miles=2.8, academic_year="2025",
                                              source_authority="Nottingham"))
        if session.get(School, F6_B_URN) is None:
            session.add(School(urn=F6_B_URN, name="Sneinton Dale Academy", phase="Secondary",
                               type_name="Academy converter", postcode="NG3 7AA",
                               latitude=F6_B_POINT[0], longitude=F6_B_POINT[1],
                               ofsted_rating=1, ofsted_rating_label="Outstanding"))
            session.add(SchoolAdmissionRadius(urn=F6_B_URN, last_distance_miles=2.1, academic_year="2024/25",
                                              source_authority="Nottingham"))
        if session.get(School, F6_NONE_URN) is None:
            session.add(School(urn=F6_NONE_URN, name="Colwick Free School", phase="Primary",
                               type_name="Free school", postcode="NG4 2AA",
                               latitude=52.95, longitude=-1.08))
        if session.get(School, F6_ALSO_URN) is None:
            # Far from both, so it changes no table here: it is only
            # ever a second name for "Sneinton" to match.
            session.add(School(urn=F6_ALSO_URN, name="Sneinton Park Primary School", phase="Primary",
                               type_name="Community school", postcode="LS1 1AA",
                               latitude=53.80, longitude=-1.55))
            session.add(SchoolAdmissionRadius(urn=F6_ALSO_URN, last_distance_miles=0.8,
                                              academic_year="2025", source_authority="Leeds"))
        session.commit()


def _f6_prices(monkeypatch):
    """The area guides' medians, as _district_price_rows_by_outcode hands
    them over. One fake for both the single school's table and the pair's,
    so the two can only ever rank on the same figures."""
    districts = {o["outcode"]: o.get("district", "") for o in app_main.ALL_OUTCODES}
    monkeypatch.setattr(app_main, "_district_price_rows_by_outcode", lambda: {
        code: {"outcode": code, "median": median, "count": 40,
               "low": 90000, "high": 900000, "district": districts.get(code, "")}
        for code, median in F6_MEDIANS.items()
    })


def _f6_page(client, query=""):
    r = client.get(f"/school/{F6_A_URN}/{F6_A_SLUG}" + (f"?{query}" if query else ""))
    assert r.status_code == 200
    return r.text.replace("\r\n", "\n")


def _f6_section(body):
    return body.split('id="both-in-reach"', 1)[1].split("</section>", 1)[0]


def _f6_table_outcodes(body):
    table = body.split('id="pair-areas"', 1)[1].split("</table>", 1)[0]
    return re.findall(r'<tr data-outcode="([^"]+)" data-median="([^"]+)">', table)


def test_f6_a_second_school_lists_only_the_districts_inside_both_distances(client, monkeypatch):
    _f6_seed()
    _f6_prices(monkeypatch)
    body = _f6_page(client, f"with={F6_B_URN}")
    flat = _flat(body)

    # Cheapest first, and only the districts whose centre falls inside
    # both published distances. NG2 is the cheapest district in the data
    # and is inside this school's distance alone, so it must not be here.
    assert _f6_table_outcodes(body) == [("NG7", "240000"), ("NG3", "275000"), ("NG1", "320000")]
    assert "NG2" not in _f6_section(body) and "NG5" not in _f6_section(body)
    # NG4 falls inside both and has no median: named, never ranked.
    assert "1 more district falls inside both without enough recorded sales to rank:" in flat
    assert '<a href="/area/NG4">NG4</a>.' in flat

    # The lead names the cheapest and the dearest of the ones shown, the
    # second school's own council's figure, and the caveat the single
    # school's table already carries.
    assert 'The cheapest is <a href="/area/NG7">NG7</a> at £240,000' in flat
    assert 'the dearest shown, <a href="/area/NG1">NG1</a>, £320,000' in flat
    assert "A district is wide, so check the address itself, not the list." in flat
    assert "Sneinton Dale Academy admitted from <strong>2.1 miles</strong> in 2024/25, published by Nottingham." in flat
    assert "Sold prices from HM Land Registry, via each district's area guide." in flat

    # Each district is measured from each school, against that school's
    # own published distance.
    table = body.split('id="pair-areas"', 1)[1].split("</table>", 1)[0]
    assert ('<th>District</th><th>Area</th><th class="num">From Trent Bridge Primary School</th>'
            '<th class="num">From Sneinton Dale Academy</th><th class="num">Median sold price</th>') in _flat(table)
    rows = re.findall(r'<td class="num">([0-9.]+) mi</td>\s*<td class="num">([0-9.]+) mi</td>', table)
    assert len(rows) == 3
    for from_a, from_b in rows:
        assert float(from_a) <= 2.8 and float(from_b) <= 2.1

    # Recomputed here off app/data/outcodes.json, the page's own source:
    # exactly these four districts have a centre inside both.
    assert {e["outcode"] for e in app_main._outcodes_within_pair(
        {"latitude": F6_A_POINT[0], "longitude": F6_A_POINT[1], "miles": 2.8},
        {"latitude": F6_B_POINT[0], "longitude": F6_B_POINT[1], "miles": 2.1})} == F6_BOTH

    # The key under the map names each school beside its own ring, so the
    # two circles are never told apart by colour alone.
    key = _flat(body.split('id="school-ring-key"', 1)[1].split("</p>", 1)[0])
    assert "Trent Bridge Primary School: 2.8 miles, 2025" in key
    assert "Sneinton Dale Academy</a>: 2.1 miles, 2024/25, published by Nottingham" in key
    assert 'class="key-ring key-ring-real"' in key and 'class="key-ring key-ring-pair"' in key

    # The labels on the map are the districts the table names, so the two
    # cannot disagree on the same screen.
    labels = json.loads(re.search(r"districts: (\[.*?\])\n", body).group(1))
    assert [d["code"] for d in labels] == ["NG7", "NG3", "NG1"]
    assert "whose centre falls inside both;" in flat

    # With no second school the page is as it was, and the section asks for one.
    alone = _f6_page(client)
    assert 'id="pair-areas"' not in alone and "partner: null" in alone
    assert "<h2>Two schools, both in reach</h2>" in alone
    assert "Only a school whose council has published a distance can be added." in _flat(alone)


def test_f6_a_budget_hides_the_districts_above_it(client, monkeypatch):
    _f6_seed()
    _f6_prices(monkeypatch)
    # The budget filters, it never works anything out: NG7 at 240,000 is
    # the only one of the three at or under 250,000.
    body = _f6_page(client, f"with={F6_B_URN}&budget=250,000")
    flat = _flat(body)
    assert _f6_table_outcodes(body) == [("NG7", "240000")]
    assert "cheapest first by the median of real sales around each district's centre, at or under £250,000" in flat
    assert "2 districts inside both distances are above £250,000 and not shown." in flat
    assert 'name="budget" value="250,000"' in body

    # A budget under every one of them says what the cheapest really is,
    # rather than leaving the reader to guess by how much.
    under = _flat(_f6_page(client, f"with={F6_B_URN}&budget=%C2%A3100000"))
    assert 'id="pair-areas"' not in under
    assert "3 postcode districts fall inside both distances, and every one has a median sold price above £100,000." in under
    assert 'The cheapest of them is <a href="/area/NG7">NG7</a> at £240,000.' in under

    # Typed with a pound sign, spaces or commas it is the same budget, and
    # with no digits in it at all it is no budget rather than nought.
    assert app_main._pair_budget("\u00a3450,000") == 450000
    assert app_main._pair_budget(" 450 000 ") == 450000
    assert app_main._pair_budget("about half a million") is None
    assert app_main._pair_budget("") is None and app_main._pair_budget("0") is None
    assert app_main._pair_budget("9" * 30) == app_main.PAIR_BUDGET_MAX
    assert _f6_table_outcodes(_f6_page(client, f"with={F6_B_URN}&budget=lots")) == [
        ("NG7", "240000"), ("NG3", "275000"), ("NG1", "320000")]


def test_f6_a_school_with_no_published_distance_is_refused_with_the_reason(client, monkeypatch):
    _f6_seed()
    _f6_prices(monkeypatch)
    # On the register, no figure from its council: it cannot be added, and
    # the page says which of the two reasons that is.
    refused = _flat(_f6_page(client, f"with={F6_NONE_URN}"))
    assert ("Colwick Free School has no admission distance published by its council, so it cannot be "
            "added here. This page shows published figures only, never a modelled one.") in refused
    assert 'id="pair-areas"' not in refused and "partner: null" in refused

    # A reference the register has never held is a different sentence.
    for junk in ("with=99999999", "with=nonsense", "with=%3Cscript%3E", "with=-4"):
        assert "No school with that reference in the Department for Education register." in _flat(_f6_page(client, junk))

    # This school is not its own second school.
    assert "That is this school. Pick a different one as the second." in _flat(_f6_page(client, f"with={F6_A_URN}"))

    # A name nobody is called, and a name several schools share.
    assert 'No school found called "Zzzz Academy of Nowhere".' in _flat(
        _f6_page(client, "with_q=Zzzz+Academy+of+Nowhere"))
    several = _f6_page(client, "with_q=Sneinton")
    assert 'More than one school matches "Sneinton". Which did you mean?' in _flat(several)
    assert f'href="/school/{F6_A_URN}/{F6_A_SLUG}?with={F6_B_URN}#both-in-reach"' in several
    assert f'href="/school/{F6_A_URN}/{F6_A_SLUG}?with={F6_ALSO_URN}#both-in-reach"' in several
    # This school's own name is not an answer to it.
    assert "That is this school. Pick a different one as the second." in _flat(
        _f6_page(client, "with_q=Trent+Bridge+Primary"))

    # A name matching one school with a published distance is taken as the
    # school meant, which is how the picker works with no script at all.
    assert _f6_table_outcodes(_f6_page(client, "with_q=Sneinton+Dale")) == [
        ("NG7", "240000"), ("NG3", "275000"), ("NG1", "320000")]
    # A name matching only a school with no figure says so by name.
    assert "Colwick Free School has no admission distance published by its council" in _flat(
        _f6_page(client, "with_q=Colwick"))

    # The typeahead's own answer carries the URN the picker builds ?with=
    # from, and says which schools cannot be added.
    rows = client.get("/api/school-search?q=Colwick+Free").json()["results"]
    assert [(r["urn"], r["has_page"]) for r in rows] == [(F6_NONE_URN, False)]
    assert client.get("/api/school-search?q=Sneinton+Dale").json()["results"][0]["urn"] == F6_B_URN


def test_f6_one_postcode_is_answered_for_both_schools_at_once(client, monkeypatch):
    _f6_seed()
    _f6_prices(monkeypatch)

    async def _lookup(raw):
        # NG3's centre: 1.35 miles from Trent Bridge, inside its 2.8, and
        # on Sneinton Dale's own doorstep.
        if raw.replace(" ", "").upper() == "NG31AA":
            return {"postcode": "NG3 1AA", "latitude": F6_B_POINT[0], "longitude": F6_B_POINT[1],
                    "admin_district": "Nottingham"}
        # NG2's centre: 1.46 from Trent Bridge, 2.41 from Sneinton Dale,
        # so inside one published distance and outside the other.
        return {"postcode": "NG2 1AA", "latitude": 52.9352, "longitude": -1.1353,
                "admin_district": "Rushcliffe"}

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    both = _flat(_f6_page(client, f"with={F6_B_URN}&check=NG3+1AA"))
    assert "<strong>NG3 1AA against both schools:</strong>" in both
    assert ('<span class="school-pair-read school-pair-read-likely"><span class="school-pair-read-name">'
            "Trent Bridge Primary School</span>") in both
    assert ('<span class="school-pair-read school-pair-read-likely"><span class="school-pair-read-name">'
            "Sneinton Dale Academy</span>") in both
    assert both.count('class="school-pair-read school-pair-read-') == 2

    # A postcode inside one distance and outside the other says exactly that.
    one = _flat(_f6_page(client, f"with={F6_B_URN}&check=NG2+1AA"))
    assert 'school-pair-read-likely"><span class="school-pair-read-name">Trent Bridge Primary School' in one
    assert 'school-pair-read-unlikely"><span class="school-pair-read-name">Sneinton Dale Academy' in one
    # The second reading is the page checker's own, against the second
    # school's own council's figure, so it cannot disagree with that
    # school's page.
    assert app_main._school_reading(
        {"latitude": F6_B_POINT[0], "longitude": F6_B_POINT[1], "miles": 2.1, "no_distance_limit": False},
        {"postcode": "NG2 1AA", "latitude": 52.9352, "longitude": -1.1353})["level"] == "unlikely"

    # The second school survives checking another postcode, and the
    # checked postcode survives swapping the second school.
    checked = _f6_page(client, f"with={F6_B_URN}&check=NG2+1AA")
    assert f'<input type="hidden" name="with" value="{F6_B_URN}">' in checked
    assert 'data-base="/school/990851/trent-bridge-primary-school?check=NG2%201AA&amp;"' in checked
    # With no second school there is no second reading.
    assert "against both schools" not in _f6_page(client, "check=NG2+1AA")


def test_f6_the_pair_is_saved_in_one_action_and_never_for_anyone_else(client, monkeypatch):
    _f6_seed()
    _f6_prices(monkeypatch)
    # Signed out, the save is an invitation to sign up, not a button.
    out = _f6_page(client, f"with={F6_B_URN}")
    assert ">Save both schools</button>" not in out
    assert ("/signup?next=/school/990851/trent-bridge-primary-school"
            "%3Fwith%3D990852%23both-in-reach") in out
    r = client.post("/schools/shortlist/save",
                    data={"urn": str(F6_A_URN), "also": str(F6_B_URN), "next": "/schools/shortlist"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    assert "Sneinton Dale Academy" not in client.get("/schools/shortlist", follow_redirects=True).text

    _signed_in(client, "f6-pair@example.com")
    assert f'<input type="hidden" name="also" value="{F6_B_URN}">' in _f6_page(client, f"with={F6_B_URN}")
    r = client.post("/schools/shortlist/save",
                    data={"urn": str(F6_A_URN), "also": str(F6_B_URN),
                          "next": f"/school/{F6_A_URN}/{F6_A_SLUG}?with={F6_B_URN}#both-in-reach"},
                    follow_redirects=False)
    assert r.status_code == 303
    shortlist = client.get("/schools/shortlist").text
    assert "Trent Bridge Primary School" in shortlist and "Sneinton Dale Academy" in shortlist
    assert 'Both are on <a href="/schools/shortlist">your shortlist</a>' in _flat(
        _f6_page(client, f"with={F6_B_URN}"))

    # A second URN that is not a number, is the first one again, or names
    # no school at all leaves nothing behind: a shortlist row pointing at
    # nothing would read as a school on the list.
    rows = shortlist.count("<td")
    for junk in ("nonsense", str(F6_A_URN), "99999999", "-2", "", "1e5"):
        assert client.post("/schools/shortlist/save",
                           data={"urn": str(F6_A_URN), "also": junk, "next": "/schools/shortlist"},
                           follow_redirects=False).status_code == 303
    assert client.get("/schools/shortlist").text.count("<td") == rows


def test_f6_the_pair_travels_in_the_address_and_works_with_no_script(client, monkeypatch):
    _f6_seed()
    _f6_prices(monkeypatch)
    body = _f6_page(client, f"with={F6_B_URN}")

    # Every part of the tool is a plain link or a GET form, and the
    # figures in the table are the server's.
    assert 'data-base="/school/990851/trent-bridge-primary-school?"' in body
    picker = body.split('class="search-form school-pair-form"', 1)[1].split("</form>", 1)[0]
    assert 'method="get"' in picker and 'name="with_q"' in picker
    budget = body.split('class="search-form school-pair-budget"', 1)[1].split("</form>", 1)[0]
    assert 'method="get"' in budget and f'name="with" value="{F6_B_URN}"' in budget

    # The picker only saves typing, and nothing typed leaves this site:
    # the only address its script asks for is this site's own endpoint.
    script = body.split("var form = document.getElementById('pair-form');", 1)[1].split("</script>", 1)[0]
    assert "/api/school-search?q=" in script
    assert "http://" not in script and "https://" not in script
    assert "localStorage" not in script

    # The canonical link stays the bare page and the links that build a
    # pair are nofollow: a pair is a tool, not a page to index.
    assert f'rel="canonical" href="https://testserver/school/{F6_A_URN}/{F6_A_SLUG}">' in body
    nearby = body.split('id="nearby-schools"', 1)[1].split("</table>", 1)[0]
    assert (f'<a class="school-pair-add" rel="nofollow" href="/school/{F6_A_URN}/{F6_A_SLUG}'
            f'?with={F6_B_URN}#both-in-reach">Both in reach</a>') in nearby

    # A pair is never served out of the anonymous page cache, so no reader
    # is handed another reader's second school.
    for query in (f"with={F6_B_URN}", "with_q=Sneinton", f"with={F6_B_URN}&budget=250000"):
        assert client.get(f"/school/{F6_A_URN}/{F6_A_SLUG}?{query}").headers.get("x-anon-cache") is None


def test_f6_both_map_branches_draw_the_second_ring(client, monkeypatch):
    _f6_seed()
    _f6_prices(monkeypatch)
    leaflet = _f6_page(client, f"with={F6_B_URN}")
    partner = json.loads(re.search(r"partner: (\{.*?\}),\n", leaflet, re.S).group(1))
    assert partner == {"lat": F6_B_POINT[0], "lng": F6_B_POINT[1], "name": "Sneinton Dale Academy",
                       "miles": 2.1, "noLimit": False, "year": "2024/25"}
    assert "partnerBounds = L.circle([S.partner.lat, S.partner.lng], {" in leaflet
    assert "if (partnerBounds) bounds.extend(partnerBounds);" in leaflet
    assert "bindPopup(S.partner.name)" in leaflet

    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    google = _f6_page(client, f"with={F6_B_URN}")
    assert "partnerBounds = new google.maps.Circle({" in google
    assert "if (partnerBounds) bounds.union(partnerBounds);" in google
    assert "title: S.partner.name" in google

    # Both branches describe the same map to a reader who cannot see it.
    for body in (leaflet, google):
        assert ('aria-label="Map of Trent Bridge Primary School and the 2.8 mile admission distance, 2025, '
                'with Sneinton Dale Academy and its 2.1 mile admission distance"') in body


def test_f6_the_pair_is_styled_from_tokens_and_the_lamp_needs_no_rule():
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    block = css.split("/* Two schools, both in reach (18 Sep 2026", 1)[1]
    block = block.split("/* Homes you looked at, under a school page's checker", 1)[0]
    # The ring key is the one exception, and it is why .key-ring-real is
    # hard-coded too: a swatch has to be the colour the map draws, which is
    # the same navy in both themes (21 Sep 2026, batch F review).
    without_key = re.sub(r"\.key-ring-pair \{[^}]*\}", "", block)
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", without_key), "a colour outside the tokens"
    assert "#2b4c8c" in block
    # "Both in reach" beside a neighbouring school takes a 32px tap.
    assert re.search(r"\.school-pair-add \{[^}]*min-height: 2rem;", block, re.S)
    # The lamp needs no rule of its own: the tokens carry the dark theme.
    assert not re.search(r"theme-dark[^{]*school-(pair|ring)", css)


# ==== F7. Your own must-haves, checked on every report ====================
# A buyer arrives with a list in their head: no worse than Zone 2, band C
# or better, freehold, that school. The report rated all of it and made
# them read forty-four cards to find the six lines they came for. Up to
# six plain thresholds are now set once and answered on every report and
# on My properties. Nothing is scored: each line is a published figure
# against the reader's own threshold, and a fact the report does not hold
# is "not yet known", never a miss. The rule batch B set holds: a
# condition on a locked check says where it opens and nothing else.

F7_SCHOOL = "Fernbrook Academy"


def _f7_gather(**overrides):
    """A report with a figure for every free condition, and for the
    locked ones too, so a test can watch the lock rather than a gap."""
    from tests.conftest import fake_gather
    base = {
        "flood_zone": {"zone": 2, "label": "Zone 2 (medium probability)", "source": None},
        "surface_water": {"label": "Low risk", "probability": "1 in 1000 (0.1%) to 1 in 100"},
        "broadband": {"gigabit_pct": 0, "ultrafast_pct": 0, "superfast_pct": 100,
                      "below_uso_pct": 0, "label": "Superfast"},
        "council_tax": {"authority": "Manchester", "slug": "manchester", "year": "2026-27",
                        "band_d": 1900.0, "bands": {"D": 1900.0}},
        "transactions": [{"address": "231 Test Street", "postcode": "M14 5TG", "amount": "250000",
                          "date": "2024-06-01", "tenure": "freehold"}],
        "sewage_outfalls": [{"name": "Outfall", "water_company": "United Utilities", "year": 2024,
                             "spill_count": 31, "duration_hrs": 260.0, "receiving_water": "Mersey",
                             "distance_m": 400, "current": True}],
        "clay_risk": {"class_2030": "Possible", "class_2050": "Probable",
                      "label_2030": "Possible", "label_2050": "Probable"},
        "bus_service": {"radius_m": 500, "stops": [], "count": 1, "nearest": None, "routes": [],
                        "best": {"name": "Wilmslow Road", "distance_m": 180, "weekday_day": 120,
                                 "weekday_day_per_hour": 8.0, "weekday_eve_per_hour": 2.0,
                                 "sunday_day_per_hour": 4.0, "routes": ["142"],
                                 "weekday_first": "05:40", "weekday_last": "23:50"},
                        "feed_date": None, "ref_weekday": None, "ref_sunday": None},
        "school_landscape": {
            "total_schools": 2, "good_or_better_pct": 79, "radius_km": 3,
            "all_schools": [
                {"name": F7_SCHOOL, "distance_m": 400, "phase_group": "Primary",
                 "admission_radius": {"last_distance_miles": 1.5}},
                {"name": "Meadowbank Primary School", "distance_m": 900, "phase_group": "Primary",
                 "catchment_estimate": {"radius_miles": 0.9}},
            ],
        },
    }
    base.update(overrides)
    # The real gather builds the verdicts from the landscape; the fake
    # stands in for the whole of it, so they are built here too.
    base.setdefault("school_verdicts", app_main._school_verdict_summary(base["school_landscape"]))
    # fake_gather marks every service it was not given as failed, and
    # the overflow returns arrive under sewage_outfalls, not "sewage".
    base.setdefault("sewage_error", False)
    return fake_gather(**base)


def _f7_context(**overrides):
    """The report context the panel reads, as the gather leaves it."""
    context = {"house_number": "231", **_f7_gather()}
    context["school_verdicts"] = app_main._school_verdict_summary(context["school_landscape"])
    context.update(overrides)
    return context


def _f7_page(client, house_number="231", **params):
    r = client.get("/property", params={"postcode": "M14 5TG", "house_number": house_number, **params})
    assert r.status_code == 200
    return r.text.replace("\r\n", "\n")


def _f7_data(body):
    blob = body.split('<script type="application/json" id="must-have-data">', 1)[1]
    return json.loads(blob.split("</script>", 1)[0])


def _f7_panel(body):
    return _flat(body.split('id="must-have-panel"', 1)[1].split("</div>", 1)[0])


def test_f7_each_condition_is_a_threshold_on_a_figure_the_report_already_holds():
    from app import must_haves

    context = _f7_context()
    known = must_haves.facts(context, premium_unlocked=True)
    assert known["flood"]["text"] == "Zone 2 (medium probability)"
    assert known["surface_water"]["text"] == "Low risk"
    assert known["epc"]["text"] == "Band C"
    assert known["broadband"]["text"] == "Superfast"
    assert known["council_tax"]["text"] == "£1,900 a year at Band D"
    assert known["tenure"]["text"] == "Freehold"
    assert known["sewage"]["text"] == "260 spill hours nearby in 2024"
    assert known["subsidence"]["text"] == "Possible by 2030"
    assert known["buses"]["text"] == "8 an hour at Wilmslow Road"
    # Published figures only: the school with a council's distance is
    # offered, the one with a modelled estimate is not.
    assert set(known["school"]["value"]) == {F7_SCHOOL}

    result = must_haves.evaluate({
        "flood": "2",            # Zone 2 against a Zone 2 home: met
        "surface_water": "Very low risk",
        "epc": "C",
        "council_tax": "2000",
        "tenure": "freehold",
        "sewage": "24",
        "school": F7_SCHOOL,
    }, known)
    met = {row["key"]: row["met"] for row in result["rows"]}
    assert met == {"flood": True, "surface_water": False, "epc": True, "council_tax": True,
                   "tenure": True, "sewage": False, "school": True}
    assert result["line"] == "5 of 7 met"

    # Worse on each scale is a miss, better is not.
    harder = must_haves.evaluate({"flood": "1", "epc": "B", "council_tax": "1750",
                                  "subsidence": "Improbable", "buses": "6"}, known)
    assert [(row["key"], row["met"]) for row in harder["rows"]] == [
        ("flood", False), ("epc", False), ("council_tax", False),
        ("subsidence", False), ("buses", True)]


def test_f7_a_figure_the_report_does_not_hold_is_never_counted_as_a_miss():
    from app import must_haves
    from app.services import flood_zones

    # Outside England the Environment Agency maps hold nothing, which is
    # a gap, not a Zone 1: the panel says so instead of counting a miss.
    welsh = _f7_context(flood_not_covered=flood_zones.outside_coverage("Wales"),
                        flood_zone=None, surface_water=None)
    known = must_haves.facts(welsh, premium_unlocked=True)
    result = must_haves.evaluate({"flood": "2", "surface_water": "Low risk", "epc": "C"}, known)
    rows = {row["key"]: row for row in result["rows"]}
    assert rows["flood"]["met"] is None and rows["flood"]["why"] == "Not mapped for Wales"
    assert rows["surface_water"]["why"] == "Not mapped for Wales"
    assert result["met"] == 1 and result["unknown"] == 2
    assert result["line"] == "1 of 3 met, 2 not yet known"

    # The overflow returns are not read outside England, and a report
    # without a house number has no one home's band or tenure to test.
    gap = _f7_context(england_only_gap="Scotland", house_number="")
    known = must_haves.facts(gap, premium_unlocked=True)
    rows = {r["key"]: r for r in must_haves.evaluate(
        {"sewage": "24", "epc": "C", "tenure": "freehold"}, known)["rows"]}
    assert rows["sewage"]["why"] == "Not covered in Scotland"
    assert rows["epc"]["why"] == "Needs a house number"
    assert rows["tenure"]["why"] == "Needs a house number"
    assert all(row["met"] is None for row in rows.values())

    # A service that failed is not an answer either.
    broken = _f7_context(broadband=None, clay_risk=None, bus_service=None)
    known = must_haves.facts(broken, premium_unlocked=True)
    rows = {r["key"]: r for r in must_haves.evaluate(
        {"broadband": "Superfast", "subsidence": "Possible", "buses": "2"}, known)["rows"]}
    assert all(row["met"] is None for row in rows.values())
    assert all(row["why"] == "Not answered on this report" for row in rows.values())


def test_f7_a_locked_condition_says_where_it_opens_and_nothing_else(client, fake_report):
    from app import auth, db, must_haves

    fake_report(gather=_f7_gather())
    # Signed out: the four locked checks carry no value, no words and no
    # gap into the page, only that they are locked.
    body = _f7_page(client)
    data = _f7_data(body)
    for key in ("sewage", "subsidence", "buses", "school"):
        assert data["facts"][key] == {"locked": True, "known": False, "value": None,
                                      "text": "", "gap": None}
    assert data["facts"]["flood"]["text"] == "Zone 2 (medium probability)"
    # Nothing a locked card found reaches the page through this tool.
    assert "260" not in json.dumps(data) and F7_SCHOOL not in json.dumps(data)
    assert data["locked_label"] == "Opens with your free full report"

    email = "f7-locked@example.com"
    _signed_in(client, email)
    uid = _f1_user_id(email)
    must_haves.save(uid, {"flood": "2", "sewage": "24", "school": F7_SCHOOL})
    panel = _f7_panel(_f7_page(client))
    assert "Opens with your free full report" in panel
    # The reader's own words are theirs; what the check found is not.
    assert panel.count("Opens with your free full report") == 2
    assert panel.count(">Not yet known</span>") == 2
    assert "260" not in panel
    assert "1 of 3 met, 2 not yet known" in panel

    # An account with its free report spent on another home is told the
    # other way round.
    from app.models import PremiumUnlock
    with db.get_session() as session:
        session.add(PremiumUnlock(user_id=uid, postcode="LS1 4DY", house_number=""))
        session.commit()
    assert "Opens with Premium" in _f7_panel(_f7_page(client))

    # Opened in full, the same two conditions are answered.
    with db.get_session() as session:
        user = auth.find_user_by_email(session, email)
        user.is_premium, user.plan = True, "monthly"
        session.commit()
    panel = _f7_panel(_f7_page(client))
    assert "260 spill hours nearby in 2024" in panel and "Not met" in panel
    assert "2 of 3 met" in panel and "not yet known" not in panel
    assert "Opens with" not in panel


def test_f7_the_conditions_round_trip_for_a_signed_in_account(client, fake_report):
    from app import must_haves

    fake_report(gather=_f7_gather())
    email = "f7-keeper@example.com"
    _signed_in(client, email)
    uid = _f1_user_id(email)

    r = client.post("/property/must-haves", data={
        "next": "/property?postcode=M14 5TG&house_number=233",
        "flood": "2", "epc": "C", "council_tax": "2000", "tenure": "freehold",
        "surface_water": "", "broadband": "",
    }, follow_redirects=False)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert must_haves.load(uid) == {"flood": "2", "epc": "C", "council_tax": "2000", "tenure": "freehold"}

    # The panel is rendered by the server, so it is right with no script.
    body = _f7_page(client)
    panel = _f7_panel(body)
    assert "4 of 4 met" in panel
    assert "Flood zone no worse than Zone 2" in panel and "Zone 2 (medium probability)" in panel
    assert "EPC band at least Band C" in panel and "Band C" in panel
    assert "Tenure on the last recorded sale Freehold" in panel
    # And the editor opens on what was saved.
    form = body.split('id="must-have-form"', 1)[1].split("</form>", 1)[0]
    assert '<option value="2" selected>Zone 2</option>' in form
    assert '<option value="C" selected>Band C</option>' in form

    # Saving again replaces the set; an empty form clears it.
    client.post("/property/must-haves", data={"flood": "3"})
    assert must_haves.load(uid) == {"flood": "3"}
    client.post("/property/must-haves", data={"next": "/watchlist"})
    assert must_haves.load(uid) == {}
    assert 'id="must-have-panel" hidden' in _f7_page(client)


def test_f7_the_save_refuses_the_signed_out_and_anything_it_does_not_offer(client, fake_report):
    from app import must_haves

    fake_report(gather=_f7_gather())
    r = client.post("/property/must-haves", data={"flood": "2"})
    assert r.status_code == 401 and r.json()["error"] == "sign_in"

    email = "f7-rules@example.com"
    _signed_in(client, email)
    uid = _f1_user_id(email)
    # A key the page does not offer, and a threshold it does not list,
    # are dropped rather than stored.
    r = client.post("/property/must-haves", data={
        "flood": "9", "epc": "C", "nice_area": "very", "school": "x" * 200})
    assert r.json()["conditions"] == {"epc": "C"}
    assert must_haves.load(uid) == {"epc": "C"}
    # Six at most, in the page's own order.
    r = client.post("/property/must-haves", data={
        "flood": "2", "surface_water": "Low risk", "epc": "C", "broadband": "Superfast",
        "council_tax": "2000", "tenure": "freehold", "sewage": "24", "buses": "2"})
    assert len(r.json()["conditions"]) == must_haves.MAX_CONDITIONS
    assert list(r.json()["conditions"]) == ["flood", "surface_water", "epc", "broadband",
                                            "council_tax", "tenure"]
    # Another site cannot post here as the reader, and the body is capped.
    assert client.post("/property/must-haves", data={"flood": "2"},
                       headers={"Origin": "https://example.org"}).status_code == 403
    assert client.post("/property/must-haves", content=b"x" * 9000,
                       headers={"Content-Type": "application/x-www-form-urlencoded"}).status_code == 413
    assert client.post("/property/must-haves", json={"flood": "2"}).status_code == 415


def test_f7_my_properties_counts_and_orders_by_must_haves_met(client, fake_report, monkeypatch):
    from app import must_haves, watchlist

    summaries = {
        "241": {"flood_zone": "Zone 1 (low probability)", "energy_band": "B", "band_d": 1800.0,
                "tenure": "freehold",
                "school_verdicts": {"likely": [F7_SCHOOL], "counts": {}, "total": 1}},
        "243": {"flood_zone": "Zone 3 (high probability)", "energy_band": "E", "band_d": 2400.0},
    }

    async def _summary(postcode, house_number):
        return {"postcode": postcode, "house_number": house_number, "admin_district": "Manchester",
                **summaries.get(house_number, {})}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    monkeypatch.setattr(app_main, "_all_unlocked", lambda user_id, items: True)
    fake_report(gather=_f7_gather())
    email = "f7-mine@example.com"
    _signed_in(client, email)
    for house in ("241", "243"):
        assert client.post("/watchlist/save", data={"postcode": "m145tg", "house_number": house},
                           follow_redirects=False).status_code == 303
    uid = _f1_user_id(email)
    assert len(watchlist.list_items(uid)) == 2

    # With nothing set, the page says where must-haves are set and shows
    # no counts at all.
    mine = _flat(client.get("/watchlist").text)
    assert "Set your own must-haves on any report, under the score" in mine
    assert "Must-haves:" not in mine

    must_haves.save(uid, {"flood": "2", "epc": "C", "council_tax": "2000",
                          "tenure": "freehold", "sewage": "24"})
    mine = _flat(client.get("/watchlist").text)
    # 241 meets the four the snapshot answers; the sewage condition is on
    # a locked check, so it is not answered here and says where it opens.
    assert "Must-haves: <strong>4 of 5 met, 1 not yet known</strong>" in mine
    assert "Must-haves: <strong>0 of 5 met, 2 not yet known</strong>" in mine
    assert ("Spill hours at the nearest storm overflow no more than 24 hours a year: "
            "Opens with Premium") in mine
    # 243 has no recorded tenure in its snapshot: not a miss, not known.
    assert "Tenure on the last recorded sale Freehold: Not yet known" in mine

    # Ordered by must-haves met, the home that meets more leads. Both
    # orders are plain links, so the page works with no script.
    assert '<a href="/watchlist?sort=must-haves"' in mine
    cards = r'class="myprops-address" href="/property\?postcode=m145tg&(?:amp;)?house_number=(\d+)"'
    assert re.findall(cards, mine) == ["243", "241"], "newest saved first, as it always was"
    ordered = _flat(client.get("/watchlist?sort=must-haves").text)
    assert re.findall(cards, ordered) == ["241", "243"]

    # With every home open in full, the locked condition is no longer an
    # offer: it is one this page does not answer, and the report does.
    from app import auth, db
    with db.get_session() as session:
        user = auth.find_user_by_email(session, email)
        user.is_premium, user.plan = True, "monthly"
        session.commit()
    mine = _flat(client.get("/watchlist").text)
    assert "Opens with Premium" not in mine
    assert ("Spill hours at the nearest storm overflow no more than 24 hours a year: "
            "Not yet known") in mine


def test_f7_a_named_school_reads_published_figures_only():
    from app import must_haves

    known = must_haves.facts(_f7_context(), premium_unlocked=True)
    # The estimated school is not in the fact, so a condition naming it
    # is not answered rather than answered from a model.
    rows = {r["key"]: r for r in must_haves.evaluate(
        {"school": "Meadowbank Primary School"}, known)["rows"]}
    assert rows["school"]["met"] is None
    # The published one is, and a home too far from it is a miss.
    far = _f7_context()
    far["school_landscape"]["all_schools"][0]["distance_m"] = 4000
    far["school_verdicts"] = app_main._school_verdict_summary(far["school_landscape"])
    known_far = must_haves.facts(far, premium_unlocked=True)
    assert must_haves.evaluate({"school": F7_SCHOOL}, known_far)["rows"][0]["met"] is False


def test_f7_the_servers_rules_are_the_scripts():
    source = (ROOT / "app" / "templates" / "property.html").read_text(encoding="utf-8").replace("\r\n", "\n")
    script = source.split('<script type="application/json" id="must-have-data">', 1)[1]
    script = script.split("</script>", 2)[1]
    # The same five tests, in the same order as must_haves._met.
    for line in ("if (def.test === 'worse')", "if (def.test === 'at_most')",
                 "if (def.test === 'at_least')", "if (def.test === 'is')",
                 "if (def.test === 'school')"):
        assert line in script, line
    # An unknown fact is never turned into a miss, on either side.
    assert "if (!fact || !fact.known) return null;" in script
    assert ("return metCount + ' of ' + total + ' met' + "
            "(unknown ? ', ' + unknown + ' not yet known' : '');") in script
    # Every storage call is wrapped, and nothing a reader types leaves the
    # device except to this site's own form.
    assert script.count("localStorage.") == 2
    assert "function readDevice() {\n        try {" in script
    assert "function writeDevice(conditions) {\n        try { localStorage.setItem" in script
    assert "fetch(form.action, {" in script
    assert not re.search(r"https?://", script)
    # The rows are built as text, never as markup.
    assert "innerHTML" not in script


def test_f7_the_panel_is_styled_from_tokens_and_the_lamp_needs_no_rule():
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8").replace("\r\n", "\n")
    block = css.split("/* Your own must-haves, on the report and on My properties (18 Sep 2026", 1)[1]
    block = block.split("/* Print: ink on paper", 1)[0]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", block), "a colour outside the tokens"
    # Every row and every control takes a 44 px tap.
    for rule in (".musthave-open {", ".musthave-pick select,\n.musthave-pick input {",
                 ".musthave-save {"):
        assert re.search(re.escape(rule) + r"[^}]*min-height: 2\.75rem;", block, re.S), rule
    # A phone puts each condition's control under its words.
    assert ".musthave-pick { grid-template-columns: minmax(0, 1fr); }" in block
    assert not re.search(r"theme-dark[^{]*musthave", css)


# ==== F8. Four smaller tools ==============================================
# A crime layer on the report's map, one homepage box that takes a district,
# a town, a council or a school, age chips and a postcode box on a council's
# private schools page, and a district comparison that stops printing the
# council's own figures twice. Each tool is pinned here for what the server
# renders, answers and refuses; the clicking itself is checked in a browser.


# ---- F8 (1). The crime layer ---------------------------------------------

def _f8_records(n, lat=53.45, lon=-2.22):
    """n Police.uk street-level records, walking away from the point, in
    a repeating spread of categories."""
    cats = ["burglary", "vehicle-crime", "anti-social-behaviour", "violent-crime", "shoplifting"]
    return [{"category": cats[i % len(cats)], "month": "2026-07",
             "location": {"latitude": str(lat + 0.0001 * i), "longitude": str(lon)}}
            for i in range(n)]


def test_f8_the_crime_summary_keeps_the_nearest_points_with_their_category():
    from app.services import crime

    records = _f8_records(crime.MAX_POINTS + 40)
    # Police.uk withholds the point on some records; a missing point is
    # not a point at (0, 0), so it is left out rather than drawn.
    records.append({"category": "burglary", "month": "2026-07", "location": {}})
    records.append({"category": "drugs", "month": "2026-07",
                    "location": {"latitude": "53.4501", "longitude": "-2.2201"}})
    points, capped = crime._points(records, 53.45, -2.22)

    assert len(points) == crime.MAX_POINTS and capped is True
    # Nearest the address first, so the cap keeps the points that answer
    # "near this home" rather than the first 500 the force listed.
    distances = [abs(p["lat"] - 53.45) for p in points]
    assert distances == sorted(distances)
    assert points[0]["category"] == "burglary" and points[0]["chip"] == "burglary"
    # Every point carries the category in the words by_category uses.
    assert {p["category"] for p in points} <= {
        "burglary", "vehicle crime", "anti social behaviour", "violent crime", "shoplifting", "drugs"}
    # Five chips, and anything the list does not name is "other".
    assert [key for key, _, _ in crime.CHIPS] == ["burglary", "vehicle", "asb", "violent", "other"]
    assert crime.chip_for("vehicle-crime") == "vehicle"
    assert crime.chip_for("anti-social-behaviour") == "asb"
    # Each chip is one Police.uk category and not a grouping of our own:
    # a bicycle theft is not filed under vehicle crime there, so it is
    # not filed under it here.
    assert crime.chip_for("bicycle-theft") == "other"
    assert crime.chip_for("possession-of-weapons") == "other"
    assert crime.chip_for("shoplifting") == "other"
    assert crime.chip_for("") == "other"
    assert crime.chip_for("violence-and-sexual-offences") == "violent"

    # Under the cap nothing is dropped and nothing claims to have been.
    few, capped_few = crime._points(_f8_records(3), 53.45, -2.22)
    assert len(few) == 3 and capped_few is False


def test_f8_a_month_with_no_count_carries_no_points_either():
    from app.services import crime

    gap = crime.gap_summary(crime.coverage_gap("Salford", "England"))
    assert gap["total"] is None and gap["points"] == [] and gap["points_capped"] is False
    # The area guides keep their payload in Postgres for 2,943 districts:
    # the points are the report's, and are dropped before one is stored.
    full = {"total": 9, "month": "2026-07", "by_category": [],
            "points": [{"lat": 1, "lon": 2}], "points_capped": True}
    slim = crime.without_points(full)
    assert slim["points"] == [] and slim["points_capped"] is False and slim["total"] == 9
    assert full["points"], "the summary handed in is left alone"
    assert crime.without_points(None) is None


def _f8_crime(points=None, total=120):
    return {"total": total, "month": "2026-07", "by_category": [{"category": "burglary", "count": 2}],
            "points": points if points is not None else [
                {"lat": 53.4501, "lon": -2.2201, "category": "burglary", "chip": "burglary"},
                {"lat": 53.4502, "lon": -2.2202, "category": "vehicle crime", "chip": "vehicle"},
                {"lat": 53.4503, "lon": -2.2203, "category": "shoplifting", "chip": "other"},
            ], "points_capped": False}


def _f8_report(client, fake_report, crime_summary=None, **params):
    from tests.conftest import fake_gather
    fake_report(gather=fake_gather(crime=crime_summary or _f8_crime()))
    r = client.get("/property", params={"postcode": "M14 5TG", "house_number": "88", **params})
    assert r.status_code == 200
    return r.text.replace("\r\n", "\n")


def test_f8_the_report_map_offers_the_months_points_by_category(client, fake_report):
    from app.services import crime

    body = _f8_report(client, fake_report)
    block = body.split('<div class="crime-layer" id="crime-layer" hidden>', 1)[1].split("</p>", 1)[0]
    # A toggle and five chips, every one a real checkbox a keyboard works.
    assert '<input type="checkbox" id="crime-layer-on">' in block
    assert "Show recorded crime, July 2026" in block
    for _, label, _ in crime.CHIPS:
        assert "<span>" + label + "</span>" in block
    assert block.count('<input type="checkbox" name="crime-chip"') == len(crime.CHIPS)
    # Police.uk's own caveat travels with the layer, and its source is named.
    assert crime.POINT_CAVEAT in block
    assert "Source: Police.uk street-level crime." in block
    # The points reach the script with their category and their chip.
    script = body.split("window.__setupCrimeLayer = function (makeGroup) {", 1)[1].split("</script>", 1)[0]
    assert '"category": "vehicle crime", "chip": "vehicle"' in script
    # Nothing typed leaves the device: the points were rendered here.
    assert not re.search(r"https?://", script)
    # Dev renders the Leaflet branch, and it draws them.
    assert "L.circleMarker([p.lat, p.lon], {" in body


def test_f8_the_google_branch_draws_the_same_points(client, fake_report, monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-key")
    body = _f8_report(client, fake_report)
    assert "window.__setupCrimeLayer(function (chipKey, chipPoints) {" in body
    assert "center: { lat: p.lat, lng: p.lon }, radius: 40, map: null," in body
    # The layer never moves the view off the address.
    assert "bounds.extend({ lat: p.lat" not in body


def test_f8_the_cap_is_said_out_loud_and_a_month_with_no_points_shows_no_layer(client, fake_report):
    from app.services import crime

    capped = _f8_crime(total=4728)
    capped["points_capped"] = True
    body = _f8_report(client, fake_report, crime_summary=capped)
    assert ("The " + str(crime.MAX_POINTS) + " points nearest this postcode are drawn, "
            "of 4,728 recorded within about a mile.") in body

    # Nothing to draw: no toggle, no chips, and the count still stands.
    body = _f8_report(client, fake_report, crime_summary=_f8_crime(points=[]))
    assert 'id="crime-layer"' not in body
    assert "120 within about a mile" in body


def test_f8_the_four_tools_are_styled_from_tokens_and_the_lamp_needs_no_rule():
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8").replace("\r\n", "\n")
    block = css.split("/* Four smaller tools (18 Sep 2026, first-visitor audit F8)", 1)[1]
    block = block.split("/* Print: ink on paper", 1)[0]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", block), "a colour outside the tokens"
    for rule in (".crime-layer-toggle {", ".crime-chip {", ".age-chip {", ".hero-suggest-item {"):
        assert re.search(re.escape(rule) + r"[^}]*min-height: 2\.75rem;", block, re.S), rule
    for name in ("crime-layer", "crime-chip", "hero-suggest", "age-chip", "compare-shared"):
        assert not re.search(r"theme-dark[^{]*" + re.escape(name), css), name


# ---- F8 (2). One search box ----------------------------------------------

def _f8_suggest(client, q, schools=None, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(app_main.schools_db, "search_admission_schools",
                            lambda text, limit=25: list(schools or []))
    r = client.get("/api/search-suggest", params={"q": q})
    assert r.status_code == 200
    return {group["label"]: group["items"] for group in r.json()["groups"]}


def test_f8_the_homepage_box_suggests_a_district_a_council_and_a_school(client, monkeypatch):
    school = {"urn": 100001, "name": "Fallowfield Primary School", "authority": "Manchester",
              "url": "/school/100001/fallowfield-primary-school", "has_page": True,
              "phase": "Primary", "miles": 0.4, "academic_year": "2024/25"}

    # A half-typed postcode district: its own guide first.
    groups = _f8_suggest(client, "M14", schools=[], monkeypatch=monkeypatch)
    assert groups["Area guide"][0] == {"label": "M14", "note": "Manchester", "url": "/area/M14"}

    # A town or council name: the districts inside it, and its council tax page.
    groups = _f8_suggest(client, "Leeds", schools=[], monkeypatch=monkeypatch)
    assert groups["Area guide"], "a council name finds the districts in it"
    assert all(item["note"] == "Leeds" for item in groups["Area guide"])
    assert all(item["url"] == "/area/" + item["label"] for item in groups["Area guide"])
    council = groups["Council tax"][0]
    assert council["label"] == "Leeds" and council["url"] == "/running-costs/council-tax/leeds"
    assert council["note"].startswith("Band D " + chr(163))

    # A school name, from the endpoint the admissions typeahead already uses.
    groups = _f8_suggest(client, "Fallowfield", schools=[school], monkeypatch=monkeypatch)
    assert groups["School"] == [{"label": "Fallowfield Primary School", "note": "Manchester",
                                 "url": "/school/100001/fallowfield-primary-school"}]

    # Nothing is offered before two characters, and what is typed is capped.
    assert client.get("/api/search-suggest", params={"q": "M"}).json()["groups"] == []
    assert app_main.SEARCH_SUGGEST_MAX_Q == 60
    long_q = client.get("/api/search-suggest", params={"q": "z" * 400}).json()
    assert len(long_q["q"]) == app_main.SEARCH_SUGGEST_MAX_Q
    assert [group["label"] for group in long_q["groups"]] == ["School"]  # the fake answers anything


def test_f8_each_group_is_capped_and_a_district_leads_its_own_list(client, monkeypatch):
    monkeypatch.setattr(app_main.schools_db, "search_admission_schools", lambda text, limit=25: [])
    areas = _f8_suggest(client, "M1")["Area guide"]
    assert len(areas) == app_main.SEARCH_SUGGEST_PER_GROUP
    assert areas[0]["label"] == "M1"
    assert [item["label"] for item in areas[1:]] == sorted(item["label"] for item in areas[1:])


def test_f8_the_hero_box_is_a_combobox_the_keyboard_walks_and_the_form_is_unchanged():
    source = (ROOT / "app" / "templates" / "index.html").read_text(encoding="utf-8").replace("\r\n", "\n")
    # The form still posts a postcode to /property, so with no script the
    # box behaves exactly as it did.
    assert '<form class="lx-hero-form search-form" action="/property" method="get">' in source
    # "town" went on 21 Sep 2026: the data matches council names, not towns.
    assert '<label for="postcode">Postcode, district, council or school</label>' in source
    assert 'role="combobox" aria-expanded="false" aria-autocomplete="list" aria-controls="hero-suggest"' in source
    assert '<ul class="hero-suggest" id="hero-suggest" role="listbox" aria-label="Suggestions" hidden></ul>' in source

    script = source.split("var input = document.getElementById('postcode');", 1)[1].split("</script>", 1)[0]
    for key in ("'Escape'", "'ArrowDown'", "'ArrowUp'", "'Enter'"):
        assert key in script, key
    assert "input.setAttribute('aria-activedescendant', options[active].id);" in script
    # Rows are built as text, never as markup, and only this site is asked.
    assert "innerHTML" not in script
    assert "fetch('/api/search-suggest?q=' + encodeURIComponent(q)" in script
    assert not re.search(r"https?://", script)
    # A slow answer to an earlier keystroke never overwrites a newer one.
    assert "if (!data || mine !== asked || input.value.trim() !== q) return;" in script


# ---- F8 (3). Private schools by age --------------------------------------

def _f8_district():
    whitworth = {"name": "Whitworth House School", "website": "", "town": "Stackford", "postcode": "M1 3CC",
                 "age_low": 3, "age_high": 18, "gender": "Girls", "religious_character": "None",
                 "number_on_roll": 300, "occupancy_pct": 75}
    senior = {"name": "Rowan Hall", "website": "", "town": "Stackford", "postcode": "M1 3CD",
              "age_low": 11, "age_high": 16, "gender": "Mixed", "religious_character": "None",
              "number_on_roll": 420, "occupancy_pct": 90}
    unstated = {"name": "Beech Tutorial College", "website": "", "town": "Stackford", "postcode": "M1 3CE",
                "age_low": None, "age_high": None, "gender": "", "religious_character": "None",
                "number_on_roll": None, "occupancy_pct": None}
    return {"name": "Stackford", "slug": "stackford", "count": 3, "mainstream": 3, "special": 0,
            "single_sex": 1, "with_sixth_form": 1, "pupils": 720,
            "groups": [("Mainstream schools", "By their registered age ranges.", [whitworth, senior, unstated])]}


def test_f8_every_private_school_row_carries_its_registered_age_range(client, monkeypatch):
    from tests.test_audit_fixes_17sep import _d2_page

    body = _d2_page(client, monkeypatch, "/schools/independent/stackford", "independent_district", _f8_district())
    found = re.findall(r"<tr([^>]*)>\s*<td data-value=\"([^\"]+)\"", body)
    ages = {name: attrs for attrs, name in found}
    assert ages["Whitworth House School"] == ' data-age-low="3" data-age-high="18"'
    assert ages["Rowan Hall"] == ' data-age-low="11" data-age-high="16"'
    # A school the register does not date carries neither, and the chips
    # leave it showing rather than answering for it.
    assert ages["Beech Tutorial College"] == ""

    # Three ages and a way back, all real buttons a keyboard reaches.
    chips = body.split('<div class="age-chips" id="age-chips" hidden', 1)[1].split("</div>", 1)[0]
    assert re.findall(r'data-age="([^"]*)"', chips) == ["", "4", "11", "16"]
    assert chips.count('type="button"') == 4
    # The chips carry no heading of their own: the stage headings under
    # them are the page's own structure and stay first.
    assert re.findall(r"<h2>(.*?)</h2>", body)[0] == "Mainstream schools"

    # The postcode box goes to the schools guide, fee-paying only.
    form = body.split('<form action="/schools/guide" method="get"', 1)[1].split("</form>", 1)[0]
    assert '<input type="hidden" name="only" value="fee">' in form
    assert 'name="q"' in form

    script = body.split("var chipBox = document.getElementById('age-chips');", 1)[1].split("</script>", 1)[0]
    assert "var keep = !age || !stated || (parseInt(low, 10) <= age && parseInt(high, 10) >= age);" in script
    assert "innerHTML" not in script


def test_f8_the_schools_guide_can_be_read_as_fee_paying_only(client, monkeypatch):
    from tests.test_ai_search_readiness import _forget_html

    landscape = {"total_schools": 2, "good_or_better_pct": 50, "radius_miles": 3, "by_rating": [],
                 "all_schools": [
                     {"urn": 900001, "name": "Stackford High", "phase_group": "Secondary", "type": "Academy",
                      "independent": False, "latitude": 53.45, "longitude": -2.22, "distance_m": 400,
                      "ofsted_rating": 2, "ofsted_rating_label": "Good"},
                     {"urn": 900002, "name": "Whitworth House School", "phase_group": "Secondary",
                      "type": "Other independent school", "independent": True,
                      "latitude": 53.46, "longitude": -2.23, "distance_m": 900,
                      "ofsted_rating": None, "ofsted_rating_label": None},
                 ]}

    async def _resolve(q):
        return {"latitude": 53.45, "longitude": -2.22, "label": "M14", "kind": "outcode"}

    monkeypatch.setattr(app_main.place_search, "resolve", _resolve)
    monkeypatch.setattr(app_main.schools_db, "school_landscape", lambda lat, lon: landscape)
    monkeypatch.setattr(app_main.schools_db, "national_baseline", lambda: {"good_or_better_pct": 90})

    _forget_html()
    both = client.get("/schools/guide", params={"q": "M14"}).text
    assert "Stackford High" in both and "Whitworth House School" in both
    assert "Fee-paying schools only" not in both

    _forget_html()
    fee = client.get("/schools/guide", params={"q": "M14", "only": "fee"}).text
    assert "Whitworth House School" in fee and "Stackford High" not in fee
    assert "Fee-paying schools only: 1 of them near M14" in fee
    assert 'href="/schools/guide?q=M14"' in fee


# ---- F8 (4). Two districts in one council --------------------------------

F8_COUNCIL_PAYLOAD = {
    "has_data": True,
    "local_sales": {"enough_for_median": True, "median": 412500, "count": 63,
                    "low": 150000, "high": 900000},
    "hpi": {"local_authority": {"name": "Leeds", "annual_change_pct": 5.9,
                                "average_price": 250000, "period": "2026-06"}},
    "landscape": {"good_or_better_pct": 91, "total_schools": 85, "radius_miles": 3},
    "flood_zone": {"zone": 1, "label": "Zone 1 (low probability)"},
    "finance": {"name": "Leeds", "latest_label": "2026-27",
                "history": [{"label": "2025-26", "band_d": 2180.0, "rise": 4.8},
                            {"label": "2026-27", "band_d": 2284.0, "rise": 4.8}]},
    "crime": {"total": 230, "month": "2026-07", "by_category": []},
    "deprivation": {"imd_decile": 3, "la_name": "Leeds"},
    "bus": {"best": {"name": "Headingley Lane", "weekday_day_per_hour": 12.0}},
    "health": {"nearest": {"patients_per_qualified_gp": 2100}},
    "census_change": {"rows": [{"key": "private_rented", "in_2021": 46.2}]},
}


def _f8_other_payload():
    other = json.loads(json.dumps(F8_COUNCIL_PAYLOAD))
    other["deprivation"]["imd_decile"] = 8
    other["bus"]["best"] = {"name": "Roundhay Road", "weekday_day_per_hour": 7.0}
    other["health"]["nearest"]["patients_per_qualified_gp"] = 1750
    other["census_change"]["rows"] = [{"key": "private_rented", "in_2021": 18.4}]
    other["local_sales"]["median"] = 310000
    return other


def _f8_compare(client, monkeypatch, there_council="Leeds"):
    from tests.test_ai_search_readiness import _forget_html

    async def _resolve(outcode):
        location = fake_location(postcode=outcode + " 2AA", outcode=outcode)
        location["admin_district"] = "Leeds" if outcode == "LS6" else there_council
        return location, True

    async def _build(outcode, location, key):
        return dict(F8_COUNCIL_PAYLOAD if outcode == "LS6" else _f8_other_payload())

    real_get = app_main._cache.get_persistent

    def _get(key, ttl):
        if isinstance(key, tuple) and key and key[0] == "area_guide":
            return None
        return real_get(key, ttl)

    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    monkeypatch.setattr(app_main, "_build_area_payload", _build)
    monkeypatch.setattr(app_main._cache, "get_persistent", _get)
    # The census table this reads is not seeded in the test database, so
    # the one figure that is not in the payload is handed in here.
    monkeypatch.setattr(app_main, "_dominant_home_type",
                        lambda lsoa: "Terraced, 54.0%" if lsoa else None)
    _forget_html()
    body = client.get("/area/LS6?compare=LS8").text
    _forget_html()
    return body


def _f8_rows(body):
    table = body.split('id="area-compare-table"', 1)[1].split("</table>", 1)[0]
    return [(re.sub(r"<[^>]+>", " ", label).split("  ")[0].strip(), here, there)
            for label, here, there in re.findall(
                r'<tr data-differs="\d">(.*?)</th><td>(.*?)</td><td>(.*?)</td>', table, re.S)]


def test_f8_two_districts_in_one_council_say_the_councils_figures_once(client, monkeypatch):
    body = _f8_compare(client, monkeypatch)
    shared = _flat(body.split('<p class="compare-shared">', 1)[1].split("</p>", 1)[0])
    assert shared.startswith("Both in Leeds:")
    # Labels as written, and the council named once in the sentence rather
    # than again inside every figure (21 Sep 2026, batch F review).
    assert "Prices on a year ago +5.9% (June 2026)" in shared
    assert "Band D council tax " + chr(163) + "2,284 a year (2026-27)" in shared
    assert "(Leeds," not in shared
    assert "Set by the council, not by the district: UK House Price Index, MHCLG." in shared

    # And never again in the table.
    rows = {label: (here, there) for label, here, there in _f8_rows(body)}
    assert "Prices on a year ago" not in rows and "Band D council tax" not in rows

    # The district figures the guide already holds join it, and differ.
    assert rows["Deprivation decile at the centre"] == ("3 of 10", "8 of 10")
    assert rows["Buses an hour at the best stop"] == ("12 at Headingley Lane", "7 at Roundhay Road")
    assert rows["Patients per fully qualified GP"] == ("2,100", "1,750")
    assert rows["Households renting privately"] == ("46.2%", "18.4%")
    assert rows["Most common home type"] == ("Terraced, 54.0%", "Terraced, 54.0%")


def test_f8_the_comparison_can_be_narrowed_to_the_checks_that_differ(client, monkeypatch):
    body = _f8_compare(client, monkeypatch)
    table = body.split('id="area-compare-table"', 1)[1].split("</table>", 1)[0]
    flags = re.findall(r'<tr data-differs="(\d)"', table)
    counted = re.search(r"differ \((\d+) of (\d+)\)", body)
    assert flags.count("1") == int(counted.group(1))
    assert len(flags) == int(counted.group(2))
    # Every row is on the page for a reader with no script; the toggle
    # only hides, and is itself hidden until the script shows it.
    assert '<label class="map-toggle" id="area-differ-toggle" hidden>' in body
    assert "row.hidden = box.checked && row.getAttribute('data-differs') === '0';" in body
    # Home type is the same in both, so it is not called a difference.
    marked = dict((label, differs) for (label, _, _), differs in zip(_f8_rows(body), flags))
    assert marked["Most common home type"] == "0"
    assert marked["Deprivation decile at the centre"] == "1"


def test_f8_two_districts_in_different_councils_keep_every_row(client, monkeypatch):
    body = _f8_compare(client, monkeypatch, there_council="Bradford")
    assert 'class="compare-shared"' not in body
    labels = [label for label, _, _ in _f8_rows(body)]
    assert "Prices on a year ago" in labels and "Band D council tax" in labels
    # The district figures belong to the folded view: two councils differ
    # on the council rows above already.
    assert "Deprivation decile at the centre" not in labels
