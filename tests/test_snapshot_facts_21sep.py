"""A new sale the count cannot see, and two free must-haves My properties
could not answer (21 Sep 2026).

1. The new sale trigger fired only when a saved home's count of sales
   rose, and a count can stay level while a sale is added: a monthly Price
   Paid update can add one sale at a postcode and remove another record
   there. (The per-postcode query both snapshots count has no row limit; the
   300 cap in land_registry.py is the nearby-sales query's, so a list cut to
   its newest rows is not a case these paths can meet today.) Each snapshot
   now records the newest sale date among the sales it is about
   (latest_sale_date, the home's own where a house number is saved), and a
   later date is a new sale too: the sales dated after it counted where the
   caller holds them, said as one where it does not, and never counted twice
   when the count sees it as well. The rule's limits are pinned too: a
   late-registered older sale and a removal in the same month give no line,
   and a newest sale, a late one and a removal are reported as one. A
   snapshot written before this change has no date and never fires on the
   date alone. The line now says what alert_triggers promises: a sale of this
   home, or a new sale at this postcode.

2. Surface water and broadband are free checks on the report, but a saved
   home's snapshot did not carry them, so My properties read "Not yet known"
   for both on every home. A report visit now writes them, My properties and
   the alert job carry them forward from the stored snapshot (no fetch and no
   statement per home), and neither is ever a change or an email.

Every property saved here has its own house number: the tests share one
database.
"""
import asyncio
import datetime
import html
import json
import re

from sqlalchemy import event, select

from app import main as app_main
from tests.conftest import fake_gather, fake_location
from tests.test_pages import _signed_in


def _flat(text):
    return " ".join(html.unescape(text).split())


def _user_id(email):
    from app import db
    from app.models import User
    with db.get_session() as session:
        return session.scalar(select(User.id).where(User.email == email))


def _snapshot(email, postcode, house_number):
    from app import watchlist
    item = watchlist.find_in(watchlist.list_items(_user_id(email)), postcode, house_number)
    return json.loads(item["last_snapshot"]) if item and item["last_snapshot"] else None


def _sales(count, newest, address=None):
    """count Price Paid records, newest first, a week apart, addressed the
    way land_registry builds them."""
    start = datetime.date.fromisoformat(newest)
    return [{"address": address or f"{i + 1} TEST STREET", "amount": str(250000 + i * 100),
             "date": (start - datetime.timedelta(days=7 * i)).isoformat()} for i in range(count)]


def _sale(date, address="99 TEST STREET"):
    return {"address": address, "amount": "260000", "date": date}


def _summary(sales, house_number=""):
    """The snapshot a report visit writes, over the gather's own list, which
    is already this home's sales where there is a house number."""
    return app_main._summary_from_report({"transactions": sales}, "M14 5TG", house_number)


def _sale_items(old, new, sales=None):
    return [item for item in app_main._snapshot_change_items(old, new, sales) if item[0] == "sale"]


POSTCODE_ONE = "A new sale has been recorded at this postcode since you last looked"
HOME_ONE = "A sale of this home has been recorded since you last looked"


# ==== 1. A new sale the count cannot see =================================

def test_a_monthly_update_that_adds_sales_and_removes_as_many_makes_one_sale_change_with_the_right_count():
    before = _sales(12, "2026-06-30")
    # Two sales since, and two older records removed in the same monthly
    # update: the count is level at 12.
    after = [_sale("2026-08-03"), _sale("2026-07-20", "98 TEST STREET")] + before[:10]
    old, new = _summary(before), _summary(after)
    assert old["tx_count"] == new["tx_count"] == 12
    assert (old["latest_sale_date"], new["latest_sale_date"]) == ("2026-06-30", "2026-08-03")

    # With the sales in hand (the report has them), the two dated after the
    # old newest are counted, once.
    assert _sale_items(old, new, after) == [
        ("sale", "2 new sales have been recorded at this postcode since you last looked")]
    assert app_main._alert_changes(old, new, after)[0] == "2 new sales have been recorded at this postcode since you last looked"
    # Without them (My properties and the alert job), said as one.
    assert _sale_items(old, new) == [("sale", POSTCODE_ONE)]
    assert app_main._alert_changes(old, new)[0] == POSTCODE_ONE

    # A house-numbered home whose count stays level (one sale recorded, one
    # old record deleted) is told about a sale of this home.
    home_before = _sales(3, "2019-03-01", "2201 TEST STREET")
    home_after = [_sale("2026-08-01", "2201 TEST STREET")] + home_before[:2]
    old, new = _summary(home_before, "2201"), _summary(home_after, "2201")
    assert old["tx_count"] == new["tx_count"] == 3
    assert _sale_items(old, new, home_after) == [("sale", HOME_ONE)]
    assert _sale_items(old, new) == [("sale", HOME_ONE)]


