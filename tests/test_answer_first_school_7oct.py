"""The school page, answer first (7 Oct 2026).

Michael asked whether the site's wording put people off reading. The
critique measured the school page at 1,209 words on a phone with six
paragraphs over 40 words, and he chose "answer first, the reasons one
tap away". Each section now opens with its answer; how it was measured,
what it does not mean and the method sit under a closed "How to read
this", with the source on one line. Nothing was deleted: these tests
hold that the facts are still in the page, only moved.
"""
import pathlib
import re

from tests.test_audit_fixes_17sep import _e3_page

STYLE = (pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")


def _verdict_section(body):
    start = body.index("<h2>Will an address get in?</h2>")
    return body[start:body.index("</section>", start)]


def test_a_checked_answer_is_not_preceded_by_the_same_figure_in_prose(client):
    section = _verdict_section(_e3_page(client, "?check=CA3+9AA"))
    assert "the furthest child offered a place lived" not in section       # the verdict says it
    assert "Not a guarantee: the distance moves every year." in section
    assert "This is not a catchment area." in section                       # the honest headline stays in view


def test_the_reasons_are_one_tap_away_and_closed_by_default(client):
    section = _verdict_section(_e3_page(client, "?check=CA3+9AA"))
    howto = re.search(r'<details class="howto"[^>]*>\s*<summary>How to read this</summary>(.*?)</details>', section, re.S)
    assert howto, "no How to read this in the answer section"
    assert " open" not in section[section.index('<details class="howto"'):][:40]
    reasons = " ".join(howto.group(1).split())
    for kept in ("Most English schools do not have a catchment area.",
                 "Distances are measured from the postcode's centre, not a front door",
                 "come before distance, so a school can fill before distance counts at all."):
        assert kept in reasons, kept
    assert '<p class="source-line">Source: Cumberland, published admission distances, 2025/26</p>' in section


def test_an_unchecked_page_still_says_the_figure_in_one_sentence(client):
    section = _verdict_section(_e3_page(client))
    flat = " ".join(re.sub(r"<[^>]+>", " ", section).split())
    assert "In 2025/26, the furthest child offered a place lived 1.2 miles away." in flat


def test_the_badge_code_waits_behind_a_tap(client):
    body = _e3_page(client)
    badge = body[body.index("figure on your own site</h2>"):]
    badge = badge[:badge.index("</section>")]
    assert '<summary>Show the badge and its code</summary>' in badge
    assert badge.index("<summary>") < badge.index('<textarea class="embed-code"')


def test_prose_keeps_a_readable_measure_and_links_are_underlined_at_rest():
    block = STYLE[STYLE.index(".report-section > p,"):]
    block = block[:block.index("}")]
    assert ".howto-body p" in block and "max-width: 70ch;" in block
    link = STYLE[STYLE.index("main p a:not([class]), .faq-item p a, .landing-subheading a {"):]
    link = link[:link.index("}")]
    assert "background-size: 0% 1px, 100% 1px;" in link


def test_the_disclosure_is_reachable_and_respects_reduced_motion():
    assert ".howto > summary:focus-visible" in STYLE
    assert "min-height: 44px;" in STYLE[STYLE.index(".howto > summary {"):STYLE.index(".howto > summary::-webkit-details-marker")]
    reduced = STYLE[STYLE.index(".howto[open] > summary::before"):]
    assert "@media (prefers-reduced-motion: reduce)" in reduced[:900]
