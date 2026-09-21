"""Two leaks of a locked answer closed, and the extension's free and paid
brought up to date (21 Sep 2026).

School Catchment Areas is a locked check: which nearby schools are Likely,
Borderline or Unlikely for an address. How many of each is not locked: the
counts are the headline of the report's free Schools Nearby card, a teaser
rather than the finding. The report has withheld the schools themselves from
anyone who has not opened the home since batch B of the 17 Sep 2026 audit, but
the side-by-side comparison printed every saved home's likely schools by name
to any signed-in account, the postcode comparison did the same for anyone at
all, and the extension's public feed gave a caller with no token a school row
with its admission distance and reading. Each now gives them only with
Premium or on a home the reader has opened, and says where they open
otherwise, in the report's own words. The comparisons keep the counts in every
column.

Rental Analysis, Household Income and Costs & Affordability, free on the
site since 17 Sep 2026, are free in the extension's feed and its new build.

The extension's half waits for main.EXTENSION_240_LIVE: the published 2.3.0
has no words for a locked reading and cannot show the three free, so while
the switch is False the feed's school row is what it was before, and the
pages that describe the extension keep their old words. The three free
checks go to every caller in both states, since 2.3.0 never reads them.
Both states are tested here.
Who counts as having opened a postcode is fixed in the extension either way:
a buying pass that has run out no longer counts, and an unlock counts in any
spacing. /watchlist/compare/full reads the unlocks the way the other two
comparisons do, in one query.
"""
import datetime
import json
import pathlib
import re

from sqlalchemy import event, select

from app import main as app_main
from tests.conftest import fake_location
from tests.test_pages import _signed_in

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOCKED_FREE = "Opens with your free full report"
LOCKED_PREMIUM = "Opens with Premium"
PASSWORD = "correct-horse-battery"  # tests.test_pages._signed_in's


def _flat(text):
    return " ".join(text.split())


def _user_id(email):
    from app import db
    from app.models import User
    with db.get_session() as session:
        return session.scalar(select(User.id).where(User.email == email))


def _unlock(email, postcode, house_number):
    """The row a free full report leaves, for this account and this home."""
    from app import db
    from app.models import PremiumUnlock
    with db.get_session() as session:
        session.add(PremiumUnlock(user_id=_user_id(email), postcode=postcode, house_number=house_number))
        session.commit()


def _subscribe(email):
    from app import auth, db
    with db.get_session() as session:
        user = auth.find_user_by_email(session, email)
        user.is_premium, user.plan = True, "monthly"
        session.commit()


def _schools_cells(body):
    """Each column's cell in the "Schools likely to admit" row, flattened."""
    row = body.split("<td><strong>Schools likely to admit</strong></td>", 1)[1].split("</tr>", 1)[0]
    return [_flat(cell) for cell in re.findall(r"<td>(.*?)</td>", row, re.S)]


# ==== 1. The side-by-side comparison of saved homes ======================
# /watchlist/compare printed each saved home's likely schools by name to any
# signed-in account. The counts stay in every column, exactly as an open
# column gives them, because the report's free Schools Nearby card gives
# them too. The names show only for a subscriber or a home the account has
# opened, read from one query for the page, and every other column gives the
# report's locked words in their place. Every test saves homes with its own
# house numbers: the tests share one database.

# One home's readings for each last digit of its house number, each with its
# own schools, so a name in the wrong column is caught.
READINGS = {
    "1": {"counts": {"likely": 2, "borderline": 1, "unlikely": 1}, "total": 4,
          "likely": ["Alderbrook Primary", "Birchfield Academy"], "borderline": ["Cedarwood School"]},
    "3": {"counts": {"likely": 1, "borderline": 0, "unlikely": 2}, "total": 3,
          "likely": ["Damsonvale Primary"], "borderline": []},
    "5": {"counts": {"likely": 3, "borderline": 1, "unlikely": 0}, "total": 4,
          "likely": ["Elmhurst Primary", "Fernlea Academy", "Gorsedale School"], "borderline": ["Hazelmere Primary"]},
    # No school likely: an open column names none, so a closed one has no
    # names to replace and reads the same.
    "7": {"counts": {"likely": 0, "borderline": 2, "unlikely": 1}, "total": 3,
          "likely": [], "borderline": ["Juniper Primary", "Kingsmead School"]},
}
ALL_NAMES = [name for r in READINGS.values() for name in r["likely"] + r["borderline"]]


def _counts(reading):
    """A column's counts, as every column shows them, open or not."""
    line = f"<strong>{reading['counts']['likely']} likely</strong>"
    if reading["counts"]["borderline"]:
        line += f", {reading['counts']['borderline']} borderline"
    return f"{line} of {reading['total']} with a figure"


def _cell(reading, note=None):
    """A column's cell, flattened: the counts, and beneath them the likely
    schools' names in an open column or the locked words in a closed one,
    where there is any likely school to name."""
    return _counts(reading) + (f' <div class="compare-note">{note}</div>' if note else "")


