"""The six ideas of the 26 Sep 2026 brainstorm, built the same day.

1. The report's PDF is made once per home however many ask at the same
   time, kept ten minutes, and a failure comes back to the same home and
   says so. On 25 Sep one buyer asked for the same PDF seven times in 90
   seconds, four in the same second.
2. "300 sales within a short walk" was the Land Registry query's LIMIT
   300. A list that reaches the ceiling says "the 300 most recent" and
   the month of the oldest.
3. The full comparison fills the column of a home the account has open
   and reads "Opens with Premium" down the others, gathering nothing for
   a locked home.
4. /login is not a pageview, and the signup page counts only a visit a
   person touched.
5. /premium and /browser-extension keep their anonymous copy an hour.
6. "Flat 18 Park Lane Central" reads "Flat 18, Park Lane Central", as
   its neighbours in the same block do.
"""
import asyncio
import html
import re
import time

from sqlalchemy import func, select

from app import main as app_main
from app.services import _cache, pdf_export
from tests.test_audit_fixes_17sep import _pdf_route_fakes
from tests.test_leaks_closed_21sep import _save, _unlock
from tests.test_pages import _signed_in


def _subscribe(email):
    from app import auth, db
    with db.get_session() as session:
        user = auth.find_user_by_email(session, email)
        user.is_premium, user.plan = True, "monthly"
        session.commit()


# ---- 1. The PDF ----------------------------------------------------------

def test_one_pdf_is_made_once_and_kept(client, monkeypatch):
    _signed_in(client, "ideas26-pdf@example.com")
    _subscribe("ideas26-pdf@example.com")
    seen = _pdf_route_fakes(monkeypatch)
    for _ in range(3):
        r = client.get("/property/pdf?postcode=M20+2AA&house_number=7", follow_redirects=False)
        assert r.status_code == 200 and r.content == b"%PDF-1.4 stand-in"
    assert len(seen["documents"]) == 1, "a repeat ask within ten minutes renders again"
    # Another home is its own document.
    client.get("/property/pdf?postcode=M20+2AA&house_number=9", follow_redirects=False)
    assert len(seen["documents"]) == 2
    # Past the keep, it is made afresh.
    key = ("M20 2AA", "7")
    stamp, pdf = app_main._pdf_kept[key]
    app_main._pdf_kept[key] = (stamp - app_main.PDF_KEEP_S - 1, pdf)
    client.get("/property/pdf?postcode=M20+2AA&house_number=7", follow_redirects=False)
    assert len(seen["documents"]) == 3


def test_asks_at_the_same_moment_share_one_render():
    calls = []

    async def build():
        calls.append(1)
        await asyncio.sleep(0.05)
        return b"%PDF shared"

    async def many():
        return await asyncio.gather(*(app_main._pdf_once(("X1 1XX", "shared"), build) for _ in range(4)))

    assert asyncio.run(many()) == [b"%PDF shared"] * 4
    assert len(calls) == 1
    assert app_main._pdf_building == {}


