"""The report, answer first (7 Oct 2026, approved by Michael from
before-and-after screenshots).

On a phone the report opened on the "Which home is yours?" buttons for a
block of flats, and its headline card read as a column of fragments:
each reason is a button, a browser will not break a button across lines,
and in a 140px column every reason wrapped as its own block with the
semicolons on lines of their own. The free checks' pop-ups opened on
explanations of up to 132 words. Premium pop-ups are left as they were:
their method paragraphs are the locked preview chosen on 17 Sep 2026,
and Michael kept them on 7 Oct.
"""
import pathlib
import re

from tests.conftest import fake_gather

STYLE = (pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
TEMPLATE = (pathlib.Path(__file__).resolve().parents[1] / "app" / "templates" / "property.html").read_text(encoding="utf-8")


def _report(client, fake_report, **gather):
    fake_report(gather=fake_gather(**gather))
    return client.get("/property?postcode=M14+5TG", headers={"User-Agent": "Googlebot/2.1"}).text


def test_the_headline_reasons_are_a_list_and_the_card_stacks_on_a_phone(client, fake_report):
    body = _report(client, fake_report, overview={
        "score": 81, "grade": "Good", "verdict": "x",
        "reasons": {"positives": [{"text": "75% of nearby schools rated Outstanding or Good", "modal": "modal-schools"}],
                    "concerns": [{"text": "High noise levels", "modal": "modal-noise"}]},
    })
    assert re.search(r'<ul class="overview-reasons">\s*<li><button type="button" class="verdict-reason" data-modal-target="modal-schools">', body)
    assert '<ul class="overview-reasons overview-reasons-concern">' in body
    assert "Balanced against 1 thing worth checking:" in body
    phone = STYLE[STYLE.index("/* On a phone the score sits above its reasons"):]
    assert ".overview-score-card { flex-direction: column;" in phone[:400]


def test_which_home_is_one_line_with_the_homes_a_tap_away():
    picker = TEMPLATE[TEMPLATE.index('<nav class="which-home"'):TEMPLATE.index("</nav>", TEMPLATE.index('<nav class="which-home"'))]
    assert '<details class="which-home-pick">' in picker
    assert '<summary class="which-home-q"><strong>Which home is yours?</strong> <span class="which-home-open">Choose it</span></summary>' in picker
    assert picker.index("<summary") < picker.index('class="which-home-list"')


def test_the_free_checks_explain_themselves_behind_a_named_tap():
    for label in ("What Likely, Borderline and Unlikely mean", "What the badges mean", "Discounts that may apply",
                  "Before you rely on these", "About these improvements", "What Flood Re is", "What the decibels mean"):
        assert f'{{% call howto.howto("{label}") %}}' in TEMPLATE, label
    # the source stays in view where the explanation held it
    assert "{{ howto.source(\"EPC Register, the certificate's suggested improvements\") }}" in TEMPLATE
    assert '{{ howto.source("Flood Re eligibility criteria") }}' in TEMPLATE


def test_premium_previews_are_left_as_they_were():
    """Their method paragraphs are what a locked reader sees; not folded."""
    for method in ("{% set sewage_method %}", "{% set planning_decisions_method %}", "{% set clay_method %}"):
        block = TEMPLATE[TEMPLATE.index(method):TEMPLATE.index("{% endset %}", TEMPLATE.index(method))]
        assert "howto" not in block, method
