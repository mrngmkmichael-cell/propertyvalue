"""Convert the site's own woff2 web fonts to TTF for the share-image
generator.

Pillow draws text through FreeType, which cannot read woff2, and the
brand faces only ship here as woff2. Rather than add fontTools and
brotli to the deployed requirements just to decompress a font on every
boot, this converts them once and the .ttf files are committed
alongside the woff2 originals.

Re-run only if the web fonts themselves are replaced:
    .venv/Scripts/python.exe scripts/build_og_fonts.py
"""
import pathlib

from fontTools.ttLib import TTFont

FONT_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "fonts"

SOURCES = [
    "instrument-sans-latin-normal.woff2",
    "jetbrains-mono-labels.woff2",
]

for name in SOURCES:
    src = FONT_DIR / name
    if not src.exists():
        raise SystemExit(f"missing {src}")
    dest = src.with_suffix(".ttf")
    font = TTFont(src)
    font.flavor = None          # drop the woff2 wrapper
    font.save(dest)
    axes = []
    if "fvar" in font:
        axes = [(a.axisTag, a.minValue, a.defaultValue, a.maxValue) for a in font["fvar"].axes]
    print(f"{name} -> {dest.name} ({dest.stat().st_size // 1024} KB) variable axes: {axes or 'none'}")
