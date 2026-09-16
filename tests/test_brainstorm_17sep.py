"""The seven ideas of the 16 Sep 2026 brainstorm that Michael approved on
17 Sep (1, 2, 3, 5, 6, 7, 8): one home is one saved row, the anonymous
HTML cache holds compressed pages, crawlers leave the signup column,
a district search on the schools guide names what a full postcode adds,
the change-alert job's answer is kept, a reading from an estimate says
so, and the competitor comparison cannot go stale unnoticed."""
import re


# ---- 1. One home, one row -------------------------------------------------

def test_the_same_home_typed_two_ways_is_one_home():
    from app.watchlist import same_home, same_place

    # The four live cases of 16 Sep 2026.
    assert same_home("Flat 1, 66 London Road", "Flat 1  66 London Road")
    assert same_home("101 maryhill road", "101")
    assert same_home("101", "101 Maryhill Road")
    assert not same_home("17", "")
    assert same_place("17", "")
    # Word by word, never character by character.
    assert not same_home("1", "1A")
    assert not same_home("Flat 1", "Flat 12")
    assert not same_home("Flat 1, 66 London Road", "Flat 2, 66 London Road")
    assert not same_place("17", "23")


def test_the_offer_never_compares_a_home_with_itself():
    from app.main import _compare_offer

    user = {"is_premium": False}
    # Account 84: its only two rows were one house.
    items = [{"id": 2, "postcode": "CM5 9HH", "house_number": ""},
             {"id": 1, "postcode": "CM5 9HH", "house_number": "17"}]
    assert _compare_offer(items, "CM5 9HH", "17", user) is None

    # Account 80: a flat saved twice, and one real other home. The other
    # home is offered once, and the flat is not offered against itself.
    items = [{"id": 4, "postcode": "TN1 2DY", "house_number": "53 Upper Grosvenor Road"},
             {"id": 3, "postcode": "TN4 0PR", "house_number": "Flat 1  66 London Road"},
             {"id": 2, "postcode": "TN4 0PR", "house_number": "Flat 1, 66 London Road"}]
    offer = _compare_offer(items, "TN4 0PR", "Flat 1  66 London Road", user)
    assert offer["others"] == ["53 Upper Grosvenor Road TN1 2DY"]
    assert offer["compared_count"] == 2
    assert offer["held_back"] == 0
    assert "item_ids=3" in offer["url"] and "item_ids=4" in offer["url"]
    assert "item_ids=2" not in offer["url"]

    # Two genuinely different homes elsewhere, one saved twice.
    items = [{"id": 9, "postcode": "G20 7XN", "house_number": "101"},
             {"id": 8, "postcode": "G20 7XN", "house_number": "101 maryhill road"},
             {"id": 7, "postcode": "G20 7AL", "house_number": "84"}]
    offer = _compare_offer(items, "G20 7AL", "84", user)
    assert offer["others"] == ["101 G20 7XN"]


def test_opening_a_home_saved_with_different_spacing_adds_no_row(client):
    from app import auth, watchlist
    from app.db import get_session

    client.post("/signup", data={
        "email": "one-home@example.test", "password": "correct horse battery staple",
    }, follow_redirects=False)
    with get_session() as db:
        user_id = auth.find_user_by_email(db, "one-home@example.test").id

    watchlist.save_item(user_id, "TN4 0PR", "Flat 1, 66 London Road", "")
    assert watchlist.remember(user_id, "TN4 0PR", "Flat 1  66 London Road") is False
    assert watchlist.remember(user_id, "TN4 0PR", "flat 1 66 london road") is False
    # A note typed against the other spelling lands on the one row.
    watchlist.save_item(user_id, "TN4 0PR", "FLAT 1 66 LONDON ROAD", "offer in")
    items = watchlist.list_items(user_id)
    assert len(items) == 1 and items[0]["note"] == "offer in"
    # A different flat in the same building is a different home.
    assert watchlist.remember(user_id, "TN4 0PR", "Flat 2, 66 London Road") is True
    assert len(watchlist.list_items(user_id)) == 2


# ---- 2. The page cache holds compressed pages ------------------------------

def test_a_cached_page_is_stored_compressed_and_served_whole(client):
    from app.services import _cache

    first = client.get("/methodology")
    assert first.status_code == 200 and first.headers.get("X-Anon-Cache") == "miss"
    stored = [v for k, (_, v, _size) in _cache._store.items()
              if isinstance(k, tuple) and k[:2] == ("anon_html", "/methodology")]
    assert stored, "the page should be in the store"
    status, packed = stored[0]
    assert isinstance(packed, bytes)
    # The inlined stylesheet alone makes a page compress several times over.
    assert len(packed) * 3 < len(first.content)
    again = client.get("/methodology")
    assert again.headers.get("X-Anon-Cache") == "hit"
    assert again.text == first.text


def test_the_sitemap_is_stored_compressed_and_served_whole(client):
    from app.services import _cache

    first = client.get("/sitemap.xml").text
    stored = [v for k, (_, v, _size) in _cache._store.items() if isinstance(k, tuple) and k[0] == "sitemap"]
    assert stored and isinstance(stored[0], bytes)
    assert client.get("/sitemap.xml").text == first


# ---- 3. A crawler fetching the signup page is not a signup-page visit ------

_BROWSER = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"}


def _signup_rows():
    from sqlalchemy import func, select
    from app.db import get_session
    from app.models import PageView
    with get_session() as db:
        return db.scalar(select(func.count()).select_from(PageView).where(PageView.path == "/signup"))


