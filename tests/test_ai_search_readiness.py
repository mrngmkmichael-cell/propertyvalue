"""Group A of the 16 Sep 2026 AI search readiness check
(docs/audits/2026-09-16-geo-analysis.md): page dates that are the stored
payload's own time and never the render time, the brand's real public
profiles as sameAs, the admissions hub's questions, figure answers at
the top of the hub pages, and the council tax trend chart."""
import datetime
import json
import re
import time
from pathlib import Path

from tests.conftest import fake_location

ROOT = Path(__file__).resolve().parent.parent

HISTORY = [
    {"label": "2021-22", "band_d": 2057.0, "rise": 5.8},
    {"label": "2022-23", "band_d": 2123.0, "rise": 3.2},
    {"label": "2023-24", "band_d": 2248.0, "rise": 5.9},
    {"label": "2024-25", "band_d": 2375.0, "rise": 5.6},
    {"label": "2025-26", "band_d": 2489.0, "rise": 4.8},
    {"label": "2026-27", "band_d": 2609.0, "rise": 4.8},
]

AREA_PAYLOAD = {
    "has_data": True,
    "local_sales": {"enough_for_median": True, "median": 412500, "count": 63, "low": 150000, "high": 900000},
    "hpi": {"local_authority": {"name": "Kingston upon Thames", "annual_change_pct": 3.3, "average_price": 591555, "period": "2026-07"}},
    "landscape": {"good_or_better_pct": 91, "total_schools": 85, "radius_miles": 3},
    "flood_zone": {"zone": 1, "label": "Zone 1 (low probability)"},
    "finance": {"name": "Kingston upon Thames", "latest_label": "2026-27", "history": HISTORY},
    "crime": {"total": 230, "month": "2026-07", "by_category": [{"category": "Violence and sexual offences"}]},
}


def _forget_html():
    from app.services import _cache
    for key in [k for k in _cache._store if isinstance(k, tuple) and k and k[0] == "anon_html"]:
        _cache._evict(key)


def _label(epoch: float) -> tuple[str, str]:
    day = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).date()
    return day.isoformat(), f"{day.day} {day.strftime('%B %Y')}"


def _site_graph(body: str) -> list[dict]:
    raw = re.search(r'<script type="application/ld\+json">\s*(\{\s*"@context": "https://schema.org",\s*"@graph".*?)</script>', body, re.S)
    return json.loads(raw.group(1))["@graph"]


# ---- 1. Dates ----------------------------------------------------------------

def test_a_page_date_is_the_stored_time_never_the_render_time():
    from app import main as app_main
    from app.services import _cache

    key = ("test_page_date", 1)
    ctx: dict = {}
    app_main._set_page_date(ctx, key)
    assert "page_modified" not in ctx, "nothing stored means no date, not today's"

    three_days_ago = time.time() - 3 * 86400
    _cache._put(key, three_days_ago, {"x": 1})
    try:
        app_main._set_page_date(ctx, key)
        iso, label = _label(three_days_ago)
        assert ctx["page_modified"] == iso
        assert ctx["page_modified_label"] == label
    finally:
        _cache._evict(key)


def test_an_area_guide_says_when_its_figures_were_gathered(client, monkeypatch):
    from app import main as app_main
    from app.services import _cache

    async def _resolve(outcode):
        return fake_location(postcode=f"{outcode} 2AA", outcode=outcode), True

    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    key = ("area_guide", app_main.AREA_GUIDE_PAYLOAD_VERSION, "AB12")
    gathered = time.time() - 2 * 86400
    _cache._put(key, gathered, dict(AREA_PAYLOAD))
    _forget_html()
    try:
        body = client.get("/area/AB12").text
    finally:
        _cache._evict(key)

    iso, label = _label(gathered)
    assert f'<p class="page-date">Figures gathered {label}, each from the source named in its section.</p>' in body
    webpage = [n for n in _site_graph(body) if n.get("@type") == "WebPage"]
    assert len(webpage) == 1 and webpage[0]["dateModified"] == iso
    assert webpage[0]["url"].endswith("/area/AB12")


def test_a_page_with_no_stored_payload_carries_no_date(client):
    body = client.get("/privacy").text
    assert 'class="page-date"' not in body
    assert not [n for n in _site_graph(body) if n.get("@type") == "WebPage"]


# ---- 5. The brand's real profiles ---------------------------------------------

