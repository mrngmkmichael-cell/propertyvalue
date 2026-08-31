"""Wrap literal interface text in templates with {{ tr("...") }}.

Mechanical, and deliberately timid: it only touches a run of text that
sits directly between two tags and contains no Jinja at all. Everything
else is left for a human, because the failure mode of being clever here
is a broken page in nine languages.

Skipped on purpose:
  * anything with { or } in it (a Jinja expression or tag)
  * anything inside <script>, <style>, <pre>, <code> or <textarea>
  * runs with no letters, or shorter than the threshold (punctuation,
    single symbols, bare numbers)
  * runs already wrapped

Whitespace around a run is preserved outside the wrap, so the rendered
output is byte-identical while the language stays English. That is the
property scripts/i18n_snapshot.py checks.
"""
import pathlib
import re
import sys

TPL = pathlib.Path("app/templates")
SKIP_ELEMENTS = re.compile(
    r"<(script|style|pre|code|textarea)\b.*?</\1>", re.S | re.I)
MIN_LEN = 2

# A run of text between tags: no angle brackets, no braces.
RUN = re.compile(r"(>)([^<>{}]+)(<)")


def translatable(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < MIN_LEN:
        return False
    # Needs actual words, not "·" or "£1,000" or "/".
    if not re.search(r"[A-Za-z]{2}", stripped):
        return False
    # A bare entity like &middot; is punctuation, not a sentence.
    if re.fullmatch(r"(&[a-z]+;|\s|[^A-Za-z])+", stripped):
        return False
    return True


def mark_file(path: pathlib.Path, dry: bool) -> tuple[int, list[str]]:
    src = path.read_text(encoding="utf-8")
    # Blank out the regions we must not touch, so offsets still line up.
    masked = SKIP_ELEMENTS.sub(lambda m: "\x01" * len(m.group(0)), src)

    found: list[str] = []
    out = []
    last = 0
    for m in RUN.finditer(masked):
        raw = src[m.start(2):m.end(2)]
        if "\x01" in masked[m.start(2):m.end(2)] or not translatable(raw):
            continue
        stripped = raw.strip()
        lead = raw[:len(raw) - len(raw.lstrip())]
        trail = raw[len(raw.rstrip()):]
        # Collapse internal newlines/indentation: the catalogue key must
        # be one tidy line or it is unreviewable, and HTML treats any run
        # of whitespace as one space anyway.
        key = " ".join(stripped.split())
        quote = '"' if '"' not in key else "'"
        if quote == "'" and "'" in key:
            continue  # both quote styles present: leave it for a human
        out.append(src[last:m.start(2)])
        out.append(f"{lead}{{{{ tr({quote}{key}{quote}) }}}}{trail}")
        last = m.end(2)
        found.append(key)
    out.append(src[last:])
    if found and not dry:
        path.write_text("".join(out), encoding="utf-8")
    return len(found), found


def main():
    dry = "--write" not in sys.argv
    total = 0
    strings: list[str] = []
    for f in sorted(TPL.rglob("*.html")):
        n, found = mark_file(f, dry)
        if n:
            total += n
            strings.extend(found)
            print(f"{f.relative_to(TPL)!s:44}{n:>5}")
    print(f"\n{total} runs{' would be' if dry else ''} wrapped, "
          f"{len(set(strings))} distinct")
    if dry:
        print("(dry run: pass --write to apply)")


main()