def test_an_old_snapshot_without_the_date_never_fires_on_it_alone():
    sales = _sales(12, "2026-08-01")
    new = _summary(sales)
    # Written before 21 Sep 2026: the same sales, and no latest_sale_date.
    old = {key: value for key, value in new.items() if key != "latest_sale_date"}
    assert app_main._snapshot_change_items(old, new, sales) == []
    assert app_main._snapshot_change_items(old, new) == []
    assert app_main._alert_changes(old, new, sales) == []
    # The count rule still speaks against an old snapshot, as it always did.
    grown = _summary(_sales(4, "2026-08-01"))
    assert _sale_items({"tx_count": 3, "avg_price": 250000.0}, grown) == [("sale", POSTCODE_ONE)]
    # The new snapshot records the date, and the next comparison uses it:
    # one sale added and one older record removed, the count level.
    later = [_sale("2026-09-01")] + sales[:11]
    assert _sale_items(new, _summary(later), later) == [("sale", POSTCODE_ONE)]


def test_the_count_still_fires_and_one_sale_is_never_counted_twice():
    before = _sales(3, "2026-05-01")
    after = [_sale("2026-08-01")] + before
    old, new = _summary(before), _summary(after)
    # Both rules see the one new sale: one line, and it says one.
    for sales in (after, None):
        assert _sale_items(old, new, sales) == [("sale", POSTCODE_ONE)]
    # Two new records, one of them registered late with an earlier date: the
    # count's two stands, the date rule's one is inside it.
    late = after + [_sale("2026-04-01", "97 TEST STREET")]
    assert _sale_items(old, _summary(late), late) == [
        ("sale", "2 new sales have been recorded at this postcode since you last looked")]
    assert _sale_items(old, _summary(late)) == [
        ("sale", "2 new sales have been recorded at this postcode since you last looked")]
    # A late registration alone: the newest date has not moved, the count fires.
    registered_late = before + [_sale("2026-04-01", "97 TEST STREET")]
    assert _summary(registered_late)["latest_sale_date"] == old["latest_sale_date"]
    assert _sale_items(old, _summary(registered_late), registered_late) == [("sale", POSTCODE_ONE)]
    # A house-numbered home, by the count alone.
    home = _sales(1, "2015-01-01", "2203 TEST STREET")
    two_more = [_sale("2026-08-01", "2203 TEST STREET"), _sale("2021-01-01", "2203 TEST STREET")] + home
    assert _sale_items(_summary(home, "2203"), _summary(two_more, "2203"), two_more) == [
        ("sale", "2 sales of this home have been recorded since you last looked")]
    # And a newest date that went back (a record deleted) is no sale at all.
    assert _sale_items(new, old, before) == []


def test_what_the_date_rule_cannot_see_is_what_its_comment_says():
    """The limits the sale branch of _snapshot_change_items names, pinned so
    the comment cannot drift from the code (21 Sep 2026)."""
    before = _sales(6, "2026-05-01")
    old = _summary(before)
    # A late-registered older sale and a removal in the same month: the count
    # is level and the newest date has not moved, so there is no line.
    late_and_removed = before[:5] + [_sale("2026-03-01", "96 TEST STREET")]
    new = _summary(late_and_removed)
    assert new["tx_count"] == old["tx_count"] and new["latest_sale_date"] == old["latest_sale_date"]
    for sales in (late_and_removed, None):
        assert _sale_items(old, new, sales) == []
    # A newest sale, a late one and a removal: two sales, reported as one.
    all_three = [_sale("2026-08-01")] + before[:5] + [_sale("2026-03-01", "96 TEST STREET")]
    new = _summary(all_three)
    assert new["tx_count"] == old["tx_count"] + 1
    for sales in (all_three, None):
        assert _sale_items(old, new, sales) == [("sale", POSTCODE_ONE)]


