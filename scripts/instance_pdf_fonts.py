"""Static weights of the site's fonts for the PDF report.

The site ships Instrument Sans and JetBrains Mono as variable fonts, which
the PDF engine (xhtml2pdf on reportlab) cannot vary: it embeds one face per
file at that file's default instance, and dedupes files that share a
PostScript name. So each weight the report uses is instanced here into its
own static TTF with its own name, under app/static/fonts/pdf/.

Re-run only if the source fonts change. Output is committed.

    .venv/Scripts/python.exe scripts/instance_pdf_fonts.py
"""
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "static" / "fonts" / "pdf"
JOBS = [
    (ROOT / "app/static/fonts/instrument-sans-latin-normal.ttf", "InstrumentSans", "Instrument Sans",
     {400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold"}),
    (ROOT / "app/static/fonts/jetbrains-mono-labels.ttf", "JetBrainsMono", "JetBrains Mono",
     {400: "Regular", 700: "Bold"}),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for src, ps_family, family, weights in JOBS:
        for weight, style in weights.items():
            font = TTFont(src)
            static = instancer.instantiateVariableFont(font, {"wght": weight})
            names = static["name"]
            for rec in list(names.names):
                if rec.nameID in (1, 2, 3, 4, 6, 16, 17):
                    names.removeNames(nameID=rec.nameID)
            for name_id, value in ((1, family), (2, style), (3, f"{family} {style} pdf"), (4, f"{family} {style}"), (6, f"{ps_family}-{style}")):
                names.setName(value, name_id, 3, 1, 0x409)
            static["OS/2"].usWeightClass = weight
            if weight >= 600:
                static["OS/2"].fsSelection = (static["OS/2"].fsSelection | 0x20) & ~0x40
                static["head"].macStyle |= 0x1
            target = OUT / f"{ps_family}-{weight}.ttf"
            static.save(target)
            print(target.relative_to(ROOT), target.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
