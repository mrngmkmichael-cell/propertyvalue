"""Dark mode and the lamp, 17 Sep 2026.

Michael asked for a dark version of the site with "an animation on top
bar like light on/off" to switch. The dark theme that already existed
(August, link-only) was pure black, pure white and a condensed caps
headline face, and readers found it hard going, so dark mode is now the
same document by lamplight: warm near-black, warm off-white, the house
typefaces, and no change of layout between the two. The switch is a
pendant lamp with a pull cord, and the light spreads out of it or draws
back into it.

These pin the decisions a later edit could quietly undo: the colours stay
warm and readable, nothing reflows when the lamp is pulled, the choice is
remembered before first paint, every Google map and homepage canvas
follows a live switch, and the retired pieces stay retired."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
TEMPLATES = ROOT / "app" / "templates"


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _dark_tokens() -> dict:
    block = re.search(r"body\.theme-dark \{(.*?)\n\}", CSS, re.S).group(1)
    return dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-f]{6})\s*;", block))


# ---- The palette --------------------------------------------------------------

def test_every_dark_text_colour_clears_aa_on_every_surface():
    t = _dark_tokens()
    for ink in ("ink", "ink-soft", "ink-faint", "accent", "good", "warn", "bad"):
        for ground in ("bg", "surface", "surface-2"):
            ratio = _contrast(t[ink], t[ground])
            assert ratio >= 4.5, f"--{ink} on --{ground} is {ratio:.2f}:1"
    assert _contrast(t["accent-ink"], t["accent"]) >= 4.5


def test_dark_mode_is_lamplight_not_black_and_white():
    """Pure black and pure white were the glare readers found hard going,
    and DESIGN.md never allows a cool grey: every neutral leans warm."""
    t = _dark_tokens()
    assert t["ink"] != "#ffffff" and _contrast(t["ink"], t["bg"]) < 17
    assert _luminance(t["bg"]) > _luminance("#0a0a0a")
    for name in ("bg", "surface", "surface-2", "border", "border-strong", "ink", "ink-soft", "ink-faint"):
        r, b = int(t[name][1:3], 16), int(t[name][5:7], 16)
        assert r > b, f"--{name} {t[name]} is not warm"
    # Cards must separate from the ground (the first dark pass's fault).
    assert _contrast(t["surface"], t["bg"]) >= 1.07


def test_the_dark_theme_keeps_the_house_typefaces():
    # Comments may still tell the history; no rule may use the face.
    assert not re.search(r"font-family:[^;]*Bebas", CSS)
    assert not re.search(r"--font-display:[^;]*Bebas", CSS)
    assert "bebas-neue" not in CSS


def test_pulling_the_lamp_never_reflows_the_page():
    """Every dark rule is colour only. A size, spacing, face or border
    width that the light theme does not have would move the page when the
    lamp is pulled. Three rules add a border their light rule already
    has, or that sits inside a border-box of fixed size."""
    layout = re.compile(r"(?<![-\w])(font-size|font-family|font-weight|letter-spacing|line-height|padding[\w-]*"
                        r"|margin[\w-]*|gap|width|height|display|text-transform|grid-template[\w-]*"
                        r"|border(?:-(?:top|right|bottom|left))?(?:-width)?)\s*:")
    already_bordered = (".dashboard-card-icon", ".check-this-tag", ".map-container")
    offenders = []
    for rule in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS):
        selector = rule.group(1).split("*/")[-1].strip()
        if "body.theme-dark" not in selector or selector.startswith("@"):
            continue
        for prop in layout.findall(rule.group(2)):
            if prop.startswith("border") and selector.endswith(already_bordered):
                continue
            offenders.append(f"{selector}: {prop}")
    assert not offenders, offenders


def test_the_retired_contrast_preview_stays_retired():
    assert "theme-contrast" not in CSS


# ---- The lamp -----------------------------------------------------------------

def test_the_lamp_is_in_the_header_of_every_page(client):
    for path in ("/", "/methodology", "/premium"):
        body = client.get(path).text
        header = body.split('<header class="site-header">', 1)[1].split("</header>", 1)[0]
        assert 'id="lamp-switch"' in header, path
        assert 'aria-pressed="false"' in header and 'aria-label="Dark mode"' in header
        assert 'class="lamp-cord"' in header
        if 'id="nav-toggle"' in header:
            # Before the menu button, so on a phone the menu button is the
            # right-most thing in the header.
            assert header.index('id="lamp-switch"') < header.index('id="nav-toggle"')


def test_the_choice_is_remembered_and_applied_before_first_paint(client):
    body = client.get("/methodology").text
    first_script = body.split("<body>", 1)[1].split("</script>", 1)[0]
    assert "localStorage.getItem('uki-theme') === 'dark'" in first_script
    assert "classList.add('theme-dark')" in first_script
    assert "if (q === 'contrast') q = 'dark';" in first_script
    assert body.index("localStorage.getItem('uki-theme')") < body.index('<header class="site-header">')
    assert "sessionStorage.setItem('uki-theme'" not in body
    assert '<meta name="theme-color" content="#ffffff">' in body


def test_no_page_preloads_the_retired_headline_face(client):
    home = client.get("/").text
    assert "bebas-neue" not in home
    assert not re.search(r"font-family:[^;]*Bebas", home)


def test_the_lamp_script_switches_remembers_and_announces(client):
    body = client.get("/premium").text
    script = body.split("var lamp = document.getElementById('lamp-switch');", 1)[1].split("})();", 1)[0]
    assert "localStorage.setItem('uki-theme'" in script
    assert "new CustomEvent('uki:themechange'" in script
    assert "prefers-reduced-motion: reduce" in script
    assert "document.startViewTransition" in script
    assert "aria-pressed" in script
    # The durations here and the afterglow delays in the stylesheet are one
    # timing: the bulb goes out only after the light has drawn back in.
    assert "duration: dark ? 640 : 720" in script
    assert ":root.lamp-going-dark body.theme-dark .lamp-bulb { transition: fill 420ms cubic-bezier(0.2, 0, 0, 1) 470ms; }" in CSS


def test_reduced_motion_keeps_the_cord_still():
    reduced = CSS.split(".lamp-switch:hover .lamp-cord", 1)[1]
    assert "@media (prefers-reduced-motion: reduce)" in CSS.split("/* Phones: logo on the left", 1)[0].split("The light pool", 1)[1]
    assert ".lamp-switch.is-pulled .lamp-cord { animation: none; transition: none; transform: none; }" in reduced


# ---- Things that paint their colours once -------------------------------------

def test_every_google_map_follows_the_lamp():
    style = (TEMPLATES / "_map_style.html").read_text(encoding="utf-8")
    assert "window.watchMapTheme = function (map)" in style
    assert "addEventListener('uki:themechange'" in style
    maps = [p for p in TEMPLATES.glob("*.html") if "new google.maps.Map(" in p.read_text(encoding="utf-8")]
    assert len(maps) >= 5
    for page in maps:
        text = page.read_text(encoding="utf-8")
        assert text.count("new google.maps.Map(") == text.count("window.watchMapTheme(map)"), page.name


def test_the_homepage_canvases_repaint_when_the_lamp_is_pulled():
    home = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    # One canvas since 17 Sep 2026, when the orbit ring and its listener
    # came off the homepage: the orb behind the scroll-built report.
    assert home.count("addEventListener('uki:themechange'") == 1
    assert "document.addEventListener('uki:themechange', orbColours);" in home
    assert "#d4a95f" not in home and "#171717" not in home