def test_the_signup_page_is_counted_by_the_page_not_the_request(client):
    before = _signup_rows()
    page = client.get("/signup", headers=_BROWSER)
    assert page.status_code == 200
    assert "/signup/seen" in page.text
    assert _signup_rows() == before, "fetching the HTML alone must not count"

    assert client.post("/signup/seen", headers=_BROWSER).status_code == 204
    assert _signup_rows() == before + 1

    # Our own checks and crawlers never count, even if they post.
    client.post("/signup/seen", headers={**_BROWSER, "X-Internal-Check": "1"})
    client.post("/signup/seen", headers={"user-agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"})
    assert _signup_rows() == before + 1


# ---- 5 and 7. The schools guide: what a postcode adds, and est. readings --

def _guide_landscape(lat, lon):
    school = {"latitude": 53.45, "longitude": -2.22, "phase_group": "Primary", "type": "Community school",
              "independent": False, "ofsted_rating_label": "Good", "ofsted_rating": 2, "exam_results": {}}
    return {
        "radius_miles": 3, "good_or_better_pct": 90, "total_schools": 2, "by_rating": [],
        "further_education": 0, "higher_education_count": 0, "higher_education_names": [],
        "all_schools": [
            {**school, "urn": 990101, "name": "Published Primary", "distance_m": 400,
             "admission_radius": {"last_distance_miles": 1.5, "academic_year": "2025/26"}},
            {**school, "urn": 990102, "name": "Estimated Primary", "distance_m": 500,
             "catchment_estimate": {"radius_miles": 1.2}},
        ],
    }


def _patch_guide(monkeypatch, resolved):
    from app import main as app_main
    from app.services import place_search

    async def _resolve(_q):
        return resolved

    monkeypatch.setattr(place_search, "resolve", _resolve)
    monkeypatch.setattr(app_main.schools_db, "school_landscape", _guide_landscape)


def test_a_district_search_says_what_a_full_postcode_adds(client, monkeypatch):
    _patch_guide(monkeypatch, {"latitude": 53.45, "longitude": -2.22, "label": "M14", "kind": "outcode"})
    body = client.get("/schools/guide?q=M14").text
    assert "Search a full postcode and the table adds a column for that address" in body
    assert "No reading for an address here: M14 is a district" in body
    assert "Full postcode or town" in body
    assert "Check a postcode or add an area" in body
    assert "<th data-sort=\"num\">From" not in body


def test_a_postcode_search_marks_a_reading_taken_from_an_estimate(client, monkeypatch):
    _patch_guide(monkeypatch, {"latitude": 53.4501, "longitude": -2.2201, "label": "M14 5TG", "kind": "postcode"})
    body = client.get("/schools/guide?q=M14+5TG").text
    assert "From M14 5TG" in body
    assert "No reading for an address here" not in body
    assert "Add area to compare" in body
    rows = re.findall(r"<tr>.*?</tr>", body, flags=re.S)
    published = next(r for r in rows if "Published Primary" in r)
    estimated = next(r for r in rows if "Estimated Primary" in r)
    verdict_cell = lambda row: re.findall(r'<td data-value="[1-4]">(.*?)</td>', row, flags=re.S)[-1]
    assert "est." not in verdict_cell(published)
    assert "est." in verdict_cell(estimated)
    assert "measured against a modelled estimate" in verdict_cell(estimated)


def test_a_postcode_carried_into_a_comparison_keeps_its_reading():
    from app.main import _parse_areas_param

    areas = _parse_areas_param("53.45,-2.22,M14 5TG|53.4,-2.2,M14|51.75,-1.25,Oxford")
    assert [a["kind"] for a in areas] == ["postcode", "outcode", "place"]


# ---- 6. The change-alert job's answer is kept and shown ------------------

def test_each_alert_run_is_recorded_and_shown_on_admin(client, monkeypatch):
    from app import auth, main as app_main, watchlist
    from app.services import email as email_service
    from app.db import get_session

    monkeypatch.setenv("ALERTS_CRON_SECRET", "s3cret")
    monkeypatch.setenv("ADMIN_EMAIL", "alerts-boss@example.test")
    monkeypatch.setattr(email_service, "is_configured", lambda: True)

    async def _send(*_a, **_k):
        return True

    monkeypatch.setattr(email_service, "send_email", _send)
    calls = {"n": 0}

    async def _summary(postcode, house_number):
        calls["n"] += 1
        if postcode == "ZZ9 9ZZ":
            raise RuntimeError("upstream down")
        return {"postcode": postcode}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    monkeypatch.setattr(app_main, "_snapshot_changes", lambda old, new: [])

    client.post("/signup", data={"email": "alerts-boss@example.test",
                                 "password": "correct horse battery staple"}, follow_redirects=False)
    with get_session() as db:
        uid = auth.find_user_by_email(db, "alerts-boss@example.test").id
    watchlist.save_item(uid, "M1 1AE", "", "")
    watchlist.save_item(uid, "ZZ9 9ZZ", "", "")

    r = client.post("/internal/run-watchlist-alerts", headers={"x-alerts-secret": "s3cret"})
    assert r.status_code == 200
    latest = app_main._alert_runs()[0]
    assert latest["failed"] >= 1 and latest["checked"] >= 2
    assert latest["emails_sent"] == 0 and "at" in latest and "seconds" in latest

    body = client.get("/admin").text
    assert "Change alerts: what each run did" in body
    assert latest["at"].replace("T", " ") in body
