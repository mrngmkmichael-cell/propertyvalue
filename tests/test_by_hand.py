"""The checks by hand, counted (16 Sep 2026): the line at the report's
wall, the counted block on the premium page and the record on the
methodology page all carry the figures of one timed run, written up in
docs/audits/2026-09-16-manual-check-timing.md, and nothing else."""
import re
from pathlib import Path

from tests.conftest import fake_location
from tests.test_pages import _signed_in
from tests.test_property_page import _report

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / "docs" / "audits" / "2026-09-16-manual-check-timing.md"


def test_the_wall_on_the_free_report_says_what_the_checks_cost_by_hand(client, fake_report):
    body = _report(client, fake_report)
    banner = body.split('class="paywall-banner"', 1)[1].split("</div>\n    </div>", 1)[0]
    assert 'class="paywall-banner-by-hand"' in banner
    assert "28 websites, nine spreadsheets and three paid reports for one house" in banner
    assert "none of it compares with the next house" in banner
    assert 'href="/methodology#by-hand"' in banner


def test_the_wall_after_the_free_report_talks_about_the_next_house(client, fake_report):
    """Mirrors the wall test in test_email_verification: spend the one
    free report, then open another property as a real browser."""
    from app.services import _cache

    _signed_in(client, "by-hand@example.com")
    try:
        # Addresses of its own: another test unlocks M20 1AA for another
        # account, and the admin page counts an address two accounts share.
        fake_report(location=fake_location(postcode="M20 3AA", outcode="M20"))
        client.get("/property?postcode=M20+3AA")
        r = client.post("/property/unlock", data={"postcode": "M20 3AA", "house_number": ""}, follow_redirects=False)
        assert r.status_code == 303
        client.get(r.headers["location"])

        fake_report(location=fake_location(postcode="M1 3AA", outcode="M1"))
        _cache.set(("property_search_gather", "M1 3AA", ""), {"warm": True})
        browser = {"user-agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Safari/537.36"}
        second = client.get("/property?postcode=M1+3AA", headers=browser).text
        assert "You've used your free report." in second
        assert "The next house by hand is the same 28 websites again, and still nothing side by side." in second
        assert 'href="/methodology#by-hand"' in second
    finally:
        client.cookies.clear()


def test_the_premium_page_counts_what_the_report_saves(client, monkeypatch):
    # The counted block sits on the open-for-business page, which needs billing configured.
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_placeholder")
    monkeypatch.setenv("STRIPE_PRICE_ID_MONTHLY", "price_m")
    monkeypatch.setenv("STRIPE_PRICE_ID_QUARTERLY", "price_q")
    from app.services import _cache
    for key in [k for k in _cache._store if isinstance(k, tuple) and k and k[0] == "anon_html"]:
        _cache._evict(key)
    body = client.get("/premium").text
    assert 'id="by-hand-summary"' in body
    assert "What the report saves you, counted" in body
    facts = dict(re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", body.split('class="by-hand-facts"', 1)[1].split("</dl>", 1)[0]))
    assert facts == {
        "Websites": "28", "Steps": "about 190",
        "Files needing a spreadsheet or GIS program": "9",
        "Answers that are a colour on a map": "12",
        "Paid reports": "3", "Dead ends": "13",
        "Minutes, knowing every site": "36",
        "Houses compared at the end": "0",
    }
    assert "The 36 minutes is a floor" in body
    assert 'href="/methodology#by-hand"' in body
    # No invented afternoon anywhere on the page.
    assert "hours" not in body.split('id="by-hand-summary"', 1)[1].split("</section>", 1)[0]


def test_the_methodology_page_carries_the_check_by_check_record(client):
    body = client.get("/methodology", headers={"User-Agent": "Mozilla/5.0"}).text
    assert 'id="by-hand"' in body
    section = body.split('id="by-hand"', 1)[1].split("</section>", 1)[0]
    rows = re.findall(r"<tr><td>(.*?)</td>", section)
    assert len(rows) == 30 and rows[0] == "Sold prices" and rows[-1] == "Deprivation and seven census cards"
    assert "Totals: 28 websites, about 190 steps, 9 files" in section
    assert "35.8 minutes" in section and "13 dead ends" in section
    # The minutes in the table add up to the total, to the tenth.
    minutes = [float(m) for m in re.findall(r'<td class="num">([\d.]+)</td></tr>', section)]
    assert abs(sum(minutes) - 35.8) < 0.15, sum(minutes)


def test_every_figure_on_the_pages_is_in_the_written_record():
    record = RECORD.read_text(encoding="utf-8")
    for fact in ("35.8 minutes", "28 (27 official", "about 190", "9 (air quality", "12 cards", "3 (radon by address", "13 (four 404s", "55 Malden Hill Gardens"):
        assert fact in record, fact
    for template in ("property.html", "premium.html", "methodology.html"):
        text = (ROOT / "app" / "templates" / template).read_text(encoding="utf-8")
        assert "6 hours" not in text and "six hours" not in text.lower()