def test_the_sale_line_says_what_alert_triggers_promises():
    for house_number in ("2205", ""):
        promise = app_main.alert_triggers(house_number)[0]
        line = app_main._sale_change_text(1, house_number)
        assert line.lower().startswith(promise.replace(" is recorded", " has been recorded")), (promise, line)
    assert app_main._sale_change_text(1, "2205") == HOME_ONE
    assert app_main._sale_change_text(3, "2205") == "3 sales of this home have been recorded since you last looked"
    assert app_main._sale_change_text(1, "") == POSTCODE_ONE
    assert app_main._sale_change_text(4, "") == "4 new sales have been recorded at this postcode since you last looked"
    # Whitespace is no house number, as it is for alert_triggers.
    assert app_main._sale_change_text(1, "  ") == POSTCODE_ONE
    # A return visit on a sale still opens Value & Market, and the certificate
    # line, which ends "prepared for sale", still opens Property & Condition.
    for line in (HOME_ONE, POSTCODE_ONE, app_main._sale_change_text(2, "2205"), app_main._sale_change_text(2, "")):
        assert app_main._group_for_changes([line]) == "cat-value-market", line
    assert app_main._group_for_changes([
        "A new energy certificate was lodged, often a sign the property is being prepared for sale"
    ]) == "cat-property-condition"


def test_both_summaries_record_the_newest_of_the_sales_they_are_about(monkeypatch):
    """_comparison_summary, which the alert job and My properties use, takes
    the date from the same _filter_by_address list as its count: a house
    number's own sales, a neighbour's never. Postcodes of its own, as the
    summary is cached by postcode and house number."""
    from app.services import area_stats, crime, flood_zones, hpi, schools_db
    postcode = "M63 1AA"
    sales = [
        {"address": "14 ACACIA AVENUE", "amount": "300000", "date": "2026-08-01", "tenure": "Freehold"},
        {"address": "2207 ACACIA AVENUE", "amount": "250000", "date": "2019-03-01", "tenure": "Freehold"},
        {"address": "22 ACACIA AVENUE", "amount": "275000", "date": "2023-06-01", "tenure": "Freehold"},
    ]

    async def _location(_pc):
        return fake_location(postcode=postcode, outcode="M63")

    async def _sold(_pc):
        return sales

    async def _down(*_a, **_k):
        raise RuntimeError("not under test")

    def _down_sync(*_a, **_k):
        raise RuntimeError("not under test")

    monkeypatch.setattr(app_main, "lookup_postcode", _location)
    monkeypatch.setattr(app_main, "sold_prices_for_postcode", _sold)
    monkeypatch.setattr(app_main, "_epc_flow", _down)
    monkeypatch.setattr(flood_zones, "zone_for", _down)
    monkeypatch.setattr(crime, "summary_near", _down)
    monkeypatch.setattr(hpi, "area_comparison", _down)
    monkeypatch.setattr(area_stats, "deprivation_for_lsoa", _down_sync)
    monkeypatch.setattr(schools_db, "school_landscape", _down_sync)

    home = asyncio.run(app_main._comparison_summary(postcode, "2207"))
    assert home["tx_count"] == 1 and home["latest_sale_date"] == "2019-03-01"
    whole = asyncio.run(app_main._comparison_summary(postcode, ""))
    assert whole["tx_count"] == 3 and whole["latest_sale_date"] == "2026-08-01"
    # The report's summary, over the gather's own filtered list, agrees.
    mine = app_main._filter_by_address(sales, "2207")
    assert app_main._summary_from_report({"transactions": mine}, postcode, "2207")["latest_sale_date"] == "2019-03-01"
    # No dated sale, no key: nothing to compare rather than a guess.
    undated = app_main._summary_from_report({"transactions": [{"address": "2207 ACACIA AVENUE", "amount": "1"}]},
                                            postcode, "2207")
    assert undated["tx_count"] == 1 and "latest_sale_date" not in undated


