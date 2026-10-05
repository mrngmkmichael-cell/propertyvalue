"""Accessibility faults axe-core found on production (5 Oct 2026).

Run against ten page types at 1280 and 375: typeahead boxes carrying
aria-expanded without being comboboxes (critical), an unlabelled
textarea (critical), 182 nameless Google map markers on one schools
guide, maps marked role="img" while holding buttons, sideways-scrolling
tables a keyboard could not reach, and comparison tables whose corner
header was empty.
"""
import pathlib
import re

TEMPLATES = pathlib.Path(__file__).resolve().parents[1] / "app" / "templates"


def _source(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_no_comparison_table_has_a_nameless_corner_header(client):
    for path in ("/", "/premium", "/alternatives"):
        body = client.get(path).text
        assert not re.search(r"<th(\s[^>]*)?>\s*(&nbsp;)?\s*</th>", body), path
    for name in ("compare.html", "outstanding_schools.html"):
        assert not re.search(r"<th(\s[^>]*)?>\s*(&nbsp;)?\s*</th>", _source(name)), name


def test_every_typeahead_box_is_a_combobox():
    """aria-expanded is only allowed on a box that says it opens a list."""
    for name in ("_school_search.html", "school_admission.html", "index.html"):
        source = _source(name)
        for tag in re.findall(r"<input[^>]*aria-expanded[^>]*>", source, re.S):
            assert 'role="combobox"' in tag, (name, tag[:80])


def test_no_map_claims_to_be_a_picture():
    """A map holds zoom buttons and markers; role="img" hid them."""
    for name in ("school_admission.html", "schools_guide.html", "running_costs.html"):
        for tag in re.findall(r'<div[^>]*class="[^"]*(?:school-page-map|school-guide-map)[^"]*"[^>]*>', _source(name), re.S):
            assert 'role="img"' not in tag and 'role="region"' in tag, (name, tag[:80])


def test_every_google_marker_has_a_name():
    """Untitled, Google draws a marker as a button with no name."""
    for name in ("schools_guide.html", "comparables.html", "property.html", "school_admission.html",
                 "running_costs.html"):
        source = _source(name)
        for call in re.findall(r"new google\.maps\.Marker\(\{(.*?)\}\);", source, re.S):
            assert "title:" in call, (name, call[:120])


def test_the_copyable_code_boxes_have_labels():
    assert re.search(r'<textarea class="embed-code"[^>]*aria-label="Code for the', _source("school_admission.html"))
    assert re.search(r'<textarea id="embed-code-box"[^>]*aria-label=', _source("embed.html"))


def test_a_sideways_scrolling_table_gets_a_keyboard_stop(client):
    body = client.get("/").text
    script = body.split("A table that scrolls sideways has to be reachable", 1)[1].split("</script>", 1)[0]
    assert "scrollWidth > wrap.clientWidth" in script
    assert "setAttribute('tabindex', '0')" in script and "setAttribute('role', 'region')" in script
    # Only while it scrolls: a wide desktop table is not given a tab stop.
    assert "removeAttribute('tabindex')" in script
    assert "ResizeObserver" in script
    # The comparison tables scroll inside their own wrappers, not .tablewrap.
    for wrapper in (".tablewrap", ".alt-table-wrap", ".compare-table-wrap"):
        assert wrapper in script, wrapper


def test_the_premium_table_stacks_on_a_phone_with_every_cell_captioned(client, monkeypatch):
    """At 375px the Premium column sat wholly off screen (5 Oct 2026);
    stacked like the homepage's table, each cell names its column."""
    # The pricing, and the table under it, show only with billing set up.
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_not_a_real_key")
    from tests.test_ai_search_readiness import _forget_html
    _forget_html()
    body = client.get("/premium").text
    _forget_html()
    table = body.split('<table class="alt-table alt-table-stack">', 1)[1].split("</table>", 1)[0]
    rows = re.findall(r"<tr><td>.*?</tr>", table, re.S)
    assert len(rows) >= 6
    assert not re.search(r"<th(\s[^>]*)?>\s*(&nbsp;)?\s*</th>", table), "a nameless corner header"
    for row in rows:
        assert row.count('data-label="Free account"') == 1, row[:80]
        assert row.count('data-label="Premium"') == 1, row[:80]