def _install_summaries(monkeypatch):
    async def _summary(postcode, house_number):
        return {"postcode": postcode, "house_number": house_number, "admin_district": "Manchester",
                "school_verdicts": READINGS.get(house_number[-1:])}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    monkeypatch.setattr(app_main.email_service, "can_verify", lambda: False)


def _save(client, email, houses, postcode="M14 5TG"):
    from app import watchlist
    _signed_in(client, email)
    for house in houses:
        assert client.post("/watchlist/save", data={"postcode": postcode, "house_number": house},
                           follow_redirects=False).status_code == 303
    items = watchlist.list_items(_user_id(email))
    return [watchlist.find_in(items, postcode, house)["id"] for house in houses]


def _compare(client, ids):
    r = client.get("/watchlist/compare?" + "&".join(f"item_ids={i}" for i in ids))
    assert r.status_code == 200
    return r.text


def test_a_free_account_reads_the_counts_and_no_school_name_for_a_home_it_has_not_opened(client, monkeypatch):
    _install_summaries(monkeypatch)
    body = _compare(client, _save(client, "leak21-compare-new@example.com", ("711", "713", "717")))
    # The counts as an open column gives them, and the report's words where
    # the names would be: this account's free full report is still to spend.
    assert _schools_cells(body) == [
        _cell(READINGS["1"], LOCKED_FREE),
        _cell(READINGS["3"], LOCKED_FREE),
        # No likely school, so no names to replace: as an open column reads.
        _cell(READINGS["7"]),
    ]
    for name in ALL_NAMES:
        assert name not in body, name


def test_a_free_account_reads_the_names_for_the_home_it_opened_and_no_other(client, monkeypatch):
    _install_summaries(monkeypatch)
    email = "leak21-compare-opened@example.com"
    # Saved as typed on My properties, opened under the looked-up postcode:
    # one home, as My properties' "Open in full" already reads it.
    ids = _save(client, email, ("721", "723"), postcode="m145tg")
    _unlock(email, "M14 5TG", "721")
    opened, closed = _schools_cells(_compare(client, ids))
    # The open column exactly as every column read before 21 Sep 2026.
    assert opened == ('<strong>2 likely</strong>, 1 borderline of 4 with a figure '
                      '<div class="compare-note">Alderbrook Primary, Birchfield Academy</div>')
    assert opened == _cell(READINGS["1"], "Alderbrook Primary, Birchfield Academy")
    # The free full report is spent on 721, so the other home's names open
    # with Premium. Its counts are there as the open column's are.
    assert closed == _cell(READINGS["3"], LOCKED_PREMIUM)
    assert "Damsonvale Primary" not in closed


def test_a_subscriber_reads_every_homes_answer(client, monkeypatch):
    _install_summaries(monkeypatch)
    email = "leak21-compare-subscriber@example.com"
    ids = _save(client, email, ("731", "733", "735"))
    _subscribe(email)
    cells = _schools_cells(_compare(client, ids))
    assert cells == [
        _cell(READINGS["1"], "Alderbrook Primary, Birchfield Academy"),
        _cell(READINGS["3"], "Damsonvale Primary"),
        _cell(READINGS["5"], "Elmhurst Primary, Fernlea Academy, Gorsedale School"),
    ]
    assert not any("Opens with" in cell for cell in cells)


def test_the_comparison_reads_every_homes_unlock_in_one_query(client, monkeypatch):
    from app import auth, db
    _install_summaries(monkeypatch)
    email = "leak21-compare-queries@example.com"
    ids = _save(client, email, ("741", "743", "745"))
    _unlock(email, "M14 5TG", "743")

    def _per_home(*args, **kwargs):
        raise AssertionError("auth.has_unlocked once per home")

    monkeypatch.setattr(auth, "has_unlocked", _per_home)
    statements = []

    def _seen(conn, cursor, statement, parameters, context, executemany):
        # The unlock rows by address; the sign-in's own count of them
        # (auth.unlocks_used) reads no house number.
        if "premium_unlocks" in statement and "house_number" in statement:
            statements.append(statement)

    engine = db._get_engine()
    event.listen(engine, "before_cursor_execute", _seen)
    try:
        cells = _schools_cells(_compare(client, ids))
    finally:
        event.remove(engine, "before_cursor_execute", _seen)
    assert len(statements) == 1, statements
    assert cells == [
        _cell(READINGS["1"], LOCKED_PREMIUM),
        _cell(READINGS["3"], "Damsonvale Primary"),
        _cell(READINGS["5"], LOCKED_PREMIUM),
    ]