def test_the_report_counts_new_sales_of_this_home_the_count_cannot_see(client, fake_report, monkeypatch):
    monkeypatch.setattr(app_main.email_service, "can_verify", lambda: False)
    email = "snap21-report-sales@example.com"
    _signed_in(client, email)
    home = "2209 Test Street"
    url = {"postcode": "M14 5TG", "house_number": "2209"}
    before = [{"address": home, "postcode": "M14 5TG", "amount": amount, "date": date}
              for amount, date in (("250000", "2019-03-01"), ("200000", "2012-05-01"), ("150000", "2004-07-01"))]
    fake_report(gather=fake_gather(transactions=before))
    first = client.get("/property", params=url).text
    assert 'class="since-visit"' not in first
    assert _snapshot(email, "M14 5TG", "2209")["latest_sale_date"] == "2019-03-01"

    # Two sales since, and two old records gone: the count is level at 3.
    after = [{"address": home, "postcode": "M14 5TG", "amount": "320000", "date": "2026-08-01"},
             {"address": home, "postcode": "M14 5TG", "amount": "290000", "date": "2025-02-01"}, before[0]]
    fake_report(gather=fake_gather(transactions=after))
    second = _flat(client.get("/property", params=url).text)
    assert "2 sales of this home have been recorded since you last looked" in second
    assert 'data-open-group="cat-value-market"' in second
    assert _snapshot(email, "M14 5TG", "2209")["latest_sale_date"] == "2026-08-01"


def _run_alert_job(client, monkeypatch, email, postcode, house_number, stored, fresh):
    """Run the change-alert job with `fresh` as this home's summary. `stored`
    is written as its snapshot first, or None to keep what it holds. Every
    other saved home in the shared database gets a summary with nothing to
    compare. Returns what this account was sent and the snapshot left."""
    from app import watchlist
    email_service = app_main.email_service
    monkeypatch.setenv("ALERTS_CRON_SECRET", "snap21-secret")
    monkeypatch.setattr(email_service, "is_configured", lambda: True)
    monkeypatch.setattr(email_service, "can_verify", lambda: False)
    sent = []

    async def _send(to, subject, body):
        sent.append({"to": to, "subject": subject, "html": body})
        return True

    monkeypatch.setattr(email_service, "send_email", _send)

    async def _summary_for(pc, hn):
        if (pc, hn) == (postcode, house_number):
            return {"postcode": pc, "house_number": hn, **fresh}
        return {"postcode": pc}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary_for)
    if _user_id(email) is None:
        _signed_in(client, email)
    uid = _user_id(email)
    if watchlist.find_in(watchlist.list_items(uid), postcode, house_number) is None:
        watchlist.save_item(uid, postcode, house_number, "")
    if stored is not None:
        item = watchlist.find_in(watchlist.list_items(uid), postcode, house_number)
        watchlist.update_snapshot(uid, item["id"], json.dumps(
            {"postcode": postcode, "house_number": house_number, **stored}))
    r = client.post("/internal/run-watchlist-alerts", headers={"x-alerts-secret": "snap21-secret"})
    assert r.status_code == 200
    return [m for m in sent if m["to"] == email], _snapshot(email, postcode, house_number)


def test_the_alert_job_emails_a_sale_the_count_cannot_see(client, monkeypatch):
    mine, left = _run_alert_job(
        client, monkeypatch, "snap21-job-sale@customer.test", "M64 2AA", "2211",
        stored={"tx_count": 4, "avg_price": 250000.0, "latest_sale_date": "2026-06-30"},
        fresh={"tx_count": 4, "avg_price": 250000.0, "latest_sale_date": "2026-08-01"})
    assert [m["subject"] for m in mine] == [f"M64 2AA, 2211: {HOME_ONE}"]
    assert left["latest_sale_date"] == "2026-08-01"


