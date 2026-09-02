"""Per-property share cards.

A link to a report pasted into WhatsApp or a group chat previously
showed the same generic image every time, so a shared report looked
identical to the homepage and carried none of what the sender wanted to
show. This draws a 1200x630 card carrying the address's own score and
headline figures, in the site's own type and palette.

Deliberately built only from data already gathered and cached: an image
request must never be able to trigger the full ~28-service gather, or a
crawler hitting a handful of links would do real work on our behalf.
When nothing is cached, the card still names the real postcode and
district, which is a good deal more specific than the generic default.

Pillow draws text through FreeType, which cannot read woff2, so the
faces here are the TTF conversions committed alongside the web fonts
(see scripts/build_og_fonts.py).
"""
import functools
import io
import pathlib

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
PAD = 72

FONT_DIR = pathlib.Path(__file__).resolve().parent.parent / "static" / "fonts"
SANS = FONT_DIR / "instrument-sans-latin-normal.ttf"
MONO = FONT_DIR / "jetbrains-mono-labels.ttf"

# The light theme's tokens, which is what the site now ships as default.
BG = (250, 249, 246)
SURFACE = (255, 255, 255)
INK = (28, 23, 20)
INK_SOFT = (87, 80, 74)
INK_FAINT = (140, 132, 124)
BORDER = (229, 225, 216)
ACCENT = (43, 76, 140)
GOOD = (47, 107, 79)
WARN = (166, 124, 46)
BAD = (168, 58, 50)