def test_a_failed_pdf_comes_back_to_the_same_home_and_says_so(client, monkeypatch, fake_report):
    _signed_in(client, "ideas26-pdf-fail@example.com")
    _subscribe("ideas26-pdf-fail@example.com")
    _pdf_route_fakes(monkeypatch)
    monkeypatch.setattr(pdf_export, "html_to_pdf", lambda document: None)
    r = client.get("/property/pdf?postcode=M14+5TG&house_number=12", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/property?postcode=M14+5TG&house_number=12&pdf=failed"
    # A failure is not kept: the next ask tries again.
    assert ("M14 5TG", "12") not in app_main._pdf_kept

    fake_report()
    body = client.get(r.headers["location"]).text
    status = re.search(r'<p class="pdf-download-status"[^>]*>(.*?)</p>', body, re.S)
    assert status and "could not be made just then" in status.group(1)
    assert "hidden" not in status.group(0)
    # Without the flag the line is there, empty and hidden, for the script.
    body = client.get("/property?postcode=M14+5TG&house_number=12").text
    assert re.search(r'<p class="pdf-download-status" id="pdf-download-status" role="status" aria-live="polite" hidden></p>', body)
    assert 'id="pdf-download"' in body and "Making your PDF" in body


# ---- 2. The ceiling is not a count ------------------------------------------

def test_the_nearby_list_at_its_ceiling_says_most_recent_and_since_when():
    full = [{"date": f"20{22 + i % 4}-0{1 + i % 9}-01", "amount": 1} for i in range(app_main.NEARBY_SALES_LIMIT)]
    full[17]["date"] = "2021-03-04"
    assert app_main._nearby_sales_since(full) == "March 2021"
    assert app_main._nearby_sales_since(full[:-1]) == ""
    assert app_main._nearby_sales_since([]) == ""


def test_the_query_asks_for_the_same_ceiling_the_pages_read():
    from app.services import land_registry
    query = land_registry._NEARBY_QUERY_TEMPLATE.format(postcode_values='"M1 1AE"', limit=land_registry.NEARBY_SALES_LIMIT)
    assert f"LIMIT {app_main.NEARBY_SALES_LIMIT}" in query


def test_the_report_tile_and_the_comparables_page_word_the_ceiling():
    env = app_main.templates.env
    tile = env.from_string(
        "{% set nearby_sales_count = 300 %}{% set nearby_sales_since = 'March 2021' %}"
        "{% set nearby_latest_sale = {'amount': 258848} %}"
        + re.search(r'<span class="keep-exploring-stat">((?:(?!</span>).)*?within a short walk.*?)</span>',
                    open("app/templates/property.html", encoding="utf-8").read(), re.S).group(1)
    ).render()
    assert " ".join(tile.split()) == ("The <strong>300</strong> most recent sales within a short walk, "
                                      "since March 2021, latest <strong>£258,848</strong>")
    source = open("app/templates/comparables.html", encoding="utf-8").read()
    assert "The {{ comparables_count }} most recent sold prices recorded nearby" in source


# ---- 3. The full comparison with one home open --------------------------------

def test_the_full_comparison_fills_the_open_home_and_gathers_nothing_for_the_other(client, monkeypatch):
    monkeypatch.setattr(app_main.email_service, "can_verify", lambda: False)
    email = "ideas26-compare@example.com"
    ids = _save(client, email, ("21", "23"))
    _unlock(email, "M14 5TG", "21")
    gathered = []

    async def _rows(postcode, house_number):
        gathered.append(house_number)
        return {"postcode": "M14 5TG", "house_number": house_number, "admin_district": "Manchester",
                "rows": {("Risk and safety", "Radon"): {"group": "Risk and safety", "check": "Radon",
                                                         "result": "Lower than 1%", "status": "good", "source": "BGS"}},
                "order": [("Risk and safety", "Radon")]}

    monkeypatch.setattr(app_main, "_compare_rows", _rows)
    body = client.get(f"/watchlist/compare/full?item_ids={ids[0]}&item_ids={ids[1]}").text
    assert gathered == ["21"]
    flat = " ".join(re.sub(r"<[^>]+>", " ", html.unescape(body)).split())
    assert "Lower than 1%" in body
    assert body.count('class="cmp-locked"') == 1
    assert "21, M14 5TG is open in full, so its column is filled" in flat
    assert "The other home opens with Premium" in flat
    assert 'id="differences-only"' not in body, "one open column has nothing to differ from"
    assert 'href="/premium"' in body

    # The light comparison leads there by name.
    from tests.test_leaks_closed_21sep import _install_summaries
    _install_summaries(monkeypatch)
    light = client.get(f"/watchlist/compare?item_ids={ids[0]}&item_ids={ids[1]}").text
    assert "See every check, 21, M14 5TG in full" in light

    # With nothing open it is still the page that explains, and gathers nothing.
    gathered.clear()
    other = _save(client, "ideas26-compare-none@example.com", ("31", "33"))
    body = client.get(f"/watchlist/compare/full?item_ids={other[0]}&item_ids={other[1]}").text
    assert "Premium puts every check on the report next to each other" in body and gathered == []


# ---- 4. Counting -----------------------------------------------------------------

def _rows(path):
    from app.db import get_session
    from app.models import PageView
    with get_session() as db:
        return db.scalar(select(func.count()).select_from(PageView).where(PageView.path == path))


def test_log_in_is_not_a_pageview_and_signup_waits_for_a_person(client):
    browser = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"}
    before = _rows("/login")
    assert client.get("/login", headers=browser).status_code == 200
    assert _rows("/login") == before
    page = client.get("/signup", headers=browser).text
    script = page[page.index("/signup/seen") - 800: page.index("/signup/seen") + 400]
    assert "pointerdown" in script and "keydown" in script
    assert "sendBeacon('/signup/seen')" in script
    # The beacon sits inside the handler, not at load.
    assert re.search(r"function send\(\)\s*\{.*?sendBeacon\('/signup/seen'\)", script, re.S)


# ---- 5. An hour for the pages that sell ------------------------------------------

def test_premium_and_the_extension_page_keep_an_hour(client):
    assert app_main._ANON_HOUR_PATHS == {"/", "/premium", "/browser-extension"}
    for path in ("/premium", "/browser-extension", "/buying-guide"):
        client.get(path)
        key = ("anon_html", path, "")
        entry = _cache._store[key]
        # Aged past ten minutes, inside the hour.
        _cache._store[key] = (time.time() - 1200, *entry[1:])
        hit = client.get(path).headers.get("X-Anon-Cache")
        assert hit == ("hit" if path != "/buying-guide" else "miss"), path


# ---- 6. One block, one way of writing it ------------------------------------------

def test_a_flat_number_then_a_building_name_takes_the_comma():
    streets = {"bennett road"}
    assert app_main._epc_home_label("Flat 18 Park Lane Central, Bennett Road", streets) == "Flat 18, Park Lane Central, Bennett Road"
    assert app_main._epc_home_label("Flat 16, Park Lane Central, Bennett Road", streets) == "Flat 16, Park Lane Central, Bennett Road"
    assert app_main._epc_home_label("Flat 1a Park Lane Central, Bennett Road", streets) == "Flat 1a, Park Lane Central, Bennett Road"
    assert app_main._sale_label("FLAT 20 PARK LANE CENTRAL") == "Flat 20, Park Lane Central"
    # Unchanged: a flat and a building number, and a house with a name.
    assert app_main._sale_label("FLAT 2 12 HIGH STREET") == "Flat 2, 12 High Street"
    assert app_main._epc_home_label("Garden Flat, 34 Bennett Road", streets) == "Garden Flat, 34 Bennett Road"
    homes = app_main._postcode_homes(
        [{"address": "Flat 18 Park Lane Central, Bennett Road"}, {"address": "Flat 16, Park Lane Central, Bennett Road"}], [])
    assert [h["label"] for h in homes] == ["Flat 16, Park Lane Central, Bennett Road", "Flat 18, Park Lane Central, Bennett Road"]
