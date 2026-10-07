"""What happens when a place is refused (1 Oct 2026).

The Department for Education publishes admission appeals by council,
never by school: appeals against community schools reach it as council
totals and the school-level returns are not released. Every page that
shows a figure here therefore names the council and says the school's
own rate does not exist, and these tests fail if that sentence ever goes
missing, because the quiet version of this feature would be a lie.
"""
import re

from app.services import appeals
from tests.test_pages import _seed_admission_school, sitemap_text

SCHOOL_PATH = "/school/990002/riverside-academy"


def flat(text):
    """One space between words, so a sentence that wraps in the template
    is still the sentence the test is looking for."""
    return " ".join(text.split())


def test_the_release_is_loaded_and_recent():
    source = appeals.source()
    assert source["release"].startswith("Admission appeals in England")
    assert source["url"].startswith("https://explore-education-statistics.service.gov.uk/")
    assert len(source["years"]) >= 3 and source["year"] == source["years"][-1]
    assert int(source["year"]) >= 2025


def test_a_council_reads_as_the_release_published_it():
    kent = appeals.for_council("Kent")
    assert kent and kent["council"] == "Kent"
    secondary = kent["secondary"]
    # The share allowed is of appeals heard, not of appeals lodged.
    assert secondary["won_pct"] == round(100 * secondary["won"] / secondary["heard"], 1)
    assert secondary["heard"] <= secondary["lodged"], "fewer are heard than lodged; some are withdrawn"
    phases = {row["phase"] for row in kent["phases"]}
    assert {"Secondary", "Primary", "Primary (infant classes)"} <= phases
    assert kent["england"]["heard"] > secondary["heard"]
    assert len(kent["history"]) >= 3, "the five-year run is what makes one year readable"


def test_a_council_the_release_does_not_carry_is_none():
    assert appeals.for_council("Narnia") is None
    assert appeals.for_council("") is None


def test_the_league_puts_readable_rates_first_and_keeps_the_counts():
    rows = appeals.league()
    assert len(rows) > 100
    readable = [r for r in rows if r["readable"]]
    assert all(r["heard"] >= appeals.READABLE_MIN_HEARD for r in readable)
    assert [r["readable"] for r in rows] == sorted((r["readable"] for r in rows), reverse=True)
    pcts = [r["won_pct"] for r in readable]
    assert pcts == sorted(pcts, reverse=True), "best first, so the page can read top down"
    assert all(r["heard"] and r["won"] is not None for r in rows), "never a rate without its counts"


def test_the_page_leads_with_england_and_shows_the_spread(client):
    body = flat(client.get("/schools/appeals").text)
    summary = appeals.summary()
    assert f"{summary['england']['won_pct']}%" in body
    assert f"{summary['england']['heard']:,}" in body
    assert summary["best"]["council"] in body and summary["worst"]["council"] in body
    # Lodged, heard and allowed are three different numbers, and the page
    # says which it divided by.
    assert "Lodged" in body and "Heard" in body and "Allowed" in body
    assert "Share allowed" in body


def test_the_page_says_whose_figures_these_are(client):
    body = flat(client.get("/schools/appeals").text)
    assert "not published school by school" in body
    assert "school-level returns" in body or "does not publish the school-level" in body
    assert "infant class" in body, "the narrow legal test explains the low primary rates"
    assert "explore-education-statistics.service.gov.uk" in body
    assert '"Dataset"' in body and '"FAQPage"' in body


def test_the_page_never_offers_a_rate_for_one_school(client):
    body = client.get("/schools/appeals").text.lower()
    for claim in ("this school's appeal", "appeal success rate for this school",
                  "chance of winning an appeal at this school"):
        assert claim not in body


def test_the_council_hub_carries_its_own_council_and_says_so(client):
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = flat(client.get("/schools/admissions/manchester").text)
    manchester = appeals.for_council("Manchester")
    assert manchester, "Manchester is in the release"
    assert "If a place is refused" in body
    assert f"{manchester['secondary']['won_pct']}%" in body
    assert "appeals are not published school by school" in body
    assert 'href="/schools/appeals"' in body


def test_a_school_page_carries_its_council_figure_labelled_as_the_council(client):
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = flat(client.get(SCHOOL_PATH).text)
    assert "If the answer is no" in body
    # Said in fewer words since 7 Oct 2026 (answer first), same meaning.
    assert "counted for all the council's schools together" in body
    assert 'href="/schools/appeals"' in body


def test_the_page_is_advertised_and_reachable(client):
    assert "/schools/appeals" in sitemap_text(client)
    assert 'href="/schools/appeals"' in client.get("/schools/admissions").text
    assert client.get("/schools/appeals").status_code == 200


def test_every_figure_on_the_page_is_one_the_release_holds(client):
    """No rounded-to-nothing or invented percentages: each share shown in
    the main table matches the counts in its own row."""
    body = client.get("/schools/appeals").text
    rows = re.findall(r'data-value="(\d+)">[\d,&;a-z]+</td>\s*'          # lodged
                      r'<td class="num" data-value="(\d+)">[\d,]+</td>\s*'  # heard
                      r'<td class="num" data-value="(\d+)">[\d,]+</td>\s*'  # allowed
                      r'<td class="num" data-value="([\d.]+)">', body, re.S)
    assert rows, "the table rendered"
    for _lodged, heard, won, pct in rows[:25]:
        assert abs(round(100 * int(won) / int(heard), 1) - float(pct)) < 0.05