def test_the_alert_job_records_the_date_on_an_old_snapshot_and_compares_next_time(client, monkeypatch):
    email = "snap21-job-first@customer.test"
    mine, left = _run_alert_job(
        client, monkeypatch, email, "M64 3AA", "2213",
        stored={"tx_count": 12, "avg_price": 250000.0},
        fresh={"tx_count": 12, "avg_price": 250000.0, "latest_sale_date": "2026-08-01"})
    assert mine == [], "a snapshot from before the change fired on the date alone"
    assert left["latest_sale_date"] == "2026-08-01"
    mine, left = _run_alert_job(
        client, monkeypatch, email, "M64 3AA", "2213", stored=None,
        fresh={"tx_count": 12, "avg_price": 250000.0, "latest_sale_date": "2026-09-01"})
    assert [m["subject"] for m in mine] == [f"M64 3AA, 2213: {HOME_ONE}"]
    assert left["latest_sale_date"] == "2026-09-01"


# ==== 2. Surface water and broadband on My properties =====================

SURFACE_LOW = {"label": "Low risk", "probability": "1 in 1000 (0.1%) to 1 in 100"}
SURFACE_HIGH = {"label": "High risk", "probability": "1 in 30 (3.3%) or greater"}
GIGABIT = {"gigabit_pct": 95, "ultrafast_pct": 95, "superfast_pct": 100, "below_uso_pct": 0,
           "label": "Gigabit-capable"}


def _musthave_cards(body):
    """{address: (the must-haves line, [each condition's line])} per saved home."""
    out = {}
    for card in re.split(r'<div class="myprops-card(?: myprops-card-changed)?">', body)[1:]:
        address = _flat(re.search(r'class="myprops-address"[^>]*>(.*?)</a>', card, re.S).group(1))
        line = re.search(r"Must-haves: <strong>(.*?)</strong>", card, re.S)
        rows = re.findall(r'<span class="myprops-musthave musthave-\w+">(.*?)</span>', card, re.S)
        out[address] = (_flat(line.group(1)) if line else None, [_flat(row) for row in rows])
    return out


def test_a_report_writes_both_into_the_snapshot_in_its_own_words():
    from app import must_haves
    here = app_main._summary_from_report(fake_gather(surface_water=SURFACE_LOW), "M14 5TG", "2221")
    assert (here["surface_water"], here["broadband"]) == ("Low risk", "Superfast")
    # Outside England the maps do not reach, and the report says so.
    wales = app_main._summary_from_report(
        fake_gather(surface_water=None, flood_not_covered={"country": "Wales"}), "CF10 1AA", "2221")
    assert wales["surface_water"] == "Not mapped for Wales"
    # Ofcom holds no figure for the postcode: the report's answer, kept.
    assert app_main._summary_from_report(fake_gather(broadband=None), "M14 5TG", "2221")["broadband"] == \
        must_haves.BROADBAND_NO_FIGURE
    # A check that failed on this render writes nothing, so nothing is lost.
    failed = app_main._summary_from_report(fake_gather(broadband_error=True), "M14 5TG", "2221")
    assert "surface_water" not in failed and "broadband" not in failed
    assert app_main.REPORT_ONLY_SNAPSHOT_KEYS == ("surface_water", "broadband")


