"""Set the number of checks the site quotes, everywhere it is written.

    python scripts/bump_check_count.py 42 "Bus Service"

The landing page's trust section carries the number a visitor can verify
by counting cards on a report, and a test fails when the two disagree
(tests/test_property_page.py::test_landing_page_check_count_matches_the_report).
The same figure appears in the build-strip tally, the "All N checks"
line, the dek, the orbit heading, the contact note, the pricing link, the
sign-up page, the area guide's button and the running-costs page, and the
landing page names every check in a script array. This script moves all
of them together and appends the new card's name to the array, so a new
check is one command rather than a hunt. Safe to re-run: a string already
moved is skipped.

By hand afterwards: the free/Premium split on the landing page and the
pricing page ("N free on every report", "N more with Premium") and the
pricing page's own list of checks.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
INDEX = ROOT / "app" / "templates" / "index.html"
WORDS = {40: "forty", 41: "forty-one", 42: "forty-two", 43: "forty-three", 44: "forty-four", 45: "forty-five",
         46: "forty-six", 47: "forty-seven", 48: "forty-eight", 49: "forty-nine", 50: "fifty"}


def _move(path: pathlib.Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old == new:
        return
    if text.count(old) == 0 and text.count(new) == 1:
        return  # already moved
    if text.count(old) != 1:
        raise SystemExit(f"{path.relative_to(ROOT)}: expected exactly one {old!r}, found {text.count(old)}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def _name_check(name: str) -> None:
    text = INDEX.read_text(encoding="utf-8")
    m = re.search(r"const CHECKS = \[[\s\S]*?\n\s*\];", text)
    if not m:
        raise SystemExit("could not find the CHECKS array on the landing page")
    block = m.group(0)
    if f"'{name}'" in block:
        return
    last_quote = block.rfind("'")
    new_block = block[:last_quote + 1] + f", '{name}'" + block[last_quote + 1:]
    INDEX.write_text(text.replace(block, new_block), encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    new = int(argv[1])
    name = argv[2] if len(argv) > 2 else ""
    m = re.search(r"stat_rows = \[\('(\d+)', 'Checks per property'\)", INDEX.read_text(encoding="utf-8"))
    if not m:
        raise SystemExit("could not find the trust-section stat on the landing page")
    old = int(m.group(1))
    if old == new:
        old = new - 1  # a re-run after a partial move
    moves = [
        (INDEX, f"[('{old}', 'Checks per property')", f"[('{new}', 'Checks per property')"),
        (INDEX, f"/ {old} checks</p>", f"/ {new} checks</p>"),
        (INDEX, f"All <strong>{old} checks</strong>", f"All <strong>{new} checks</strong>"),
        (INDEX, f"all {WORDS[old]}.", f"all {WORDS[new]}."),
        (INDEX, f"{WORDS[old].capitalize()} checks on any UK address", f"{WORDS[new].capitalize()} checks on any UK address"),
        (INDEX, f'lx-orbit-heading">{WORDS[old].capitalize()}<br>checks</h2>', f'lx-orbit-heading">{WORDS[new].capitalize()}<br>checks</h2>'),
        (INDEX, f"{old} checks &middot; 13 official sources", f"{new} checks &middot; 13 official sources"),
        (INDEX, f"full list of {old}", f"full list of {new}"),
        (ROOT / "app" / "templates" / "signup.html", f"all {old} checks unlocked", f"all {new} checks unlocked"),
        (ROOT / "app" / "templates" / "area_guide.html", f"Run the {old} checks", f"Run the {new} checks"),
        (ROOT / "app" / "templates" / "running_costs.html", f"its {WORDS[old]} other checks", f"its {WORDS[new]} other checks"),
    ]
    for path, before, after in moves:
        _move(path, before, after)
    if name:
        _name_check(name)
    print(f"check count {old} -> {new}" + (f", named {name!r}" if name else ""))
    print("By hand: the free/Premium split on the landing and pricing pages, and the pricing page's list of checks.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
