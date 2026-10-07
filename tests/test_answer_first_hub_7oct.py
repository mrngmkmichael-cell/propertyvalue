"""The admissions hub, answer first (7 Oct 2026, approved by Michael
from before-and-after screenshots).

Stockport's hub was 19.7 phone screens and 1,510 words, and its school
lists, the reason the page exists, started 4.2 screens down, under the
appeals and absence sections. The lists now come first, ten rows each
with the rest behind Show all, and the sections after them say their
answer before their reasons. Every row is still in the page.
"""
import pathlib
import re

from tests.test_audit_fixes_17sep import E4_BOTH, _e4_page

TEMPLATE = (pathlib.Path(__file__).resolve().parents[1] / "app" / "templates"
            / "schools_admissions_council.html").read_text(encoding="utf-8")


def test_the_school_lists_come_before_appeals_and_absence():
    lists = TEMPLATE.index("{% for phase, schools in council.by_phase %}")
    for later in ("<h2>If a place is refused</h2>", "<h2>How often children here are out of school</h2>",
                  "<h2>The entrance test here</h2>", "<h2>Reading the numbers</h2>"):
        assert lists < TEMPLATE.index(later), later


def test_ten_rows_then_show_all_and_every_row_stays_in_the_page():
    assert '<tr id="school-{{ s.urn }}"{% if loop.index > 10 %} class="hub-more"{% endif %}>' in TEMPLATE
    assert ('<button type="button" class="hub-show-all button-quiet" hidden>Show all {{ schools | length }} '
            '{{ phase | lower }} schools</button>') in TEMPLATE
    script = TEMPLATE[TEMPLATE.index("Ten rows of each list"):]
    script = script[:script.index("})();")]
    # hidden only by the script, so with none every row shows
    assert "more.forEach(function (row) { row.hidden = true; });" in script
    # sorting, or a row's own address, shows them all
    assert "th[data-sort]" in script and "hashchange" in script


def test_a_short_list_has_no_show_all(client, monkeypatch):
    body = _e4_page(client, E4_BOTH, monkeypatch)
    assert 'class="hub-more"' not in body and 'class="hub-show-all' not in body


def test_the_dek_and_reading_notes_answer_first(client, monkeypatch):
    body = _e4_page(client, E4_BOTH, monkeypatch)
    flat = " ".join(re.sub(r"<[^>]+>", " ", body).split())
    assert "How far away the last child offered a place lived, for" in flat
    assert "These are distances, not catchment areas, and they move every year." in flat
    reasons = body[body.index("<h2>Reading the numbers</h2>"):]
    reasons = reasons[:reasons.index("</section>")]
    assert '<details class="howto">' in reasons and "These are not catchment areas: most English schools have none." in reasons
