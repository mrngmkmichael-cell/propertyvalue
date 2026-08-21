"""Build the homepage hero's background map as one static image.

    .venv/Scripts/python.exe scripts/build_hero_map.py

The hero map was a live Leaflet map, which is a strange thing to use for
what it actually is: a fixed view, at a fixed zoom, that cannot be
dragged, zoomed or clicked, blurred and desaturated, and mostly covered
by a gradient. It was fetching 20 OpenStreetMap tiles (~277 KiB) plus
Leaflet itself (~56 KiB) over two third-party origins to render what is,
in effect, a photograph.

This fetches those same 20 tiles once, stitches them, and writes a single
46 KiB WebP that the CSS drops in as a background-image. Nothing at
runtime touches openstreetmap.org or unpkg.com any more - which is also
considerably kinder to OSM's donated tile servers than sending every
visitor to them.

The tile coordinates below were read off the live page rather than
derived: Leaflet snaps zoom 6.4 to 6, and at zoom 6 this view is x 29-33,
y 19-22. If the hero's centre or zoom changes in index.html, re-read them
from the browser (inspect the img.leaflet-tile src attributes) rather
than guessing.

Sizing: main.site-main is capped at 1080px, so this never displays wider
than ~1052 CSS px. 900px at quality 60 measured best of the options -
better than 1024px, because the extra downscaling acts as a low-pass
filter that suits the CSS blur better than sharper detail plus
compression artefacts. Mean per-channel error after the page's own filter
chain is 0.9 out of 255.

Attribution: OSM tiles are ODbL. The Leaflet control used to carry the
credit; index.html now carries it in the hero instead. It has to stay.
"""
import io
import os
import sys
import time

import httpx
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(REPO_ROOT, "app", "static", "img", "hero-map.webp")

ZOOM, X0, X1, Y0, Y1 = 6, 29, 33, 19, 22
TILE_PX = 256
OUTPUT_WIDTH = 900
QUALITY = 60

# OSM's tile usage policy asks for a User-Agent identifying the app.
HEADERS = {
    "User-Agent": "ukpropertyinsight-hero-map-builder/1.0 "
                  "(one-off static asset build; contact via ukpropertyinsight.co.uk)"
}


def main() -> None:
    cols, rows = X1 - X0 + 1, Y1 - Y0 + 1
    canvas = Image.new("RGB", (cols * TILE_PX, rows * TILE_PX))

    print(f"Fetching {cols * rows} tiles at zoom {ZOOM}...")
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        for x in range(X0, X1 + 1):
            for y in range(Y0, Y1 + 1):
                url = f"https://tile.openstreetmap.org/{ZOOM}/{x}/{y}.png"
                resp = client.get(url)
                if resp.status_code != 200:
                    print(f"  {url} -> HTTP {resp.status_code}, aborting")
                    return
                tile = Image.open(io.BytesIO(resp.content)).convert("RGB")
                canvas.paste(tile, ((x - X0) * TILE_PX, (y - Y0) * TILE_PX))
                # A one-off build has no business hammering donated servers.
                time.sleep(0.3)

    height = round(canvas.size[1] * OUTPUT_WIDTH / canvas.size[0])
    out = canvas.resize((OUTPUT_WIDTH, height), Image.LANCZOS)
    out.save(OUT_PATH, "WEBP", quality=QUALITY, method=6)

    size = os.path.getsize(OUT_PATH)
    print(f"\nWrote {OUT_PATH}")
    print(f"  {OUTPUT_WIDTH}x{height}, {size / 1024:.1f} KiB")
    print(f"  replaces ~277 KiB of tiles + ~56 KiB of Leaflet")


if __name__ == "__main__":
    main()
