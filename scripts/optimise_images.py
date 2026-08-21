"""Generate the WebP versions of the PNGs the site actually serves.

Run after adding or replacing any image under app/static/img/, then
reference the .webp from the template. The .png originals stay in the
repo as the editable source; nothing at runtime requests them once the
template points at the .webp.

    .venv/Scripts/python.exe scripts/optimise_images.py

Three findings behind the settings below, all measured rather than
assumed:

1. Each file is encoded every available way and the smallest wins. One
   setting does not suit both kinds of image here: the why-card art is
   flat with fourteen unique colours and compresses best losslessly,
   while the extension screenshots have thousands of colours and do
   better at quality 85.

2. Resizing flat art to its displayed size makes the file BIGGER, which
   is the opposite of the usual advice. Resampling anti-aliases fourteen
   colours into two thousand and doubles the encoded size - a 792x620
   illustration went from 19.5 KiB to 39.4 KiB when scaled to 440px.

3. ...unless you quantize afterwards, which puts the colour count back
   where it started and makes the resize pay off properly. Resize plus a
   16-colour quantize took the why-cards from 61.7 KiB to 29.4 KiB, at a
   mean per-channel error of under 1/255 measured at real display size
   against the card background - invisible.

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

# max_px is twice the largest box the layout gives the image, so it stays
# sharp on a 2x display and no sharper. None means keep full resolution.
# quantize applies only where the source is flat art (see note 3 above);
# on a photographic screenshot it would band the gradients.
GROUPS = [
    {
        "pattern": "why-cards/transparent/*.png",
        # .why-card-icon-box is 220x220 (desktop) / full-width x 120 (mobile)
        "max_px": 440,
        "quantize": 16,
    },
    {
        # Displayed near full width on /browser-extension, and photographic -
        # neither resizing nor quantizing suits them.
        "pattern": "extension/*.png",
        "max_px": None,
        "quantize": None,
    },
]

EXCLUDE = {"og-default.png"}


def encode(im, **opts):
    buf = io.BytesIO()
    im.save(buf, "WEBP", method=6, **opts)
    return buf.getvalue()


def candidates(im, max_px, quantize):
    """Every encoding worth trying for this image, as {label: bytes}."""
    out = {
        "lossless": encode(im, lossless=True),
        "q85": encode(im, quality=85),
        "q92": encode(im, quality=92),
    }
    if max_px and max(im.size) > max_px:
        small = im.copy()
        small.thumbnail((max_px, max_px), Image.LANCZOS)
        out[f"{max_px}px lossless"] = encode(small, lossless=True)
        out[f"{max_px}px q85"] = encode(small, quality=85)
        if quantize:
            # FASTOCTREE is the only quantizer Pillow offers for RGBA, and
            # dithering would scatter noise across the flat areas that make
            # this worth doing at all.
            q = small.quantize(
                colors=quantize, method=Image.FASTOCTREE, dither=Image.NONE
            ).convert("RGBA")
            out[f"{max_px}px {quantize}-colour"] = encode(q, lossless=True)
    return out


def main() -> None:
    total_png = total_webp = 0
    converted = 0

    print(f"{'file':<34}{'PNG':>9}{'WebP':>9}{'saving':>8}  encoding")
    for group in GROUPS:
        pattern = os.path.join(IMG_ROOT, group["pattern"])
        for path in sorted(glob.glob(pattern)):
            name = os.path.basename(path)
            if name in EXCLUDE:
                continue

            im = Image.open(path).convert("RGBA")
            opts = candidates(im, group["max_px"], group["quantize"])
            best_label = min(opts, key=lambda k: len(opts[k]))
            best = opts[best_label]

            png_size = os.path.getsize(path)
            if len(best) >= png_size:
                print(f"{name:<34}{png_size/1024:8.1f}K{'':>9}{'':>8}  "
                      f"skipped, PNG already smaller")
                continue

            out_path = os.path.splitext(path)[0] + ".webp"
            with open(out_path, "wb") as fh:
                fh.write(best)

            rel = os.path.relpath(out_path, IMG_ROOT).replace("\\", "/")
            total_png += png_size
            total_webp += len(best)
            converted += 1
            print(f"{rel:<34}{png_size/1024:8.1f}K{len(best)/1024:8.1f}K"
                  f"{100*(png_size-len(best))/png_size:7.0f}%  {best_label}")

    if not converted:
        print("\nNothing to convert.")
        return

    print(f"\n{converted} file(s): {total_png/1024:.1f}K -> {total_webp/1024:.1f}K "
          f"({100*(total_png-total_webp)/total_png:.0f}% smaller, "
          f"{(total_png-total_webp)/1024:.0f} KiB saved)")
    print("Templates must reference the .webp paths for any of this to count.")


if __name__ == "__main__":
    sys.exit(main())
