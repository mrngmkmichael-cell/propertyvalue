"""Two promises about the outreach, enforced here rather than remembered.

One: a firm is written to once. On 30 September 2026 seven firms received
the same email two or three times, because the duplicate check read a
mailbox folder that came back empty while the store was syncing. The
check now lives in docs/outreach/prospects.json, and this test fails the
build if that file ever holds the same address, or the same domain, twice.

Two: two days of emails do not read alike. Each firm draws its subject,
its opening angle and its price sentence from a rotation keyed by its own
address and the day it was added, so a week of letters is not one letter
with the names changed.
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEDGER = ROOT / "docs" / "outreach" / "prospects.json"
CATEGORIES = {"reloc", "agent", "broker", "convey"}


def _builder():
    spec = importlib.util.spec_from_file_location(
        "outreach_build_emails", ROOT / "scripts" / "outreach_build_emails.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build():
    return _builder()


@pytest.fixture(scope="module")
def ledger():
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def test_no_firm_is_written_to_twice(build, ledger):
    build.check_one_firm_one_email(ledger)  # raises SystemExit on a repeat


def test_a_repeat_address_stops_the_build(build):
    twice = [{"firm": "A", "email": "hello@example.com"},
             {"firm": "B", "email": "Hello@example.com"}]
    with pytest.raises(SystemExit):
        build.check_one_firm_one_email(twice)


def test_a_second_person_at_the_same_firm_stops_the_build_unless_said(build):
    same = [{"firm": "A", "email": "jo@example.com"},
            {"firm": "A, other desk", "email": "sam@example.com"}]
    with pytest.raises(SystemExit):
        build.check_one_firm_one_email(same)
    same[1]["allow_same_domain"] = True
    build.check_one_firm_one_email(same)


def test_every_entry_says_where_its_address_came_from(ledger):
    for p in ledger:
        assert p["firm"] and p["category"] in CATEGORIES, p
        assert p["source"], f"{p['firm']} has no source page"
        assert len(p["added"]) == 10 and p["added"][4] == "-", p["added"]
        if p.get("email"):
            assert "@" in p["email"] and " " not in p["email"], p["email"]


def test_a_batch_does_not_read_as_one_letter(build):
    """Ten firms added the same day draw more than one opening and more
    than one subject between them."""
    figures = {"schools": "3,627", "councils": "88", "checks": "44", "free": "29"}
    batch = [{"email": f"person{i}@firm{i}.co.uk", "added": "2026-10-01", "category": "agent"}
             for i in range(10)]
    openings = {build.pick(build.angles(figures), p, "angle") for p in batch}
    subjects = {build.pick(build.SUBJECTS["agent"], p, "subject") for p in batch}
    prices = {build.pick(build.PRICES, p, "price") for p in batch}
    assert len(openings) >= 3, "a day of emails should not all open the same way"
    assert len(subjects) >= 3, "a day of emails should not share one subject line"
    assert len(prices) >= 2


def test_the_same_firm_always_rebuilds_to_the_same_email(build):
    figures = {"schools": "3,627", "councils": "88", "checks": "44", "free": "29"}
    p = {"email": "hello@example.co.uk", "added": "2026-10-01", "category": "reloc"}
    first = build.pick(build.angles(figures), p, "angle")
    assert build.pick(build.angles(figures), p, "angle") == first


def test_every_draft_keeps_the_opt_out_line(build):
    """UK business-to-business email is lawful without prior consent only
    while the sender is named and every message offers a way out."""
    figures = {"schools": "3,627", "councils": "88", "checks": "44", "free": "29"}
    for category in CATEGORIES:
        p = {"email": "hello@example.co.uk", "added": "2026-10-01", "category": category,
             "contact": "Sam", "hook": "You cover the south west.", "link": "", "note": ""}
        text = build.body(p, figures)
        assert text.rstrip().endswith('reply with "no thanks" and I will not write again.')
        assert "ukpropertyinsight.co.uk" in text
        assert "£9.99 a month" in text and "£24.99 a quarter" in text
        assert "discount" not in text.lower(), "introductory, never discounted from an invented price"
