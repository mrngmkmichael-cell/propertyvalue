"""A secondary's headline result when Progress 8 was not published (1 Oct 2026).

The Department for Education published no Progress 8 for 2024/25, so a
headline read from Progress 8 alone left every secondary in England blank.
"""
import pathlib
import re
from types import SimpleNamespace

from app.services.schools_db import _ks4_headline

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "templates"


def _row(year, p8, a8, g5=50.0):
    return SimpleNamespace(academic_year=year, progress8_score=p8, attainment8_avg=a8,
                           grade5_english_maths_pct=g5)


def test_progress8_leads_while_published():
    rows = [_row("2022/23", 0.2, 50.1), _row("2023/24", 0.3, 51.0)]
    head = _ks4_headline(rows[-1], rows)
    assert head["headline_label"] == "Progress 8"
    assert head["headline_value"] == 0.3
    assert head["progress8_unpublished"] is False
    assert [t["headline_value"] for t in head["trend"]] == [0.2, 0.3]


def test_attainment8_when_newest_year_has_no_progress8():
    rows = [_row("2023/24", 0.45, 50.2), _row("2024/25", None, 51.5)]
    head = _ks4_headline(rows[-1], rows)
    assert head["headline_label"] == "Attainment 8"
    assert head["headline_value"] == 51.5
    assert head["progress8_unpublished"] is True
    # The trend stays on one scale: never a Progress 8 beside an Attainment 8.
    assert [t["headline_value"] for t in head["trend"]] == [50.2, 51.5]


def test_nothing_published_stays_progress8_and_empty():
    rows = [_row("2024/25", None, None)]
    head = _ks4_headline(rows[-1], rows)
    assert head["headline_label"] == "Progress 8"
    assert head["headline_value"] is None


def test_no_template_puts_a_percent_sign_on_attainment8():
    # The percent rule used to be "anything but Progress 8", which would
    # have printed "51.5%" for an Attainment 8 score.
    for path in TEMPLATES.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"""!=\s*['"]Progress 8['"]""", text), path.name