def test_the_full_comparison_and_my_properties_hold_no_school_answer_either(client, monkeypatch):
    _install_summaries(monkeypatch)
    email = "leak21-compare-elsewhere@example.com"
    ids = _save(client, email, ("751", "753"))
    _unlock(email, "M14 5TG", "751")
    gathered = []

    async def _rows(postcode, house_number):
        gathered.append((postcode, house_number))
        return {"not_found": True, "postcode": postcode, "house_number": house_number}

    monkeypatch.setattr(app_main, "_compare_rows", _rows)
    # Every check side by side waits until every home is open, as it did.
    full = client.get(f"/watchlist/compare/full?item_ids={ids[0]}&item_ids={ids[1]}").text
    assert "Premium puts every check on the report next to each other" in full
    assert gathered == []
    # And My properties never prints a home's school readings at all.
    mine = client.get("/watchlist").text
    for body in (full, mine):
        for name in ALL_NAMES:
            assert name not in body, name
        assert "with a figure" not in body


def test_the_full_comparison_opens_homes_saved_in_any_spacing_from_one_query(client, monkeypatch):
    """/watchlist/compare/full asked auth.has_unlocked once per home, on the
    postcode as saved, so homes saved as "m145tg" and opened as "M14 5TG"
    kept it locked. It reads the unlocks once, on _watchlist_unlock_key, as
    the other two comparisons do, and the page's statements do not grow with
    the number of homes."""
    from app import auth, db
    email = "leak21-full-spacing@example.com"
    houses = ("761", "763", "765", "767")
    ids = _save(client, email, houses, postcode="m145tg")
    for house in houses:
        _unlock(email, "M14 5TG", house)
    gathered = []

    async def _rows(postcode, house_number):
        gathered.append(house_number)
        return {"postcode": "M14 5TG", "house_number": house_number, "admin_district": "Manchester",
                "rows": {}, "order": []}

    def _per_home(*args, **kwargs):
        raise AssertionError("auth.has_unlocked once per home")

    monkeypatch.setattr(app_main, "_compare_rows", _rows)
    monkeypatch.setattr(auth, "has_unlocked", _per_home)
    engine = db._get_engine()

    def _visit(chosen):
        seen = []

        def _count(_conn, _cursor, statement, *_rest):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", _count)
        try:
            body = client.get("/watchlist/compare/full?" + "&".join(f"item_ids={i}" for i in chosen)).text
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        return body, seen

    _visit(ids[:1])  # anything a first visit does once is out of the way
    gathered.clear()
    two, two_seen = _visit(ids[:2])
    four, four_seen = _visit(ids)
    for body, count in ((two, 2), (four, 4)):
        assert "Premium puts every check on the report next to each other" not in body
        assert f"checks for {count} homes" in body
    assert gathered == list(houses[:2] + houses)
    assert len(four_seen) == len(two_seen), (two_seen, four_seen)
    assert sum(1 for s in four_seen if "premium_unlocks" in s and "house_number" in s) == 1


# ==== 2. The postcode comparison, open to anyone ==========================
# /compare took any two or three postcodes, signed in or not, and printed
# the same row. A home's readings come from its postcode alone, so leaving
# the names in it would have given out what the saved-homes comparison now
# keeps back. The counts stay, as they do there.

POSTCODE_READINGS = {
    "M60 7AA": {"counts": {"likely": 1, "borderline": 1, "unlikely": 0}, "total": 2,
                "likely": ["Ivydene Primary"], "borderline": ["Jasmine Hill Academy"]},
    "M60 7AB": {"counts": {"likely": 2, "borderline": 0, "unlikely": 1}, "total": 3,
                "likely": ["Kingfisher Primary", "Laurelbank School"], "borderline": []},
}


def test_the_postcode_comparison_keeps_the_names_for_whoever_opened_it(client, monkeypatch):
    async def _summary(postcode, house_number):
        code = postcode.strip().upper()
        return {"postcode": code, "house_number": "", "admin_district": "Manchester",
                "school_verdicts": POSTCODE_READINGS.get(code)}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    monkeypatch.setattr(app_main.email_service, "can_verify", lambda: False)
    url = "/compare?postcode=M60+7AA&postcode=M60+7AB"
    first_reading, second_reading = POSTCODE_READINGS["M60 7AA"], POSTCODE_READINGS["M60 7AB"]

    # Signed out: the counts, and where the names open.
    body = client.get(url).text
    assert _schools_cells(body) == [_cell(first_reading, LOCKED_FREE), _cell(second_reading, LOCKED_FREE)]
    for name in ("Ivydene Primary", "Jasmine Hill Academy", "Kingfisher Primary", "Laurelbank School"):
        assert name not in body, name

    # An account that opened one postcode's own report reads its names.
    # Postcodes of their own, as no other test uses them.
    email = "leak21-postcode-opened@example.com"
    _signed_in(client, email)
    _unlock(email, "M60 7AA", "")
    first, second = _schools_cells(client.get(url).text)
    assert first == _cell(first_reading, "Ivydene Primary")
    assert second == _cell(second_reading, LOCKED_PREMIUM)

    # A subscriber reads both.
    client.cookies.clear()
    email = "leak21-postcode-subscriber@example.com"
    _signed_in(client, email)
    _subscribe(email)
    first, second = _schools_cells(client.get(url).text)
    assert first == _cell(first_reading, "Ivydene Primary")
    assert second == _cell(second_reading, "Kingfisher Primary, Laurelbank School")


