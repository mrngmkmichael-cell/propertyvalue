"""Generate WebP versions of the PNGs the site actually serves.

Run after adding or replacing any image under app/static/img/, then
reference the .webp from the template. The .png originals stay in the
repo as the editable source; nothing at runtime requests them once the
template points at the .webp.

    .venv/Scripts/python.exe scripts/optimise_images.py

Two findings worth keeping, both measured rather than assumed:

1. Do NOT resize these down to their displayed size. The why-card
   illustrations are flat art with fourteen unique colours; resampling
   anti-aliases those edges into thousands of colours and DOUBLES the
   encoded size. The usual "serve images at display size" advice
   backfires on flat vector-style art.

2. Lossless beats lossy on that same flat art (fewer colours compress
   perfectly), while the extension screenshots - thousands of colours,
   photographic-ish - do better at quality 85. So each file is encoded
   every way and the smallest wins, rather than picking one setting for
   everything.

og-default.png is deliberately excluded: it is the Open Graph preview
image, never loaded by a browser, and WhatsApp/Facebook/LinkedIn link
previews are unreliable with WebP. No gain, real risk.
"""
import glob
import io
import os
import sys

from PIL import Image

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG_ROOT = os.path.join(REPO_ROOT, "app", "static", "img")

# Only what a browser actually downloads. The full-size originals in
# why-cards/ (as opposed to why-cards/transparent/) are unreferenced
# working files, so converting them would just add weight to the repo.
PATTERNS = [
    "why-cards/transparent/*.png",
    "extension/*.png",
]

EXCLUDE = {"og-default.png"}

ENCODINGS = [
    ("lossless", {"lossless": True, "method": 6}),
    ("q85", {"quality": 85, "method": 6}),
    ("q92", {"quality": 92, "method": 6}),
]


def encode(im: Image.Image, opts: dict) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "WEBP", **opts)
    return buf.getvalue()


def main() -> None:
    total_png = total_webp = 0
    converted = 0

    print(f"{'file':<34}{'PNG':>9}{'WebP':>9}{'saving':>9}  encoding")
    for pattern in PATTERNS:
        for path in sorted(glob.glob(os.path.join(IMG_ROOT, pattern))):
            name = os.path.basename(path)
            if name in EXCLUDE:
                continue

            im = Image.open(path).convert("RGBA")
            best_name, best_bytes = None, None
            for label, opts in ENCODINGS:
                data = encode(im, opts)
                if best_bytes is None or len(data) < len(best_bytes):
                    best_name, best_bytes = label, data

            png_size = os.path.getsize(path)
            if len(best_bytes) >= png_size:
                print(f"{name:<34}{png_size/1024:8.1f}K "
                      f"{'':>8} {'':>8}  skipped, PNG already smaller")
                continue

            out_path = os.path.splitext(path)[0] + ".webp"
            with open(out_path, "wb") as fh:
                fh.write(best_bytes)

            rel = os.path.relpath(out_path, IMG_ROOT).replace("\\", "/")
            total_png += png_size
            total_webp += len(best_bytes)
            converted += 1
            print(f"{rel:<34}{png_size/1024:8.1f}K{len(best_bytes)/1024:8.1f}K"
                  f"{100*(png_size-len(best_bytes))/png_size:8.0f}%  {best_name}")

    if not converted:
        print("\nNothing to convert.")
        return

    print(f"\n{converted} file(s): {total_png/1024:.1f}K -> {total_webp/1024:.1f}K "
          f"({100*(total_png-total_webp)/total_png:.0f}% smaller, "
          f"{(total_png-total_webp)/1024:.0f} KiB saved)")
    print("Templates must reference the .webp paths for any of this to count.")


if __name__ == "__main__":
    sys.exit(main())
