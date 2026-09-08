"""A school's admission distance as a picture.

The school page draws the distance as a circle on a JavaScript map, which
a search engine cannot see and a WhatsApp preview cannot show. This
renders the same facts as a real image: the ring to scale, the school at
its centre, the postcode districts whose centre falls inside, a scale
bar, and the figure itself. Server-side with Pillow, sharing the fonts
and palette of the share cards in og_image, so the card a parent shares
and the picture a search engine indexes are one drawing.

Step 3 of the catchment map, 9 Sep 2026. Everything drawn here is the
published figure and the district centres from outcodes.json; nothing
is inferred.
"""
from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw

from app.services import og_image
from app.services.og_image import ACCENT, BG, BORDER, H, INK, INK_FAINT, INK_SOFT, PAD, W

RING = (217, 119, 6)          # the map's own orange, #d97706
KM_PER_MILE = 1.60934
RADIUS_PX = 180


def _blend(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(round(x * (1 - t) + y * t) for x, y in zip(a, b))


def geometry(lat0: float, lon0: float, miles: float, districts: list[dict], radius_px: int = RADIUS_PX) -> list[dict]:
    """Each district centre as an offset in pixels from the school, on a
    flat projection good to well under a percent at these distances.
    x runs east, y runs south, so it can be drawn straight onto the
    canvas."""
    scale = radius_px / max(miles * KM_PER_MILE, 0.01)   # px per km
    out = []
    for d in districts:
        dx_km = (d["lon"] - lon0) * math.cos(math.radians(lat0)) * 111.32
        dy_km = (d["lat"] - lat0) * 110.57
        out.append({"code": d["outcode"], "x": dx_km * scale, "y": -dy_km * scale})
    return out


def _scale_bar_miles(miles: float) -> float:
    """A round length that draws between 45 and 180 px."""
    px_per_mile = RADIUS_PX / max(miles, 0.01)
    for candidate in (5, 2, 1, 0.5, 0.25, 0.1, 0.05):
        if 45 <= candidate * px_per_mile <= 180:
            return candidate
    return 0.05


def render(
    name: str,
    authority: str,
    miles: float,
    miles_label: str,
    year_label: str,
    lat: float,
    lon: float,
    districts: list[dict],
    town: str = "",
) -> bytes:
    """PNG bytes, 1200 by 630 like the share cards, so the same file
    serves as the page's picture and its Open Graph image."""
    sans, mono, tracked = og_image.sans, og_image.mono, og_image._tracked
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, W, 8), fill=ACCENT)
    tracked(draw, (PAD, PAD), "UKPROPERTYINSIGHT  ·  ADMISSION DISTANCE, TO SCALE", mono(20, 500), ACCENT, tracking=3.2)

    # The ring on the right; the words on the left get what is left.
    cx = W - PAD - RADIUS_PX - 24
    cy = PAD + 62 + RADIUS_PX
    left_w = cx - RADIUS_PX - 36 - PAD

    size = 54
    while size > 30 and draw.textlength(name, font=sans(size, 700)) > left_w * 1.9:
        size -= 4
    f = sans(size, 700)
    words, lines, cur = name.split(), [], ""
    for w_ in words:
        trial = (cur + " " + w_).strip()
        if draw.textlength(trial, font=f) <= left_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w_
    if cur:
        lines.append(cur)
    y = PAD + 50
    for ln in lines[:2]:
        draw.text((PAD, y), ln, font=f, fill=INK)
        y += size + 6

    sub = ", ".join(p for p in (town, authority) if p)
    if sub:
        fs = sans(24)
        while draw.textlength(sub, font=fs) > left_w and len(sub) > 8:
            sub = sub[:-2].rstrip() + "…"
        draw.text((PAD, y + 4), sub, font=fs, fill=INK_SOFT)
        y += 40

    # The figure, the one number the picture is about.
    y += 22
    fig_font = sans(84, 700)
    draw.text((PAD, y), miles_label, font=fig_font, fill=RING)
    fig_w = draw.textlength(miles_label, font=fig_font)
    draw.text((PAD + fig_w + 14, y + 44), "miles", font=sans(30, 500), fill=INK_SOFT)
    y += 100
    caption = f"ADMITTED FROM, {year_label}" if year_label else "ADMITTED FROM, LATEST PUBLISHED YEAR"
    tracked(draw, (PAD, y), caption, mono(18), INK_FAINT, tracking=1.4)
    y += 40

    codes = [d["outcode"] for d in districts]
    if codes:
        label = "Districts whose centre is inside: " + ", ".join(codes)
        fs = sans(22)
        words, cur, lines = label.split(), "", []
        for w_ in words:
            trial = (cur + " " + w_).strip()
            if draw.textlength(trial, font=fs) <= left_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = w_
        if cur:
            lines.append(cur)
        for ln in lines[:3]:
            if y > H - PAD - 70:
                break
            draw.text((PAD, y), ln, font=fs, fill=INK_SOFT)
            y += 30

    # The ring itself, filled faintly like the map's, then the districts,
    # then the school on top.
    draw.ellipse((cx - RADIUS_PX, cy - RADIUS_PX, cx + RADIUS_PX, cy + RADIUS_PX), fill=_blend(BG, RING, 0.08), outline=RING, width=3)
    label_font = sans(21, 700)
    for pt in geometry(lat, lon, miles, districts):
        px, py = cx + pt["x"], cy + pt["y"]
        tw = draw.textlength(pt["code"], font=label_font)
        if math.hypot(pt["x"], pt["y"]) < 28:
            # The district whose centre all but coincides with the school:
            # no second dot, and the code sits beside the school's own.
            tx, ty = cx + 16, cy - 14
        else:
            draw.ellipse((px - 4, py - 4, px + 4, py + 4), fill=INK_SOFT)
            tx = min(max(px - tw / 2, cx - RADIUS_PX - 10), cx + RADIUS_PX + 10 - tw)
            ty = py + 7 if py < cy + RADIUS_PX - 34 else py - 30
        draw.text((tx, ty), pt["code"], font=label_font, fill=INK, stroke_width=3, stroke_fill=BG)
    draw.ellipse((cx - 12, cy - 12, cx + 12, cy + 12), fill=BG)
    draw.ellipse((cx - 9, cy - 9, cx + 9, cy + 9), fill=ACCENT)

    # A scale bar under the ring, in miles, at the ring's own scale.
    bar_miles = _scale_bar_miles(miles)
    bar_px = bar_miles * RADIUS_PX / max(miles, 0.01)
    bx, by = cx + RADIUS_PX - bar_px, cy + RADIUS_PX + 18
    draw.line((bx, by, bx + bar_px, by), fill=INK_SOFT, width=3)
    draw.line((bx, by - 6, bx, by + 6), fill=INK_SOFT, width=3)
    draw.line((bx + bar_px, by - 6, bx + bar_px, by + 6), fill=INK_SOFT, width=3)
    bar_text = f"{bar_miles:g} mile" + ("" if bar_miles == 1 else "s")
    bt_w = draw.textlength(bar_text, font=sans(18))
    draw.text((bx + bar_px - bt_w, by + 10), bar_text, font=sans(18), fill=INK_SOFT)

    draw.line((PAD, H - PAD - 40, W - PAD, H - PAD - 40), fill=BORDER, width=2)
    foot = (f"PUBLISHED BY {authority.upper()}  ·  A DISTANCE, NOT A BOUNDARY" if authority
            else "A DISTANCE, NOT A BOUNDARY  ·  OFFICIAL DATA, EVERY FIGURE NAMES ITS SOURCE")
    tracked(draw, (PAD, H - PAD - 26), foot, mono(18), INK_FAINT, tracking=1.6)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
