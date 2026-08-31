"""Which interface strings exist, and how much of each language is done.

Reads every tr("...") call out of the templates, compares against each
language's catalogue, and reports coverage. Also finds orphans: entries
in a catalogue whose English source no longer appears in any template,
which is what happens when someone edits the English and forgets the
translations. An orphan is harmless (the fallback renders English) but
it is dead weight in a file a human has to review.

    python scripts/i18n_report.py            # coverage table
    python scripts/i18n_report.py todo ja    # untranslated strings, one per line
"""
import pathlib
import re
import sys

sys.path.insert(0, ".")
from app import translations  # noqa: E402

TPL = pathlib.Path("app/templates")
CALL = re.compile(r'tr\(\s*(["\'])(.*?)\1\s*\)', re.S)


def sources() -> dict[str, list[str]]:
    """Every distinct English string -> the templates it appears in."""
    found: dict[str, list[str]] = {}
    for f in sorted(TPL.rglob("*.html")):
        for _quote, text in CALL.findall(f.read_text(encoding="utf-8")):
            found.setdefault(text, []).append(f.name)
    return found


def main():
    all_sources = sources()
    words = sum(len(s.split()) for s in all_sources)

    if len(sys.argv) > 2 and sys.argv[1] == "todo":
        lang = sys.argv[2]
        table = translations.catalogue(lang)
        for text in sorted(all_sources, key=lambda s: (-len(s.split()), s)):
            if text not in table:
                print(text)
        return

    print(f"{len(all_sources)} distinct strings, {words} words\n")
    print(f"{'language':12}{'done':>7}{'missing':>9}{'coverage':>10}{'orphans':>9}")
    for code in translations._MODULES:
        table = translations.catalogue(code)
        done = sum(1 for s in all_sources if s in table)
        orphans = sum(1 for k in table if k not in all_sources)
        pct = 100 * done / len(all_sources) if all_sources else 0
        print(f"{code:12}{done:>7}{len(all_sources) - done:>9}{pct:>9.1f}%{orphans:>9}")


main()
