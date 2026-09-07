"""Set the number of checks the site quotes, everywhere it is written.

    python scripts/bump_check_count.py 41 "Development Nearby"

The landing page's trust section carries the number a visitor can verify
by counting cards on a report, and a test fails when the two disagree
(tests/test_property_page.py::test_landing_page_check_count_matches_the_report).
The same figure appears in the build-strip tally, the "All N checks"
line, the contact note, the sign-up page, the area guide's button and the
running-costs page, and the landing page names every check in a script
array. This script moves all of them together and appends the new card's
name to the array, so a new check is one command rather than a hunt.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORDS = {40: "forty", 41: "forty-one", 42: "forty-two", 43: "forty-three", 44: "forty-four", 45: "forty-five",
         46: "forty-six", 47: "forty-seven", 48: "forty-eight", 49: "forty-nine", 50: "fifty"}


def _replace_once(path: pathlib.Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"{path.relative_to(ROOT)}: expected exactly one {old!r}, found {text.count(old)}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    new = int(argv[1])
    name = argv[2] if len(argv) > 2 else ""
    index = ROOT / "app" / "templates" / "index.html"
    text = index.read_text(encoding="utf-8")
    m = re.search(r"stat_rows = \[\('(\d+)', 'Checks per property'\)", text)
    if not m:
        raise SystemExit("could not find the trust-section stat on the landing page")
    old = int(m.group(1))
    if new == old and not name:
        print(f"already {old}")
        return 0
    _replace_once(index, f"[('{old}', 'Checks per property')", f"[('{new}', 'Checks per property')")
    _replace_once(index, f"/ {old} checks</p>", f"/ {new} checks</p>")
    _replace_once(index, f"All <strong>{old} checks</strong>", f"All <strong>{new} checks</strong>")
    _replace_once(index, f"all {WORDS[old]}.", f"all {WORDS[new]}.")
    _replace_once(index, f"{WORDS[old].capitalize()} checks on any UK address", f"{WORDS[new].capitalize()} checks on any UK address")
    _replace_once(index, f"{old} checks &middot; 13 official sources", f"{new} checks &middot; 13 official sources")
    _replace_once(index, f'lx-orbit-heading">{WORDS[old].capitalize()}<br>checks</h2>', f'lx-orbit-heading">{WORDS[new].capitalize()}<br>checks</h2>')
    if name:
        _replace_once(index, "'Universities'\n    ];", f"'Universities', '{name}'\n    ];")
    _replace_once(ROOT / "app" / "templates" / "signup.html", f"all {old} checks unlocked", f"all {new} checks unlocked")
    _replace_once(ROOT / "app" / "templates" / "area_guide.html", f"Run the {old} checks", f"Run the {new} checks")
    _replace_once(ROOT / "app" / "templates" / "running_costs.html", f"its {WORDS[old]} other checks", f"its {WORDS[new]} other checks")
    _replace_once(index, f"full list of {old}", f"full list of {new}")
    print("By hand: the free/Premium split on the landing page and the pricing page (N free on every report, N more with Premium), and the pricing page's own list of checks.")
    print(f"check count {old} -> {new}" + (f", named {name!r}" if name else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