@functools.lru_cache(maxsize=32)
def _font(path: str, size: int, weight: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(path, size)
    try:
        font.set_variation_by_axes([weight])
    except Exception:  # noqa: BLE001 - a static build of the face is fine
        pass
    return font


def sans(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    return _font(str(SANS), size, weight)


def mono(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    return _font(str(MONO), size, weight)


def is_available() -> bool:
    return SANS.exists() and MONO.exists()


def _tracked(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, tracking: float = 0.0) -> None:
    """Letter-spaced text. Pillow has no tracking, and the site's mono
    labels are set wide enough that drawing them tight looks wrong."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def _score_colour(score: int) -> tuple:
    if score >= 70:
        return GOOD
    if score >= 50:
        return WARN
    return BAD


def _dial(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, score: int) -> None:
    """The report's own score dial: a full faint ring with the score's
    share swept over it, opening from twelve o'clock."""
    box = (cx - r, cy - r, cx + r, cy + r)
    draw.arc(box, 0, 360, fill=BORDER, width=14)
    colour = _score_colour(score)
    if score > 0:
        draw.arc(box, -90, -90 + int(360 * min(score, 100) / 100), fill=colour, width=14)

    label = str(score)
    f = sans(76, 700)
    tw = draw.textlength(label, font=f)
    draw.text((cx - tw / 2, cy - 52), label, font=f, fill=INK)

    out_of = "/100"
    f2 = mono(20)
    tw2 = draw.textlength(out_of, font=f2)
    draw.text((cx - tw2 / 2, cy + 22), out_of, font=f2, fill=INK_FAINT)


def _chip(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, label: str, value: str) -> None:
    h = 116
    draw.rounded_rectangle((x, y, x + w, y + h), radius=14, fill=SURFACE, outline=BORDER, width=2)
    _tracked(draw, (x + 22, y + 22), label.upper(), mono(17), INK_FAINT, tracking=1.4)

    f = sans(30, 600)
    text = value
    # One line only: the card is a glance, not a table.
    while draw.textlength(text, font=f) > w - 44 and len(text) > 4:
        text = text[:-2]
        if len(text) < len(value):
            text = text.rstrip() + "…"
    draw.text((x + 22, y + 56), text, font=f, fill=INK)


def render(
    postcode: str,
    district: str = "",
    region: str = "",
    score: int | None = None,
    grade: str = "",
    facts: list[tuple[str, str]] | None = None,
) -> bytes:
    """PNG bytes for one address's share card. facts is up to three
    (label, value) pairs, already resolved to display strings by the
    caller so this module needs no knowledge of the data sources."""
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # A quiet accent rule along the top, the same device the report uses.
    draw.rectangle((0, 0, W, 8), fill=ACCENT)

    _tracked(draw, (PAD, PAD), "UKPROPERTYINSIGHT", mono(20, 500), ACCENT, tracking=3.2)

    # Postcode, shrunk to fit rather than wrapped: it is the headline.
    size = 104
    while size > 48 and draw.textlength(postcode, font=sans(size, 700)) > W - PAD * 2 - 260:
        size -= 6
    draw.text((PAD, PAD + 54), postcode, font=sans(size, 700), fill=INK)

    place = ", ".join(p for p in (district, region) if p)
    if place:
        f = sans(30)
        while draw.textlength(place, font=f) > W - PAD * 2 - 260 and len(place) > 8:
            place = place[:-2].rstrip() + "…"
        draw.text((PAD, PAD + 64 + size), place, font=f, fill=INK_SOFT)

    if score is not None:
        _dial(draw, W - PAD - 96, PAD + 116, 96, score)
        if grade:
            f = mono(22, 500)
            tw = draw.textlength(grade.upper(), font=f)
            draw.text((W - PAD - 96 - tw / 2, PAD + 232), grade.upper(), font=f, fill=_score_colour(score))

    facts = (facts or [])[:3]
    if facts:
        gap = 24
        cw = (W - PAD * 2 - gap * (len(facts) - 1)) // len(facts)
        for i, (label, value) in enumerate(facts):
            _chip(draw, PAD + i * (cw + gap), H - PAD - 176, cw, label, value)

    draw.line((PAD, H - PAD - 40, W - PAD, H - PAD - 40), fill=BORDER, width=2)
    _tracked(
        draw, (PAD, H - PAD - 26),
        "40 CHECKS  ·  EVERY FIGURE NAMES ITS OFFICIAL SOURCE",
        mono(18), INK_FAINT, tracking=1.6,
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_school(
    name: str,
    authority: str,
    miles: float | None,
    academic_year: str = "",
    rating_label: str = "",
    town: str = "",
    facts: list[tuple[str, str]] | None = None,
) -> bytes:
    """PNG bytes for one school's share card: the name, and the one
    figure this site holds that nobody else surfaces, how far it
    admitted from. Shared into a parents' WhatsApp group or a Mumsnet
    thread, the card says the number before anyone taps the link."""
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, W, 8), fill=ACCENT)
    _tracked(draw, (PAD, PAD), "UKPROPERTYINSIGHT  ·  SCHOOL ADMISSIONS", mono(20, 500), ACCENT, tracking=3.2)

    # The name wraps to two lines at most; the figure on the right owns
    # 380px, so the name gets the rest.
    name_w = W - PAD * 2 - 400
    size = 60
    while size > 34 and draw.textlength(name, font=sans(size, 700)) > name_w * 1.9:
        size -= 4
    f = sans(size, 700)
    words, lines, cur = name.split(), [], ""
    for w_ in words:
        trial = (cur + " " + w_).strip()
        if draw.textlength(trial, font=f) <= name_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    lines = lines[:2]
    y = PAD + 54
    for ln in lines:
        draw.text((PAD, y), ln, font=f, fill=INK)
        y += size + 8

    sub = ", ".join(p for p in (town, authority) if p)
    if sub:
        fs = sans(28)
        while draw.textlength(sub, font=fs) > name_w and len(sub) > 8:
            sub = sub[:-2].rstrip() + "…"
        draw.text((PAD, y + 6), sub, font=fs, fill=INK_SOFT)

    # The figure.
    if miles is not None:
        fig = f"{miles:g}"
        ff = sans(120, 700)
        tw = draw.textlength(fig, font=ff)
        x = W - PAD - tw
        draw.text((x, PAD + 44), fig, font=ff, fill=ACCENT)
        _tracked(draw, (x, PAD + 176), "MILES", mono(22, 500), INK_SOFT, tracking=3)
        _tracked(draw, (x, PAD + 206), f"ADMITTED FROM, {academic_year}".upper() if academic_year else "ADMITTED FROM",
                 mono(18), INK_FAINT, tracking=1.4)

    facts = (facts or [])[:3]
    if facts:
        gap = 24
        cw = (W - PAD * 2 - gap * (len(facts) - 1)) // len(facts)
        for i, (label, value) in enumerate(facts):
            _chip(draw, PAD + i * (cw + gap), H - PAD - 176, cw, label, value)

    draw.line((PAD, H - PAD - 40, W - PAD, H - PAD - 40), fill=BORDER, width=2)
    foot = f"PUBLISHED BY {authority.upper()}  ·  NOT A CATCHMENT, A REAL DISTANCE" if authority else "OFFICIAL DATA, EVERY FIGURE NAMES ITS SOURCE"
    _tracked(draw, (PAD, H - PAD - 26), foot, mono(18), INK_FAINT, tracking=1.6)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

