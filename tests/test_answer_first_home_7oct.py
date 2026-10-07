"""The home page, answer first (7 Oct 2026, approved by Michael from
before-and-after screenshots).

The dek carried two sentences the headline and the three cards under the
box already say, and with the blank above the eyebrow they put the Search
button below the first screen of a 375px phone and the postcode box below
a 360px one's. The trust section said its four sources in a paragraph and
then repeated the accuracy strip directly below and the hero's Schools
card; it now lists the sources one to a line and keeps both links.
"""
import re

from app import main as app_main


def _home(client):
    from tests.test_ai_search_readiness import _forget_html
    _forget_html()
    return client.get("/").text


def test_the_dek_states_the_answer_and_the_offer_and_nothing_the_headline_says(client):
    body = _home(client)
    dek = " ".join(body.split('<p class="lx-hero-dek">', 1)[1].split("</p>", 1)[0].split())
    assert dek == "Forty-six checks on any UK address, each from the body that published it. " + app_main.OFFER_SENTENCE
    assert "Whether the house gets a child into the school" not in body


def test_the_trust_section_lists_its_sources_and_keeps_every_link(client):
    body = _home(client)
    about = body.split('<section class="lx-section lx-about" id="about">', 1)[1].split("</section>", 1)[0]
    pairs = re.findall(r"<div><dt>(.*?)</dt><dd>(.*?)</dd></div>", about)
    assert pairs == [("Sold prices", "HM Land Registry"), ("Flood zones", "Environment Agency"),
                     ("School ratings", "Ofsted"), ("Subsidence", "British Geological Survey")]
    assert "Where a source does not cover an address, the report says so" in " ".join(about.split())
    for href in ("/schools/admissions", "/schools/tightest-catchments", "/schools/catchment-house-prices",
                 "/schools/independent", "/schools/how-admissions-work"):
        assert f'href="{href}"' in about, href
    # Said once, in the strip directly below, which carries the counts.
    assert "School sites show you the school" not in body
    strip = body.split('<section class="accuracy-strip"', 1)[1].split("</section>", 1)[0]
    assert 'href="/accuracy"' in strip and "including every time we were wrong" in strip


def test_the_phone_hero_leaves_half_the_blank_above_the_eyebrow():
    css = open("app/static/css/style.css", encoding="utf-8").read()
    phone = css[css.index("@media (max-width: 560px) {\n    .lx-check-grid"):]
    phone = phone[:phone.index("\n}")]
    assert ".lx-hero-inner { padding-top: 2.5rem; }" in phone