# ==== 3. The extension's public feed ======================================
# /api/extension-report gave a caller with no token one school row with its
# admission distance, whether that was published or estimated, its year and
# the Likely, Borderline or Unlikely reading. Once EXTENSION_240_LIVE is True
# a caller without Premium, or without this postcode opened on their
# account, gets name, phase, Ofsted, straight-line distance and the link to
# the school's free page, and the words for where the rest opens, and every
# caller gets the three checks the site made free on 17 Sep as free_cards.
# While it is False the feed is what 2.3.0 was built for, plus free_cards,
# which 2.3.0 never reads. The paid path is as it was in both.

LOCKED_KEYS = ("admission_miles", "admission_kind", "admission_year", "verdict")
EXT_LANDSCAPE = {
    "total_schools": 3, "good_or_better_pct": 67, "radius_miles": 3,
    "all_schools": [
        # 400 m against a published 1.2 miles: Likely.
        {"urn": 990761, "name": "Kestrel Lane Primary", "distance_m": 400, "phase_group": "Primary",
         "ofsted_rating_label": "Good", "admission_radius": {"last_distance_miles": 1.2, "academic_year": "2025"}},
        # 1,500 m against an estimated 0.8 miles: Unlikely.
        {"urn": 990762, "name": "Larkspur Academy", "distance_m": 1500, "phase_group": "Secondary",
         "ofsted_rating_label": "Outstanding", "catchment_estimate": {"radius_miles": 0.8}},
        {"urn": 990763, "name": "Merlin Road School", "distance_m": 2000, "phase_group": "Primary",
         "ofsted_rating_label": None},
    ],
}
# The nearest school's row as the feed sent it to anyone before 21 Sep 2026,
# and as it still does while the switch is off.
KESTREL_ROW = {"name": "Kestrel Lane Primary", "urn": 990761, "slug": "kestrel-lane-primary", "distance_m": 400,
               "phase": "Primary", "ofsted_rating_label": "Good", "admission_miles": 1.2,
               "admission_kind": "published", "admission_year": "2025", "verdict": "likely"}
# Every key a caller without Premium was sent before 21 Sep 2026.
HEAD_KEYS = {"postcode", "admin_district", "region", "latitude", "longitude", "report_url",
             "market_history_error", "area_level", "district", "overview", "summary", "market_history",
             "comparables", "schools", "epc", "demographics", "crime", "premium_unlocked",
             "market_history_full_count", "comparables_full_count", "schools_full_count"}
EXT_RENTAL = {"la_name": "Manchester", "period": "2026-06", "price_all": 1250, "change_all_pct": 5.2,
              "by_bedroom": []}
EXT_INCOME = {"here": 45000, "la_name": "Manchester", "la_average": 41000,
              "region_name": "North West", "region_average": 43000}


def _switch(monkeypatch, live):
    """main.EXTENSION_240_LIVE for this test. The main session flips the
    real one when the owner confirms 2.4.0 is live in the Chrome Web Store."""
    monkeypatch.setattr(app_main, "EXTENSION_240_LIVE", live)


def _ext_install(monkeypatch, postcode, landscape=EXT_LANDSCAPE):
    """Every service the feed reads, faked, for a postcode no other test
    uses, and its cached payloads forgotten. The lookup answers postcode
    whatever spacing it is asked in, as postcodes.io does."""
    from app.services import _cache
    location = fake_location(postcode=postcode, outcode=postcode.split()[0])
    location["codes"] = {**location["codes"], "msoa": "E02001234"}

    async def _resolve(_postcode):
        return location, False

    async def _sold(_postcode):
        return [{"address": "1 TEST ROAD", "postcode": postcode, "amount": "200000", "date": "2025-01-01",
                 "tenure": "Freehold"},
                {"address": "3 TEST ROAD", "postcode": postcode, "amount": "300000", "date": "2024-01-01",
                 "tenure": "Freehold"}]

    async def _comparables(lat, lon):
        return []

    async def _zone(lat, lon, country=None):
        return {"zone": 1, "label": "Zone 1 (low probability)"}

    async def _crime(lat, lon, district=None, country=None):
        return {"total": 120, "month": "2026-06", "by_category": []}

    async def _crime_outcode(outcode):
        return {"total": 150, "month": "2026-06", "by_category": []}

    async def _hpi(*args, **kwargs):
        return {"local_authority": {"name": "Manchester", "annual_change_pct": 4.1,
                                    "average_price": 260000, "period": "2026-06"}}

    async def _certs(_postcode):
        return []

    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    monkeypatch.setattr(app_main, "sold_prices_for_postcode", _sold)
    monkeypatch.setattr(app_main, "_comparables_for_extension", _comparables)
    monkeypatch.setattr(app_main.flood_zones, "zone_for", _zone)
    monkeypatch.setattr(app_main.crime, "summary_near", _crime)
    monkeypatch.setattr(app_main.crime, "summary_for_outcode", _crime_outcode)
    monkeypatch.setattr(app_main.schools_db, "school_landscape", lambda lat, lon: landscape)
    monkeypatch.setattr(app_main.hpi, "area_comparison", _hpi)
    monkeypatch.setattr(app_main.epc, "certificates_for_postcode", _certs)
    monkeypatch.setattr(app_main.area_stats, "deprivation_for_lsoa", lambda code: {"imd_decile": 3})
    monkeypatch.setattr(app_main.area_stats, "income_for_msoa", lambda code: EXT_INCOME)
    monkeypatch.setattr(app_main.census_stats, "occupation_for_lsoa", lambda code: {"professional_pct": 40})
    monkeypatch.setattr(app_main.rental, "rental_for_laua", lambda code: EXT_RENTAL)
    monkeypatch.setattr(app_main.email_service, "can_verify", lambda: False)
    for area_level in (False, True):
        _cache._evict(("extension_report", postcode, area_level))


