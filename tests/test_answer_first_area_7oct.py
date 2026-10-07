"""The area guide, answer first (7 Oct 2026, approved by Michael from
before-and-after screenshots).

M20's guide opened on one 79-word block holding four figures, the buses
section on a 57-word sentence of frequencies, first and last buses and
routes, and the council section on 66 words that ended in how to look a
band up. The four opening sentences now sit one to a line; each section
leads with its answer, keeps its source on one short line, and holds the
detail in the same words one tap away. Long tables show their first rows
and a Show all.
"""
import re

from tests.test_pages import V20_STUBS, _fresh_guide


def _section(body: str, heading: str) -> str:
    part = body[body.index(heading):]
    return part[:part.index("</section>")]


def test_the_opening_sentences_sit_one_to_a_line(client, monkeypatch):
    body = _fresh_guide(client, monkeypatch, "AB13", V20_STUBS)
    lead = re.search(r'<ul class="area-lead">(.*?)</ul>', body, re.S).group(1)
    items = re.findall(r"<li>(.*?)</li>", lead, re.S)
    assert len(items) >= 2
    assert all(item.strip().endswith(".") for item in items)


def test_buses_lead_with_the_best_stop_and_keep_the_rest_one_tap_away(client, monkeypatch):
    body = _fresh_guide(client, monkeypatch, "AB13", V20_STUBS)
    buses = _section(body, "Buses from the centre of AB13")
    first = buses.index("<p>Best-served stop: <strong>Wilmslow Road</strong>, with <strong>22.5 buses an hour</strong> on weekday daytimes.</p>")
    tap = buses.index("<summary>Evenings, Sundays and routes</summary>")
    assert first < tap
    # The evening, Sunday and route detail is the same sentence, inside the tap.
    assert buses.index("42, 43, 142") > tap
    assert re.search(r'<p class="source-line">Source: operators(&#39;|\')? timetables on the Bus Open Data Service', buses)


def test_council_tax_leads_with_the_bill_and_the_band_lookup_is_one_tap_away(client, monkeypatch):
    body = _fresh_guide(client, monkeypatch, "AB13", V20_STUBS)
    tax = _section(body, "Council tax and the council")
    first = tax[:tax.index("</p>")]
    assert "pays £2,253 for 2026-27" in first
    assert "council-tax-bands" not in first
    tap = tax.index('<details class="howto"')
    assert tax.index("gov.uk/council-tax-bands") > tap
    assert '<p class="source-line">Source: MHCLG live council tax tables and Exceptional Financial Support lists</p>' in tax


def test_the_census_section_names_its_biggest_move_first(client, monkeypatch):
    body = _fresh_guide(client, monkeypatch, "AB13", V20_STUBS)
    census = _section(body, "How AB13 has changed since 2011")
    move = census.index("The biggest move: <strong>private renting</strong>")
    assert "up 9.4 points since 2011" in census
    assert move < census.index('<details class="howto"')
    assert re.search(r'<tr class="census-change-biggest" data-keep>', census)


def test_long_tables_fold_and_only_real_tables_carry_the_mark(client, monkeypatch):
    body = _fresh_guide(client, monkeypatch, "AB13", V20_STUBS)
    assert "document.querySelectorAll('table[data-fold]')" in body
    # Every data-fold the page serves is on a real table: the script's own
    # comment once carried a literal table tag, which every page then served.
    for tag in re.findall(r"<table[^>]*data-fold[^>]*>", body):
        assert 'class="tx-table' in tag, tag
    source = open("app/templates/area_guide.html", encoding="utf-8").read()
    for noun in ("sales", "schools", "occupation groups", "measures"):
        assert f'data-fold-noun="{noun}"' in source, noun
