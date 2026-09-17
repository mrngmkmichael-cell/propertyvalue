"""Ofsted grades that can be read, and that agree with the map, 17 Sep 2026.

The grade badges carried white 12px text on Tailwind's greens and amber,
which measured 3.77:1 for Outstanding, 2.54 for Good and 2.94 for
Requires improvement, where text that size needs 4.5.
scripts/audit_contrast.js found them during the dark mode work. They now
take the colours the school pins and the map key already used, so a
badge and the pin beside it say the same thing in both themes.

These work the ratios out from style.css itself, so a colour edit that
fails one cannot ship, and they read the pins' colours out of the
templates, so the badges and the map cannot drift apart again."""
import re
from pathlib import Path

from tests.test_dark_mode import CSS, _contrast, _dark_tokens

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"

GRADES = {1: "Outstanding", 2: "Good", 3: "Requires improvement", 4: "Inadequate", 5: "Report card"}
BADGE_CLASSES = {"ofsted-badge"} | {f"ofsted-{n}" for n in GRADES}
THEMES = ("light", "dark")
GROUNDS = ("bg", "surface", "surface-2")
COLOUR_DECLARATION = re.compile(r"(?<![-\w])(color|background(?:-color)?)\s*:\s*([^;]+);")
UNCOMMENTED = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)


def _tokens(theme: str) -> dict:
    light = {}
    for block in re.findall(r"^:root \{(.*?)\}", UNCOMMENTED, re.S | re.M):
        light.update(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-f]{6})\s*;", block))
    return light if theme == "light" else {**light, **_dark_tokens()}


def _rules():
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", UNCOMMENTED):
        yield [s.strip() for s in rule.group(1).split(",")], rule.group(2)


def _in_theme(selector: str, theme: str):
    """The selector without its theme prefix, or None when it only
    applies in the other theme."""
    for prefix, only in (("body.theme-dark ", "dark"), ("body:not(.theme-dark) ", "light")):
        if selector.startswith(prefix):
            return selector[len(prefix):].strip() if theme == only else None
    return selector


def _hex(value: str, tokens: dict) -> str:
    value = value.replace("!important", "").strip().lower()
    token = re.fullmatch(r"var\(--([a-z0-9-]+)\)", value)
    if token:
        assert token.group(1) in tokens, f"--{token.group(1)} is not a solid hex token"
        value = tokens[token.group(1)]
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        value = "#" + "".join(c * 2 for c in value[1:])
    assert re.fullmatch(r"#[0-9a-f]{6}", value), f"{value!r}: this guard measures a hex colour or a token"
    return value


def _badge_colours(theme: str) -> dict:
    """Every text colour and every background any rule can give each
    grade's badge in one theme. A rule scoped to a context counts too,
    and every pairing must pass, so a failing colour cannot slip in
    somewhere quieter."""
    found = {n: {"text": [], "ground": []} for n in GRADES}
    for selectors, body in _rules():
        for selector in selectors:
            selector = _in_theme(selector, theme)
            if selector is None:
                continue
            classes = set(re.findall(r"\.([\w-]+)", re.split(r"[\s>+~]+", selector)[-1])) & BADGE_CLASSES
            if not classes:
                continue
            grades = [int(c.rsplit("-", 1)[1]) for c in classes if c != "ofsted-badge"] or list(GRADES)
            for prop, value in COLOUR_DECLARATION.findall(body):
                for n in grades:
                    found[n]["text" if prop == "color" else "ground"].append(value)
    return found


def test_every_ofsted_badge_clears_aa_in_both_themes():
    for theme in THEMES:
        tokens = _tokens(theme)
        for n, colours in _badge_colours(theme).items():
            assert colours["text"] and colours["ground"], f"{GRADES[n]}: no colours found for the badge"
            for text in colours["text"]:
                for ground in colours["ground"]:
                    ratio = _contrast(_hex(text, tokens), _hex(ground, tokens))
                    assert ratio >= 4.5, f"{GRADES[n]} badge, {theme}: {text} on {ground} is {ratio:.2f}:1"


def test_badges_pins_and_the_map_key_share_one_set_of_colours():
    """A reader matches a badge in the table to a pin on the map by its
    colour. Both map branches of both pages (Google in production,
    Leaflet in dev) and the key use the badge's colour for every grade,
    the report card included: its pins were drawn grey under a key that
    said blue."""
    tokens = _tokens("light")
    badge = {}
    for n, label in GRADES.items():
        grounds = [v for selectors, body in _rules() if f".ofsted-{n}" in selectors
                   for prop, v in COLOUR_DECLARATION.findall(body) if prop != "color"]
        assert grounds, f"no plain .ofsted-{n} background"
        badge[label] = _hex(grounds[-1], tokens)
    for page in ("schools_guide.html", "property.html"):
        text = (TEMPLATES / page).read_text(encoding="utf-8")
        maps = re.findall(r"const ratingColors = \{(.*?)\};", text, re.S)
        assert len(maps) == 2, f"{page}: expected a Google branch and a Leaflet branch"
        for block in maps:
            assert dict(re.findall(r"'([^']+)':\s*'(#[0-9a-f]{6})'", block)) == badge, page
    guide = (TEMPLATES / "schools_guide.html").read_text(encoding="utf-8")
    key = {label.strip(): colour for colour, label in
           re.findall(r'<span class="key-dot" style="background:(#[0-9a-f]{6})"></span>([^<]+)', guide)}
    for label, colour in badge.items():
        assert key.get(label) == colour, f"map key for {label}"


def test_report_card_grade_words_clear_aa_on_every_surface_in_both_themes():
    """A school's report card colours each area's grade. Two of its three
    colours were light theme values, 2.72 and 2.90:1 on a dark card."""
    for theme in THEMES:
        tokens = _tokens(theme)
        colours = []
        for selectors, body in _rules():
            scoped = [_in_theme(s, theme) for s in selectors]
            if all(s and re.fullmatch(r"\.ofsted-card-[a-z-]+", s) for s in scoped):
                colours += [v for prop, v in COLOUR_DECLARATION.findall(body) if prop == "color"]
        assert len(colours) >= 3, theme
        for colour in colours:
            for ground in GROUNDS:
                ratio = _contrast(_hex(colour, tokens), tokens[ground])
                assert ratio >= 4.5, f"{colour} on --{ground}, {theme}: {ratio:.2f}:1"


def test_no_bare_rule_styles_a_rating_chip_class():
    """rating_css in schools_db.py puts one class on the schools guide's
    chip and on the report's expandable <details> list, so a bare rule
    for it paints the whole list. .rating-card did, in solid blue."""
    source = (ROOT / "app" / "services" / "schools_db.py").read_text(encoding="utf-8")
    classes = set(re.findall(r'"(rating-[a-z]+)"', source.split("rating_css = {", 1)[1].split("}", 1)[0]))
    assert "rating-card" in classes
    bare = [s for selectors, _ in _rules() for s in selectors
            if any(_in_theme(s, theme) and _in_theme(s, theme).lstrip(".") in classes for theme in THEMES)]
    assert not bare, bare