def test_the_organisation_names_its_real_public_profiles(client):
    from app.main import EXTENSION_STORE_URL, TRUSTPILOT

    body = client.get("/privacy").text
    org = next(n for n in _site_graph(body) if n.get("@type") == "Organization")
    assert org["sameAs"] == [EXTENSION_STORE_URL, TRUSTPILOT["profile_url"]]
    # Trustpilot's brand rules: a plain link, never a score or a count.
    for banned in ("aggregateRating", "ratingValue", "reviewCount"):
        assert banned not in body


# ---- 2. The admissions hub's questions ----------------------------------------

STATS = {
    "total": 3627, "council_count": 88, "median_miles": 1.28, "under_a_mile": 1498, "over_five": 399,
    "councils": [],
    "tightest": [{"name": "Pebble Brook Primary School", "town": "Crewe", "authority": "Cheshire East",
                  "miles": 0.02, "academic_year": "2025/26", "urn": 1, "slug": "pebble-brook"}],
}


def test_the_admissions_hub_answers_the_questions_people_ask(client, monkeypatch):
    from app import main as app_main

    monkeypatch.setattr(app_main.schools_db, "tightest_catchments", lambda: STATS)
    _forget_html()
    body = client.get("/schools/admissions").text

    # The opening answer carries the national figures.
    dek = re.search(r'<p class="dek">(.*?)</p>', body, re.S).group(1)
    dek = re.sub(r"\s+", " ", dek)
    assert "the middle school admitted from 1.28 miles" in dek
    assert "the last place at 1,498 went to a child living under a mile away" in dek
    assert "399 reached five miles or more" in dek

    # The questions, on the page and as FAQPage structured data.
    assert '<section class="report-section" id="admissions-faq">' in body
    assert "<h2>How do I check one address?</h2>" in body
    faq = next(json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
               if '"FAQPage"' in s)
    answers = {q["name"]: q["acceptedAnswer"]["text"] for q in faq["mainEntity"]}
    assert list(answers) == [
        "What is the last distance offered?",
        "How far away can you live and still get a school place?",
        "Which school admitted from the shortest distance?",
        "Does living inside the distance guarantee a place?",
        "Why is my council not listed?",
    ]
    assert "the middle one admitted from 1.28 miles" in answers["How far away can you live and still get a school place?"]
    assert answers["Which school admitted from the shortest distance?"] == (
        "Pebble Brook Primary School in Crewe, in Cheshire East: its last place went to a child living "
        "0.02 miles away, about 32 metres in 2025/26, according to the council's published figures."
    )


def test_the_hub_answers_leave_out_what_the_data_does_not_say():
    from app import main as app_main

    faqs = dict(app_main._admissions_hub_faqs({
        "tightest": [{"name": "Oak School", "town": "", "authority": "Somewhere", "miles": 1.5, "academic_year": "varies"}],
    }))
    assert "How far away can you live and still get a school place?" not in faqs
    tight = faqs["Which school admitted from the shortest distance?"]
    assert tight == ("Oak School, in Somewhere: its last place went to a child living 1.5 miles away, "
                     "according to the council's published figures.")
    bare = dict(app_main._admissions_hub_faqs({}))
    assert "Which school admitted from the shortest distance?" not in bare
    assert "What is the last distance offered?" in bare


# ---- 3. Figure answers at the top of the hub pages ----------------------------

def test_the_market_report_opens_on_its_strongest_and_weakest_city(client):
    from app.services import _cache

    key = ("market_report", 1)
    payload = {"generated_date": "16 September 2026", "areas": [
        {"name": "Leeds", "average_price": 250000, "annual_change_pct": 3.2, "period": "2026-07"},
        {"name": "Bristol", "average_price": 340000, "annual_change_pct": 0.4, "period": "2026-07"},
        {"name": "York", "average_price": 300000, "annual_change_pct": -1.4, "period": "2026-07"},
    ]}
    _cache._put(key, time.time(), payload)
    _forget_html()
    try:
        body = client.get("/market-report").text
    finally:
        _cache._evict(key)
    dek = re.sub(r"\s+", " ", re.search(r'<p class="dek">(.*?)</p>', body, re.S).group(1))
    assert "Prices rose over the year in 2 of the 3 cities." in dek
    assert "The strongest was Leeds at +3.2%, and the weakest York at -1.4%." in dek


