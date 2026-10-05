"""Can someone using a screen reader or a keyboard use the pages people
land on? axe-core in a real browser, at desktop and phone width.

    .venv/Scripts/python.exe scripts/check_a11y.py
    .venv/Scripts/python.exe scripts/check_a11y.py http://localhost:8012

audit_site.py reads the HTML and catches the basics (alt text, labels,
one h1). It cannot see what only exists once the page runs: a Google
map that draws its markers as nameless buttons, a table that scrolls
sideways on a phone with no way to reach it by keyboard, a typeahead
whose ARIA only a browser evaluates. On 5 Oct 2026 this found six kinds
of fault on production, two of them critical, that every other check
here had passed for weeks; all six were fixed the same day.

Run it once a month with check_freshness.py, and after any change to a
map, a table or a form. Needs Playwright and Microsoft Edge (or set
PV_CHANNEL=chrome), and fetches axe-core from cdnjs each run. Sends
X-Internal-Check so none of it reaches /admin. Exit status 1 when
anything is found.
"""
import collections
import os
import sys

import httpx
from playwright.sync_api import sync_playwright

AXE_URLS = (
    "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js",
    "https://cdn.jsdelivr.net/npm/axe-core@4.10.2/axe.min.js",
)
# The page types people arrive on, plus the page that sells Premium.
PAGES = [
    ("home", "/"),
    ("school page", "/school/151031/marple-hall-school"),
    ("area guide", "/area/M20"),
    ("council tax", "/running-costs/council-tax/ashford"),
    ("admissions hub", "/schools/admissions/stockport"),
    ("entrance tests", "/schools/entrance-tests"),
    ("comparison", "/compare/BH1/vs/BH4"),
    ("schools guide", "/schools/guide?q=M20"),
    ("premium", "/premium"),
    ("methodology", "/methodology"),
]
WIDTHS = (1280, 375)
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"]
ORDER = {"critical": 0, "serious": 1, "moderate": 2, "minor": 3}
RUN_AXE = """async (tags) => {
    const result = await axe.run(document, {resultTypes: ['violations'],
                                            runOnly: {type: 'tag', values: tags}});
    return result.violations.map(v => ({id: v.id, impact: v.impact, help: v.help,
        nodes: v.nodes.length,
        sample: v.nodes.slice(0, 2).map(n => n.target.join(' ') + ' :: '
            + (n.failureSummary || '').split('\\n').slice(0, 2).join(' '))}));
}"""


def out(text=""):
    sys.stdout.buffer.write((str(text) + "\n").encode("utf-8", "replace"))


def axe_source() -> str:
    for url in AXE_URLS:
        try:
            reply = httpx.get(url, timeout=60)
        except httpx.HTTPError:
            continue
        if reply.status_code == 200 and "axe" in reply.text[:2000]:
            return reply.text
    raise SystemExit("could not fetch axe-core from cdnjs or jsdelivr")


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "https://ukpropertyinsight.co.uk").rstrip("/")
    source = axe_source()
    by_rule = collections.defaultdict(lambda: {"impact": "", "help": "", "views": [], "nodes": 0, "sample": []})
    out(f"accessibility check: {base}, {len(PAGES)} pages at {', '.join(map(str, WIDTHS))} px\n")
    with sync_playwright() as play:
        browser = play.chromium.launch(channel=os.environ.get("PV_CHANNEL", "msedge"))
        for width in WIDTHS:
            ctx = browser.new_context(viewport={"width": width, "height": 900}, is_mobile=width < 600,
                                      extra_http_headers={"X-Internal-Check": "1"})
            for label, path in PAGES:
                page = ctx.new_page()
                page.goto(base + path, wait_until="load", timeout=120000)
                # Long enough for a map and the scroll-region script to run.
                page.wait_for_timeout(1500)
                page.add_script_tag(content=source)
                for v in page.evaluate(RUN_AXE, TAGS):
                    rule = by_rule[v["id"]]
                    rule["impact"], rule["help"] = v["impact"], v["help"]
                    rule["views"].append(f"{label} @{width}")
                    rule["nodes"] += v["nodes"]
                    if len(rule["sample"]) < 3:
                        rule["sample"].extend(v["sample"][:1])
                page.close()
            ctx.close()
        browser.close()

    if not by_rule:
        out("clean: no violations")
        return 0
    for rule_id, r in sorted(by_rule.items(), key=lambda kv: (ORDER.get(kv[1]["impact"], 9), -kv[1]["nodes"])):
        out(f"[{r['impact']}] {rule_id}: {r['help']}  ({r['nodes']} elements, {len(r['views'])} page views)")
        out(f"    on: {', '.join(r['views'][:6])}{' ...' if len(r['views']) > 6 else ''}")
        for sample in r["sample"]:
            out(f"    e.g. {sample[:220]}")
        out()
    out(f"{len(by_rule)} kind(s) of fault")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
