"""Re-download the self-hosted Inter woff2 files from Google Fonts.

Only needed if the set of weights or styles the CSS uses ever changes -
the files in app/static/fonts/ are committed and don't otherwise need
regenerating.

    .venv/Scripts/python.exe scripts/fetch_fonts.py

Why self-hosted at all: loading Inter from fonts.googleapis.com cost
~780ms of render-blocking time for a 1.6 KiB stylesheet, because the
browser had to resolve and handshake with fonts.googleapis.com, read the
CSS, then resolve and handshake with fonts.gstatic.com before it could
start fetching an actual font file - two chained third-party origins
ahead of first paint. Served from our own origin that connection already
exists.

Google serves Inter as a VARIABLE font, so one file covers every weight
from 400 to 800; that is why the @font-face rules in style.css declare
`font-weight: 400 800` rather than one block per weight.

If the printed unicode-range values differ from what is already in
style.css, update them there too - they are what stops the browser
downloading the latin-ext file for a page that has no accented
characters on it.

Inter is licensed under the SIL Open Font License 1.1 (app/static/fonts/
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

# Keep in step with the weights actually used in style.css.
CSS_URL = ("https://fonts.googleapis.com/css2?family=Inter:ital,wght@"
           "0,400;0,500;0,600;0,700;0,800;1,400&display=swap")

# An English-language site: latin covers it, latin-ext adds central and
# eastern European accents for the occasional place or street name. The
# greek, cyrillic and vietnamese subsets Google also offers would never
# be requested.
WANTED_SUBSETS = {"latin", "latin-ext"}


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    resp = httpx.get(CSS_URL, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()

    blocks = re.findall(
        r"/\*\s*([a-z-]+)\s*\*/\s*@font-face\s*\{(.*?)\}", resp.text, re.S
    )
    if not blocks:
        print("Could not parse the Google Fonts CSS - has the format changed?")
        return

    seen = set()
    total = 0
    ranges = {}
    for subset, body in blocks:
        if subset not in WANTED_SUBSETS:
            continue
        style = re.search(r"font-style:\s*([^;]+);", body).group(1).strip()
        if (subset, style) in seen:
            continue  # every weight shares the one variable file
        seen.add((subset, style))

        url = re.search(r"url\((https[^)]+)\)", body).group(1)
        ranges[(subset, style)] = re.search(
            r"unicode-range:\s*([^;]+);", body).group(1).strip()

        name = f"inter-{subset}-{style}.woff2"
        data = httpx.get(url, headers={"User-Agent": UA}, timeout=60).content
        with open(os.path.join(OUT_DIR, name), "wb") as fh:
            fh.write(data)
        total += len(data)
        print(f"  {name:<30} {len(data)/1024:7.1f} KiB")

    print(f"\n{len(seen)} file(s), {total/1024:.1f} KiB into {OUT_DIR}")
    print("\nunicode-range values Google is currently using - check these still "
          "match style.css:")
    for (subset, style), urange in sorted(ranges.items()):
        print(f"\n  {subset} / {style}:\n    {urange}")


if __name__ == "__main__":
    main()
