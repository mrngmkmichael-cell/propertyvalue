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
