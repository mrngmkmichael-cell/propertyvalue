"""Download the self-hosted webfonts from Google Fonts.

    .venv/Scripts/python.exe scripts/fetch_fonts.py

Only needed when the set of families, weights or styles changes - the
files in app/static/fonts/ are committed and don't otherwise need
regenerating.

Why self-hosted: loading these from fonts.googleapis.com cost ~780ms of
render-blocking time for a 1.6 KiB stylesheet, because the browser had
to resolve and handshake with fonts.googleapis.com, read the CSS, then
resolve and handshake with fonts.gstatic.com before it could start
fetching an actual font file - two chained third-party origins ahead of
first paint. Served from our own origin that connection already exists.

Google serves all of these as VARIABLE fonts, so one file covers the
whole weight range; that is why the @font-face rules in style.css
declare `font-weight: 400 700` rather than one block per weight.

If the printed unicode-range values differ from what is already in
style.css, update them there too - they are what stops the browser
downloading the latin-ext file for a page with no accented characters.

All families here are SIL Open Font License 1.1 (app/static/fonts/
OFL.txt), which permits self-hosting and redistribution.
"""
import os
import re
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO_ROOT, "app", "static", "fonts")

# Google returns legacy formats to anything it doesn't recognise as a
# modern browser, so ask as one.
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# slug -> Google css2 family spec. Keep the weight ranges in step with
# what style.css actually uses; a wider range is a bigger file for
# nothing.
FAMILIES = {
    # Body face. Deliberately not Inter: Inter is the default of nearly
    # every AI site builder, and looking generated was the problem this
    # redesign set out to fix. Instrument Sans has real character in the
    # 'a', 'g' and 'R' without being hard to read at small sizes.
    "instrument-sans": "Instrument+Sans:ital,wght@0,400..700;1,400",
    # Labels, eyebrows and figures. A monospace for small uppercase
    # labels is the move that makes a data product read as precise
    # rather than generic. Subset hard afterwards (see MONO_GLYPHS) -
    # it only ever renders labels and figures, so the full 30.7 KiB
    # face is 62% dead weight.
    "jetbrains-mono": "JetBrains+Mono:wght@400..500",
    # Display face for report headings and the score. One weight only -
    # Bebas Neue is a single-weight condensed caps face, which is the
    # whole point of it: it sets big without shouting in bold.
    "bebas-neue": "Bebas+Neue",
}

# Everything the mono face is actually asked to render: uppercase
# labels, figures, and the separators used between them.
MONO_GLYPHS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    " .,:;·/–—()£$%+-#&'’→›"
)

# An English-language site: latin covers it, latin-ext adds central and
# eastern European accents for the occasional place or street name.
WANTED_SUBSETS = {"latin", "latin-ext"}


def fetch_family(client: httpx.Client, slug: str, spec: str) -> tuple[int, int, dict]:
    url = f"https://fonts.googleapis.com/css2?family={spec}&display=swap"
    resp = client.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()

    blocks = re.findall(
        r"/\*\s*([a-z-]+)\s*\*/\s*@font-face\s*\{(.*?)\}", resp.text, re.S
    )
    if not blocks:
        print(f"  {slug}: could not parse the Google Fonts CSS - format changed?")
        return 0, 0, {}

    seen, total, ranges = set(), 0, {}
    for subset, body in blocks:
        if subset not in WANTED_SUBSETS:
            continue
        style = re.search(r"font-style:\s*([^;]+);", body).group(1).strip()
        if (subset, style) in seen:
            continue  # every weight shares the one variable file
        seen.add((subset, style))

        src = re.search(r"url\((https[^)]+)\)", body).group(1)
        ranges[(subset, style)] = re.search(
            r"unicode-range:\s*([^;]+);", body).group(1).strip()

        name = f"{slug}-{subset}-{style}.woff2"
        data = client.get(src, headers={"User-Agent": UA}, timeout=60).content
        with open(os.path.join(OUT_DIR, name), "wb") as fh:
            fh.write(data)
        total += len(data)
        print(f"    {name:<38} {len(data)/1024:7.1f} KiB")

    return len(seen), total, ranges


def subset_mono() -> None:
    """Cut the mono face down to the glyphs the site actually uses, then
    delete the full-range files - nothing references them."""
    from fontTools.subset import main as subset_main

    src = os.path.join(OUT_DIR, "jetbrains-mono-latin-normal.woff2")
    out = os.path.join(OUT_DIR, "jetbrains-mono-labels.woff2")
    if not os.path.exists(src):
        print("\n  mono subset skipped - source file missing")
        return

    before = os.path.getsize(src)
    subset_main([
        src, f"--text={MONO_GLYPHS}", "--flavor=woff2",
        f"--output-file={out}", "--layout-features=*",
        "--no-hinting", "--desubroutinize",
    ])
    after = os.path.getsize(out)
    print(f"\n  jetbrains-mono subset: {before / 1024:.1f} -> {after / 1024:.1f} KiB "
          f"({100 * (before - after) / before:.0f}% smaller)")

    for leftover in ("jetbrains-mono-latin-normal.woff2",
                     "jetbrains-mono-latin-ext-normal.woff2"):
        path = os.path.join(OUT_DIR, leftover)
        if os.path.exists(path):
            os.remove(path)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    grand_total = 0
    all_ranges = {}

    with httpx.Client(follow_redirects=True) as client:
        for slug, spec in FAMILIES.items():
            print(f"\n  {spec.split(':')[0].replace('+', ' ')}")
            count, total, ranges = fetch_family(client, slug, spec)
            grand_total += total
            all_ranges.update(ranges)

    subset_mono()

    print(f"\n  {grand_total/1024:.1f} KiB downloaded into {OUT_DIR}")
    print("\n  Only the latin/normal files are on the critical path - the "
          "latin-ext and\n  italic ones carry unicode-range rules and load "
          "only if a page needs them.")
    print("\n  unicode-range values Google is currently using - check these "
          "still match style.css:")
    for (subset, style), urange in sorted(all_ranges.items()):
        print(f"\n    {subset} / {style}:\n      {urange}")


if __name__ == "__main__":
    main()