def test_my_properties_answers_both_must_haves_from_what_the_report_wrote(client, fake_report, monkeypatch):
    from app import must_haves, watchlist
    monkeypatch.setattr(app_main.email_service, "can_verify", lambda: False)
    email = "snap21-mine@example.com"
    _signed_in(client, email)
    uid = _user_id(email)
    # 2223 is opened since the change, so its report writes both.
    fake_report(gather=fake_gather(surface_water=SURFACE_LOW, transactions=[
        {"address": "2223 Test Street", "postcode": "M14 5TG", "amount": "250000", "date": "2024-06-01"}]))
    assert client.get("/property", params={"postcode": "M14 5TG", "house_number": "2223"}).status_code == 200
    written = _snapshot(email, "M14 5TG", "2223")
    assert (written["surface_water"], written["broadband"]) == ("Low risk", "Superfast")
    # 2225 is saved and its report not opened since.
    watchlist.save_item(uid, "M14 5TG", "2225", "")

    # My properties writes its snapshots from _comparison_summary, which
    # fetches neither.
    def _fresh(house_number):
        return {"postcode": "M14 5TG", "house_number": house_number, "admin_district": "Manchester",
                "tx_count": 1, "avg_price": 250000.0, "latest_sale_date": "2024-06-01",
                "flood_zone": "Zone 1 (low probability)"}

    async def _summary_for(postcode, house_number):
        return _fresh(house_number)

    monkeypatch.setattr(app_main, "_comparison_summary", _summary_for)
    must_haves.save(uid, {"surface_water": "Low risk", "broadband": "Superfast"})
    cards = _musthave_cards(client.get("/watchlist").text)
    assert cards["2223, M14 5TG"] == ("2 of 2 met", [
        "Surface water risk no worse than Low risk: Low risk", "Broadband at least Superfast: Superfast"])
    not_yet = must_haves.NOT_YET_FROM_REPORT
    assert not_yet == "Not yet known until you next open this home's report"
    assert cards["2225, M14 5TG"] == ("0 of 2 met, 2 not yet known", [
        f"Surface water risk no worse than Low risk: {not_yet}", f"Broadband at least Superfast: {not_yet}"])
    # The page wrote its own snapshot and kept both in it.
    assert _snapshot(email, "M14 5TG", "2223") == {**_fresh("2223"), "surface_water": "Low risk",
                                                   "broadband": "Superfast"}
    assert "surface_water" not in _snapshot(email, "M14 5TG", "2225")

    # The alert job keeps them too, and has nothing to send.
    mine, left = _run_alert_job(client, monkeypatch, email, "M14 5TG", "2223", None,
                                {key: value for key, value in _fresh("2223").items()
                                 if key not in ("postcode", "house_number")})
    assert mine == []
    assert (left["surface_water"], left["broadband"]) == ("Low risk", "Superfast")


def test_a_home_outside_england_is_not_mapped_for_surface_water_before_its_report_is_opened():
    """The flood zone's own gap, from the same Environment Agency coverage
    rule (flood_zones.outside_coverage), rather than "not yet known"."""
    from app import must_haves
    known = must_haves.facts_from_snapshot({"flood_zone": "Not mapped for Wales"})
    assert known["surface_water"]["known"] is False
    assert known["surface_water"]["gap"] == "Not mapped for Wales"
    assert known["broadband"]["gap"] == must_haves.NOT_YET_FROM_REPORT
    # What a report wrote is read back as the report said it.
    known = must_haves.facts_from_snapshot({"surface_water": "High risk",
                                            "broadband": must_haves.BROADBAND_NO_FIGURE})
    assert (known["surface_water"]["known"], known["surface_water"]["text"]) == (True, "High risk")
    assert (known["broadband"]["known"], known["broadband"]["gap"]) == (False, must_haves.BROADBAND_NO_FIGURE)


def test_a_report_whose_check_failed_keeps_the_last_reading_and_one_that_answered_replaces_it(
        client, fake_report, monkeypatch):
    from app import must_haves
    monkeypatch.setattr(app_main.email_service, "can_verify", lambda: False)
    email = "snap21-report-keeps@example.com"
    _signed_in(client, email)
    url = {"postcode": "M14 5TG", "house_number": "2227"}
    fake_report(gather=fake_gather(surface_water=SURFACE_LOW))
    client.get("/property", params=url)
    # Both checks fail on the next render: the last readings stay.
    fake_report(gather=fake_gather(broadband_error=True))
    client.get("/property", params=url)
    kept = _snapshot(email, "M14 5TG", "2227")
    assert (kept["surface_water"], kept["broadband"]) == ("Low risk", "Superfast")
    # Answered again, the new readings replace them, words and all.
    fake_report(gather=fake_gather(surface_water=SURFACE_HIGH, broadband=None))
    client.get("/property", params=url)
    now = _snapshot(email, "M14 5TG", "2227")
    assert (now["surface_water"], now["broadband"]) == ("High risk", must_haves.BROADBAND_NO_FIGURE)