def _ext_get(client, postcode, token=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.get("/api/extension-report", params={"postcode": postcode}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _premium_get(client, postcode, token):
    return client.get("/api/extension-premium-report", params={"postcode": postcode},
                      headers={"Authorization": f"Bearer {token}"})


def _token_for(client, email, subscribed=False):
    _signed_in(client, email)
    if subscribed:
        _subscribe(email)
    client.cookies.clear()
    return app_main._extension_token_serializer().dumps({"user_id": _user_id(email)})


def _cached_premium_payload(postcode):
    """A Premium payload already built for this postcode, so the gate alone
    decides who is given it and no test runs the ~35-service gather. Returns
    its cache key, for the test to forget."""
    from app.services import _cache
    key = ("extension_premium_report", postcode, False)
    _cache.set(key, {"postcode": postcode, "sections": [], "area_level": False, "district": None})
    return key


def test_while_the_switch_is_off_the_feed_is_what_2_3_0_was_sent(client, monkeypatch):
    """EXTENSION_240_LIVE False: every key and the teaser school row as the
    feed sent them before 21 Sep 2026, built or from the cache, signed in or
    not, plus free_cards, which 2.3.0 never reads (checked in its published
    content.js) and 2.4.0 needs from the day it is approved. 2.3.0 prints
    "no figure" for a row without admission_miles. True, the same payload
    blanks that row."""
    _switch(monkeypatch, False)
    _ext_install(monkeypatch, "M60 7BF")
    token = _token_for(client, "leak21-ext-switch-off@example.com")
    for attempt, bearer in (("built", None), ("from the cache", None), ("signed in, free", token)):
        data = _ext_get(client, "M60 7BF", bearer)
        assert set(data) == HEAD_KEYS | {"free_cards"}, attempt
        assert data["premium_unlocked"] is False, attempt
        assert data["schools"] == [KESTREL_ROW], attempt
        assert data["schools_full_count"] == 3, attempt

    _switch(monkeypatch, True)
    data = _ext_get(client, "M60 7BF")
    assert set(data) == HEAD_KEYS | {"free_cards", "schools_locked_label"}
    assert data["schools"] == [{**KESTREL_ROW, **dict.fromkeys(LOCKED_KEYS)}]


def test_with_the_switch_on_a_caller_without_a_token_gets_no_admission_distance_or_reading(client, monkeypatch):
    _switch(monkeypatch, True)
    _ext_install(monkeypatch, "M60 7BA")
    for attempt in ("built", "from the cache"):
        data = _ext_get(client, "M60 7BA")
        assert data["premium_unlocked"] is False, attempt
        assert data["schools"], "the teaser row is still there"
        for row in data["schools"]:
            assert {key: row[key] for key in LOCKED_KEYS} == dict.fromkeys(LOCKED_KEYS), attempt
        # What the report gives free stays: name, phase, Ofsted, distance, and
        # the slug, which only links the name to the school's free page.
        assert data["schools"] == [{**KESTREL_ROW, **dict.fromkeys(LOCKED_KEYS)}], attempt
        assert data["schools"][0]["slug"] == "kestrel-lane-primary"
        words = json.dumps(data["schools"]).lower()
        for hint in ("likely", "borderline", "published", "estimated", "1.2", "2025"):
            assert hint not in words, (attempt, hint)
        assert data["schools_locked_label"] == LOCKED_FREE


def test_the_paid_path_is_as_it_was_and_the_cached_rows_are_never_blanked(client, monkeypatch):
    _switch(monkeypatch, True)
    _ext_install(monkeypatch, "M60 7BB")
    # A caller with no token first, so the Premium call below is served
    # from the payload that call cached.
    assert _ext_get(client, "M60 7BB")["schools"][0]["verdict"] is None
    token = _token_for(client, "leak21-ext-premium@example.com", subscribed=True)
    data = _ext_get(client, "M60 7BB", token)
    assert data["premium_unlocked"] is True and "schools_locked_label" not in data
    assert data["schools_full_count"] == 3 and len(data["schools"]) == 3
    near, far, none = data["schools"]
    assert near == KESTREL_ROW
    assert ({key: far[key] for key in LOCKED_KEYS + ("slug",)}
            == {"admission_miles": 0.8, "admission_kind": "estimated", "admission_year": None,
                "verdict": "unlikely", "slug": None})
    assert none["admission_miles"] is None and none["verdict"] is None
    # The free cards come to a paying caller too.
    assert [c["title"] for c in data["free_cards"]] == ["Costs & Affordability", "Rental Analysis", "Household Income"]
    # With the switch off, a paying caller gets the same rows and cards.
    _switch(monkeypatch, False)
    off = _ext_get(client, "M60 7BB", token)
    assert off["schools"] == data["schools"] and off["free_cards"] == data["free_cards"]


def test_a_signed_in_free_account_gets_the_answer_only_for_a_home_it_opened(client, monkeypatch):
    _switch(monkeypatch, True)
    _ext_install(monkeypatch, "M60 7BC")
    email = "leak21-ext-free@example.com"
    token = _token_for(client, email)
    data = _ext_get(client, "M60 7BC", token)
    assert data["premium_unlocked"] is False and data["schools"][0]["verdict"] is None
    assert data["schools_locked_label"] == LOCKED_FREE
    # Its free full report spent on another postcode's report: Premium is
    # the way in here, and that postcode, asked the way the extension asks
    # (a postcode, no house number), is open.
    _unlock(email, "M60 7BD", "")
    assert _ext_get(client, "M60 7BC", token)["schools_locked_label"] == LOCKED_PREMIUM
    _ext_install(monkeypatch, "M60 7BD")
    opened = _ext_get(client, "M60 7BD", token)
    assert opened["premium_unlocked"] is True and "schools_locked_label" not in opened
    assert opened["schools"][0]["verdict"] == "likely" and opened["schools"][0]["admission_miles"] == 1.2


def test_the_three_checks_the_site_made_free_are_in_a_tokenless_payload(client, monkeypatch):
    _switch(monkeypatch, True)
    _ext_install(monkeypatch, "M60 7BE")
    data = _ext_get(client, "M60 7BE")
    assert [c["title"] for c in data["free_cards"]] == ["Costs & Affordability", "Rental Analysis", "Household Income"]
    cards = {c["title"]: c for c in data["free_cards"]}
    # Free on the site, and locked nowhere there.
    free = {title for _, title, _, _ in app_main.FREE_CHECKS}
    locked = {title for _, title, _, _ in app_main.PREMIUM_CHECKS}
    assert set(cards) <= free and not set(cards) & locked

    costs = cards["Costs & Affordability"]
    assert (costs["section"], costs["value"], costs["status"]) == ("Value & Market", "Stamp duty, mortgage, yield", "ok")
    # Started from the postcode's average sale (the two faked, £250,000),
    # as the report starts it for a reader who has not opened the home,
    # never a valuation.
    assert costs["detail"] == {"type": "calculator", "price": 250000.0, "rent": 1250, "country": "England"}

    rent = cards["Rental Analysis"]
    assert (rent["section"], rent["value"], rent["status"]) == ("Value & Market", "£1,250/month typical", "ok")

    income = cards["Household Income"]
    assert (income["section"], income["value"], income["status"]) == ("Area & Community", "£45,000 p/a", "ok")
    assert income["detail"] == {"type": "table", "columns": ["Area", "Household income"],
                                "rows": [["This neighbourhood", "£45,000"], ["Manchester", "£41,000"],
                                         ["North West", "£43,000"]]}


def test_a_lapsed_buying_pass_gets_no_admission_fields_and_no_premium_payload(client, monkeypatch):
    """Nothing resets is_premium when a buying pass runs out, and the login
    answer, the feed and the Premium payload all read it bare. They read
    auth.has_active_premium now, as the site does, whatever the switch."""
    from app import auth, db
    from app.services import _cache
    postcode = "M60 7CB"
    _ext_install(monkeypatch, postcode)
    email = "leak21-ext-lapsed@example.com"
    _signed_in(client, email)
    client.cookies.clear()

    def _pass_ends(days):
        with db.get_session() as session:
            user = auth.find_user_by_email(session, email)
            user.is_premium, user.plan = True, "pass"
            user.pass_expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
            session.commit()

    def _login():
        r = client.post("/api/extension-login", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return r.json()

    premium_key = _cached_premium_payload(postcode)
    try:
        _pass_ends(-1)
        login = _login()
        assert login["is_premium"] is False
        # With the switch on: not one admission field, and the words for
        # an account whose free full report is still to spend.
        _switch(monkeypatch, True)
        data = _ext_get(client, postcode, login["token"])
        assert data["premium_unlocked"] is False
        assert len(data["schools"]) == 1 and data["schools_full_count"] == 3
        for row in data["schools"]:
            assert {key: row[key] for key in LOCKED_KEYS} == dict.fromkeys(LOCKED_KEYS)
        assert data["schools_locked_label"] == LOCKED_FREE
        # With it off: what any caller without Premium is sent, one row.
        _switch(monkeypatch, False)
        data = _ext_get(client, postcode, login["token"])
        assert data["premium_unlocked"] is False and data["schools"] == [KESTREL_ROW]
        # And the Premium payload is refused, though one is built.
        r = _premium_get(client, postcode, login["token"])
        assert r.status_code == 403 and r.json() == {"error": "premium_required"}

        # A pass still running is Premium in all three.
        _pass_ends(7)
        login = _login()
        assert login["is_premium"] is True
        data = _ext_get(client, postcode, login["token"])
        assert data["premium_unlocked"] is True and len(data["schools"]) == 3
        r = _premium_get(client, postcode, login["token"])
        assert r.status_code == 200 and r.json()["postcode"] == postcode
    finally:
        _cache._evict(premium_key)


def test_an_unlock_opens_the_postcode_in_the_extension_in_any_spacing(client, monkeypatch):
    """Both extension endpoints asked auth.has_unlocked about the postcode
    as the listing gave it, so an account that opened "M60 7CA" was closed
    on "m607ca". They ask about the looked-up postcode now, in any spacing."""
    from app.services import _cache
    _switch(monkeypatch, True)
    postcode = "M60 7CA"
    _ext_install(monkeypatch, postcode)
    email = "leak21-ext-spacing@example.com"
    token = _token_for(client, email)
    _unlock(email, postcode, "")
    premium_key = _cached_premium_payload(postcode)
    try:
        for asked in ("m607ca", "M607CA", "m60 7ca", "M60 7CA"):
            data = _ext_get(client, asked, token)
            assert data["premium_unlocked"] is True, asked
            assert "schools_locked_label" not in data and data["schools"][0]["verdict"] == "likely", asked
            assert _premium_get(client, asked, token).status_code == 200, asked
    finally:
        _cache._evict(premium_key)
    # An unlock kept without its space opens the looked-up postcode too.
    _unlock(email, "M607CC", "")
    _ext_install(monkeypatch, "M60 7CC")
    assert _ext_get(client, "M60 7CC", token)["premium_unlocked"] is True
    # And one postcode's unlock opens no other.
    _ext_install(monkeypatch, "M60 7CF")
    other = _ext_get(client, "m607cf", token)
    assert other["premium_unlocked"] is False and other["schools_locked_label"] == LOCKED_PREMIUM
    assert _premium_get(client, "m607cf", token).status_code == 403


def test_the_locked_words_are_read_in_the_unlock_session(client, monkeypatch):
    """The words for where a locked answer opens took a session of their
    own; they are read in the one the unlock check already has open."""
    _switch(monkeypatch, True)
    _ext_install(monkeypatch, "M60 7CD")
    token = _token_for(client, "leak21-ext-one-session@example.com")
    sessions = []
    real_keys, real_state = app_main._opened_home_keys, app_main.auth.premium_state

    def _keys(user_id, session=None):
        sessions.append(("unlocks", session))
        return real_keys(user_id, session)

    def _state(user, db=None):
        sessions.append(("words", db))
        return real_state(user, db)

    monkeypatch.setattr(app_main, "_opened_home_keys", _keys)
    monkeypatch.setattr(app_main.auth, "premium_state", _state)
    data = _ext_get(client, "M60 7CD", token)
    assert data["schools_locked_label"] == LOCKED_FREE
    assert [kind for kind, _ in sessions] == ["unlocks", "words"]
    assert sessions[0][1] is not None and sessions[0][1] is sessions[1][1]
    # With the switch off nothing reads the words at all.
    sessions.clear()
    _switch(monkeypatch, False)
    _ext_get(client, "M60 7CD", token)
    assert [kind for kind, _ in sessions] == ["unlocks"]


def test_a_year_that_is_not_a_year_is_sent_as_none(client, monkeypatch):
    """832 profiles carry "varies" where a year would be, and both extension
    builds print the year in brackets after the distance, so a row read
    "admitted from 0.45 mi (varies)". The feed sends a year only when it is
    one (_year_label, the site's own rule), and None otherwise, whatever the
    switch says."""
    landscape = {
        "total_schools": 2, "good_or_better_pct": 100, "radius_miles": 3,
        "all_schools": [
            {"urn": 990771, "name": "Brentry Lane Primary", "distance_m": 400, "phase_group": "Primary",
             "ofsted_rating_label": "Good",
             "admission_radius": {"last_distance_miles": 0.45, "academic_year": "varies"}},
            {"urn": 990772, "name": "Coombe Hill Academy", "distance_m": 900, "phase_group": "Secondary",
             "ofsted_rating_label": "Good",
             "admission_radius": {"last_distance_miles": 2.1, "academic_year": "2025/26"}},
        ],
    }
    token = _token_for(client, "leak21-ext-years@example.com", subscribed=True)
    for live in (False, True):
        _switch(monkeypatch, live)
        _ext_install(monkeypatch, "M60 7CE", landscape=landscape)
        paid = _ext_get(client, "M60 7CE", token)
        assert [(s["name"], s["admission_year"]) for s in paid["schools"]] == [
            ("Brentry Lane Primary", None), ("Coombe Hill Academy", "2025/26")], live
        assert (paid["schools"][0]["admission_miles"], paid["schools"][0]["verdict"]) == (0.45, "likely"), live
    # The switch off, the teaser row a caller with no token is sent is the
    # row it was always sent, with that one field cleaned.
    _switch(monkeypatch, False)
    assert _ext_get(client, "M60 7CE")["schools"] == [{
        "name": "Brentry Lane Primary", "urn": 990771, "slug": "brentry-lane-primary", "distance_m": 400,
        "phase": "Primary", "ofsted_rating_label": "Good", "admission_miles": 0.45,
        "admission_kind": "published", "admission_year": None, "verdict": "likely"}]


def test_the_new_build_shows_the_three_as_free_and_the_schools_lock_in_words():
    js = (ROOT / "browser-extension" / "content.js").read_text(encoding="utf-8")
    placeholders = js.split("const PREMIUM_SECTIONS = [", 1)[1].split("];", 1)[0]
    for title in ("Costs & Affordability", "Rental Analysis", "Household Income"):
        assert f'"{title}"' not in placeholders, title
    for title in ("Local Market", "Valuation Estimate", "Price Trend", "School Catchment Areas", "Deprivation"):
        assert f'"{title}"' in placeholders, title
    # The free cards open at the head of their group, and the popup finds
    # their detail without a Premium payload.
    overview = js.split("overview: function (data) {", 1)[1].split("map: function (data) {", 1)[0]
    assert "const freeCards = data.free_cards || [];" in overview
    assert "function findFreeCard(title)" in js and "findPremiumCard(title) || findFreeCard(title)" in js
    # A locked admission column says where it opens, never "no figure".
    schools = js.split("schools: function (data) {", 1)[1].split("epc: function (data) {", 1)[0]
    assert "data.premium_unlocked ? \"\" : (data.schools_locked_label" in schools
    assert schools.index("if (lockedLabel)") < schools.index("if (!s.admission_miles)")

    manifest = json.loads((ROOT / "browser-extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "2.4.0"
    readme = (ROOT / "browser-extension" / "README.md").read_text(encoding="utf-8")
    assert "### 2.4.0, 21 September 2026" in readme


# The words the pages that describe the extension use, before and after
# 2.4.0 is live.
ANSWER_BEFORE = "including the admissions verdict for the nearest school"
ANSWER_AFTER = "and the costs calculator, typical rent and household income in full"
OFFER_BEFORE = ("The free Chrome extension puts sold prices, the flood zone and each nearby school's Likely, "
                "Borderline or Unlikely reading on Rightmove, Zoopla and OnTheMarket listings, from the same "
                "sources as this report.")
OFFER_AFTER = ("The free Chrome extension puts sold prices, the flood zone and the nearest schools on Rightmove, "
               "Zoopla and OnTheMarket listings, from the same sources as this report. With Premium it also "
               "says whether a place at each school is Likely, Borderline or Unlikely.")


def test_the_pages_that_describe_the_extension_keep_their_words_until_the_switch(client, fake_report, monkeypatch):
    """The /browser-extension FAQ's account answer, in its FAQPage data and
    its list, and the report's extension offer: the old words while 2.3.0 is
    the published build, the new ones once EXTENSION_240_LIVE is True, and
    never both."""
    from app.services import _cache
    for live, answer, other in ((False, ANSWER_BEFORE, ANSWER_AFTER), (True, ANSWER_AFTER, ANSWER_BEFORE)):
        _switch(monkeypatch, live)
        client.cookies.clear()
        for key in [k for k in _cache._store if isinstance(k, tuple) and k and k[0] == "anon_html"]:
            _cache._evict(key)
        body = client.get("/browser-extension").text
        assert body.count(answer) == 2, live
        assert other not in body, live

    fake_report()
    _signed_in(client, "leak21-offer-words@example.com")
    for live, offer, other in ((False, OFFER_BEFORE, OFFER_AFTER), (True, OFFER_AFTER, OFFER_BEFORE)):
        _switch(monkeypatch, live)
        body = client.get("/property?postcode=M14%205TG").text
        shown = _flat(body.split('<p class="compare-offer-lead">The next listing you open can show this too.</p>', 1)[1]
                      .split("</div>", 1)[0])
        assert offer in shown, live
        assert other not in shown, live
