"""Official brand colours for UK rail/tube/tram lines, keyed by the
line name OSM public_transport=stop_area relations use. Matching is
case-insensitive and tolerant of "line"/"&"/"and" variants since real
OSM data isn't perfectly consistent.

Confidence varies: London Underground, DLR, Elizabeth line and
Tramlink colours are exact and stable (TfL brand standards, unchanged
for years). The regional light rail/tram systems below are each
tagged with a confidence note - most use one main brand colour rather
than per-branch colours, since those vary more and are less widely
documented.
"""
import re

LINE_COLORS: dict[str, str] = {
    # London Underground - exact TfL brand colours
    "bakerloo": "#B36305",
    "central": "#E32017",
    "circle": "#FFD300",
    "district": "#00782A",
    "hammersmith city": "#F3A9BB",
    "jubilee": "#A0A5A9",
    "metropolitan": "#9B0056",
    "northern": "#000000",
    "piccadilly": "#003688",
    "victoria": "#0098D4",
    "waterloo city": "#95CDBA",
    # London Overground - exact TfL brand colour (2024 renamed the
    # network into 6 named lines; each keeps this same orange in TfL's
    # own wayfinding, so one colour covers all of them accurately)
    "overground": "#EE7C0E",
    "mildmay": "#0077C8",
    "windrush": "#EE2E24",
    "weaver": "#9B0058",
    "suffragette": "#65B32E",
    "liberty": "#828282",
    "lioness": "#FFA600",
    # Other London modes - exact
    "dlr": "#00A4A7",
    "docklands light railway": "#00A4A7",
    "elizabeth": "#7156A5",
    "tramlink": "#84B817",
    "london trams": "#84B817",
    # Regional systems - each one main system colour, good confidence
    # on the well-known ones (Glasgow, Nottingham), approximate on the
    # less-documented ones (Tyne and Wear, West Midlands)
    "glasgow subway": "#F57920",
    "subway": "#F57920",
    "nottingham express transit": "#84329B",
    "net": "#84329B",
    "metrolink": "#FFCC00",
    "sheffield supertram": "#00A6E2",
    "supertram": "#00A6E2",
    "tyne and wear metro": "#F4A21E",
    "west midlands metro": "#6E2B62",
    "edinburgh trams": "#7B2D26",
}

FALLBACK_COLOR = "#667085"  # --ink-soft - used when a line name isn't recognised


def _normalize(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\b(line|and|&)\b", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def color_for_line(name: str) -> str:
    key = _normalize(name)
    if key in LINE_COLORS:
        return LINE_COLORS[key]
    # substring match - handles cases like "bakerloo line (eastbound)"
    # or a stop_area relation whose name includes extra qualifiers
    for known, color in LINE_COLORS.items():
        if known in key or key in known:
            return color
    return FALLBACK_COLOR


def text_color_for(hex_color: str) -> str:
    """Black or white text, whichever contrasts better against
    hex_color - needed since some line colours are pale (Circle's
    yellow, Hammersmith & City's pink) and others are near-black
    (Northern)."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#12141c" if luminance > 0.6 else "#ffffff"