def test_neither_fact_is_ever_a_change_or_an_email(client, fake_report, monkeypatch):
    from app import must_haves
    base = {"tx_count": 2, "avg_price": 250000.0, "latest_sale_date": "2026-01-01",
            "flood_zone": "Zone 1 (low probability)"}
    moves = [
        ({"surface_water": "Very low risk", "broadband": "Gigabit-capable"},
         {"surface_water": "High risk", "broadband": "Standard/limited"}),
        ({"surface_water": "Low risk", "broadband": "Superfast"}, {}),
        ({}, {"surface_water": "Not mapped for Wales", "broadband": must_haves.BROADBAND_NO_FIGURE}),
    ]
    for before, after in moves:
        old, new = {**base, **before}, {**base, **after}
        assert app_main._snapshot_change_items(old, new) == [], (before, after)
        assert app_main._alert_changes(old, new) == [], (before, after)
    for key in app_main.REPORT_ONLY_SNAPSHOT_KEYS:
        assert key not in app_main.ALERT_CHANGE_KINDS

    # The alert job, handed a summary in which both moved, sends nothing.
    mine, left = _run_alert_job(
        client, monkeypatch, "snap21-quiet@customer.test", "M64 4AA", "2229",
        stored={**base, "surface_water": "Very low risk", "broadband": "Gigabit-capable"},
        fresh={**base, "surface_water": "High risk", "broadband": "Standard/limited"})
    assert mine == []

    # A return visit to a report on which only they moved says nothing either.
    client.cookies.clear()
    monkeypatch.setattr(app_main.email_service, "can_verify", lambda: False)
    email = "snap21-report-quiet@example.com"
    _signed_in(client, email)
    url = {"postcode": "M14 5TG", "house_number": "2231"}
    fake_report(gather=fake_gather(surface_water=SURFACE_LOW))
    client.get("/property", params=url)
    fake_report(gather=fake_gather(surface_water=SURFACE_HIGH, broadband=GIGABIT))
    second = client.get("/property", params=url).text
    assert 'class="since-visit"' not in second and 'data-open-group=""' in second
    assert _snapshot(email, "M14 5TG", "2231")["surface_water"] == "High risk"


def test_my_properties_keeps_them_at_no_statement_per_home(client, monkeypatch):
    """Counted before and after the change on the same page: every statement,
    not only the writes. The count does not grow with the number of saved
    homes, and a list whose snapshots did not move writes nothing, where it
    used to rewrite every home's snapshot without the two facts."""
    from app import db, watchlist
    monkeypatch.setattr(app_main.email_service, "can_verify", lambda: False)
    email = "snap21-rounds@example.com"
    _signed_in(client, email)
    uid = _user_id(email)
    fresh = {"admin_district": "Manchester", "tx_count": 3, "avg_price": 250000.0,
             "latest_sale_date": "2026-05-01", "flood_zone": "Zone 1 (low probability)"}

    async def _summary_for(postcode, house_number):
        return {"postcode": postcode, "house_number": house_number, **fresh}

    monkeypatch.setattr(app_main, "_comparison_summary", _summary_for)

    def _save(house_number):
        watchlist.save_item(uid, "M64 5AA", house_number, "")
        item = watchlist.find_in(watchlist.list_items(uid), "M64 5AA", house_number)
        watchlist.update_snapshot(uid, item["id"], json.dumps({
            "postcode": "M64 5AA", "house_number": house_number, **fresh,
            "surface_water": "Low risk", "broadband": "Superfast"}))

    engine = db._get_engine()

    def _visit():
        seen = []

        def _count(_conn, _cursor, statement, *_rest):
            seen.append(statement)

        event.listen(engine, "before_cursor_execute", _count)
        try:
            assert client.get("/watchlist").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        return seen

    for house_number in ("2241", "2242"):
        _save(house_number)
    two_homes = _visit()
    for house_number in ("2243", "2244", "2245", "2246"):
        _save(house_number)
    six_homes = _visit()
    assert len(six_homes) == len(two_homes), (two_homes, six_homes)
    assert not any(s.lstrip().upper().startswith("UPDATE") for s in two_homes + six_homes)
    for item in watchlist.list_items(uid):
        snapshot = json.loads(item["last_snapshot"])
        assert (snapshot["surface_water"], snapshot["broadband"]) == ("Low risk", "Superfast"), item