def test_the_council_tax_table_opens_on_englands_range(client):
    body = client.get("/running-costs/council-tax").text
    dek = re.sub(r"\s+", " ", re.search(r'<p class="dek">(.*?)</p>', body, re.S).group(1))
    match = re.search(r"Across England's (\d+) billing authorities the middle Band D bill for \S+ is "
                      r"£([\d,]+), from £([\d,]+) at [^,]+? to £([\d,]+) at .+?\.", dek)
    assert match, dek
    count, median, low, high = (int(g.replace(",", "")) for g in match.groups())
    assert count > 250 and low < median < high


def test_the_areas_page_says_what_every_guide_holds(client):
    body = client.get("/areas").text
    dek = re.sub(r"\s+", " ", re.search(r'<p class="dek">(.*?)</p>', body, re.S).group(1))
    assert re.match(r"\d{1,3}(,\d{3})+ postcode districts across \d+ regions and nations, one guide each\.", dek)
    for source in ("HM Land Registry", "UK House Price Index", "Ofsted", "Environment Agency", "MHCLG", "Police.uk"):
        assert source in dek, source


# ---- 4. The council tax trend chart -------------------------------------------

def test_the_trend_chart_starts_from_zero_and_needs_a_real_trend():
    from app.main import _trend_chart

    rows = [{"value": h["band_d"], "period": h["label"], "note": ""} for h in HISTORY]
    chart = _trend_chart(rows)
    assert [t["label"] for t in chart["ticks"]] == ["£0", "£1,000", "£2,000", "£3,000"]
    # The wash starts and ends on the zero baseline, so the slope is the real change.
    assert chart["ticks"][0]["y"] == chart["baseline"]
    area = chart["area"].split()
    assert area[0].endswith(f",{chart['baseline']}") and area[-1].endswith(f",{chart['baseline']}")
    assert chart["end"]["value"] == "£2,609" and chart["end"]["period"] == "2026-27"
    assert chart["first"]["period"] == "2021-22"
    # Two points, or points with no value, are not a trend.
    assert _trend_chart(rows[:2]) is None
    assert _trend_chart([{"value": None, "period": "x"}] * 5) is None


def test_the_area_guide_draws_the_band_d_trend_above_its_table(client, monkeypatch):
    from app import main as app_main
    from app.services import _cache

    async def _resolve(outcode):
        return fake_location(postcode=f"{outcode} 2AA", outcode=outcode), True

    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    key = ("area_guide", app_main.AREA_GUIDE_PAYLOAD_VERSION, "AB12")
    _cache._put(key, time.time(), dict(AREA_PAYLOAD))
    _forget_html()
    try:
        body = client.get("/area/AB12").text
    finally:
        _cache._evict(key)

    section = body.split("Council tax and the council's finances", 1)[1].split("</section>", 1)[0]
    assert section.index('class="trend-chart"') < section.index('class="tx-table finance-table"')
    assert ("Band D council tax at Kingston upon Thames by year, from £2,057 in 2021-22 to "
            "£2,609 in 2026-27. Use the left and right arrow keys to read each year.") in section
    points = json.loads(re.search(r"data-trend='(.*?)'", section).group(1))
    assert [p["period"] for p in points] == [h["label"] for h in HISTORY]
    assert points[-1]["note"] == "up 4.8% on the year before"
    # The hover script ships once, and text goes in with textContent.
    assert body.count("document.querySelectorAll('.trend-chart')") == 1
    script = body.split("document.querySelectorAll('.trend-chart')", 1)[1][:3000]
    assert "textContent" in script and "innerHTML" not in script


def test_the_trend_chart_follows_the_chart_rules_in_css():
    css = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert ".trend-line { fill: none; stroke: var(--accent); stroke-width: 2;" in css
    assert ".trend-area { fill: var(--accent); fill-opacity: 0.1;" in css
    # Text wears text tokens, never the series colour.
    assert ".trend-period { fill: var(--ink-soft);" in css
    assert ".trend-end-label { fill: var(--ink);" in css
    still = re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?)\n\}", css, re.S)
    assert any(".trend-tip { transition: none; }" in block for block in still)
    printed = re.findall(r"@media print \{(.*?)\n\}", css, re.S)
    assert any(".trend-cross, .trend-focus, .trend-tip { display: none; }" in block for block in printed)
