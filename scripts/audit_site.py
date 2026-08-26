"""Full-site audit: every internal link, and every word of user-facing copy.

Checks, in order:
  1. Every internal href on every rendered page resolves (no 404/500).
  2. The project's copy rules: no em-dashes in user-facing text.
  3. Stale numbers: the check count must be 37 everywhere it appears.
  4. Common typos and doubled words.
  5. Placeholder text that should never ship (lorem, TODO, FIXME, XXX).
  6. Accessibility basics: every img has alt, every input has a label,
     every page has exactly one h1 and a non-empty title.

Runs against the dev server so it sees the real rendered output rather
than the templates.
"""
import html
import re
import sys
from collections import defaultdict

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
UA = {"User-Agent": "Googlebot/2.1 (+audit)"}

PAGES = [
    "/", "/areas", "/area/M1", "/area/SW1A", "/schools/guide?q=M1",
    "/schools/shortlist", "/buying-guide", "/browser-extension", "/premium",
    "/accuracy", "/data", "/methodology", "/support", "/terms", "/privacy",
    "/market-report", "/login", "/signup", "/forgot-password", "/embed",
    "/property?postcode=M1+1AE", "/property/comparables?postcode=M1+1AE",
    "/watchlist", "/watchlist/compare", "/no-such-page-404",
    # A Scottish postcode renders the data-gap disclaimers, which no
    # English address shows. They sat outside every audit until they
    # turned out to be carrying em-dashes (28 Aug 2026), so both the
    # property and the area-guide versions are now in the sample.
    "/property?postcode=EH1+1YZ", "/area/EH1",
    "/compare?postcode=M1+1AE&postcode=LS1+4DY",
]

client = httpx.Client(timeout=25.0, headers=UA, follow_redirects=True)

problems = defaultdict(list)
pages_html = {}

print("fetching pages...")
for path in PAGES:
    try:
        r = client.get(BASE + path)
        pages_html[path] = r.text
        if r.status_code >= 500:
            problems["server errors"].append(f"{path} -> {r.status_code}")
    except Exception as exc:  # noqa: BLE001
        problems["fetch failed"].append(f"{path}: {exc}")

# ---- 1. internal links -------------------------------------------------
print("checking links...")
links = defaultdict(set)
for path, body in pages_html.items():
    for href in re.findall(r'href="([^"#][^"]*)"', body):
        if href.startswith(("http://", "https://", "mailto:", "tel:", "javascript:", "data:")):
            continue
        links[href.split("#")[0]].add(path)

# /areas links to all 2,943 outcode guides. Checking every one means
# 2,943 cold renders, which is a load test rather than an audit - so
# repetitive families are sampled instead.
def family(h):
    m = re.match(r"^(/area|/property|/schools/guide)", h)
    return m.group(1) if m else None

seen_family = {}
targets = []
for href in sorted(links):
    if not href.startswith("/"):
        continue
    # /property/pdf renders a whole PDF per request, so a link check
    # there is a minutes-long job, not a status code. Same for the
    # CSV and ICS exports.
    if re.search(r'/pdf|\.csv|\.ics|/logout', href):
        continue
    if href.split('?')[0] in pages_html or href in pages_html:
        continue                      # already fetched above
    f = family(href)
    if f:
        seen_family[f] = seen_family.get(f, 0) + 1
        if seen_family[f] > 3:
            continue
    targets.append(href)
# Belt and braces on the sampling above: whatever happens, this is an
# audit, not a crawl.
targets = targets[:150]
print(f"  {len(links)} distinct hrefs, checking {len(targets)} "
      f"(repetitive families sampled)")

checked = {}
for href in targets:
    try:
        r = client.head(BASE + href)
        if r.status_code == 405:            # some routes refuse HEAD
            r = client.get(BASE + href)
        checked[href] = r.status_code
        if r.status_code >= 400:
            problems["broken links"].append(
                f"{href} -> {r.status_code}  (linked from {', '.join(sorted(links[href])[:3])})")
    except Exception as exc:  # noqa: BLE001
        problems["broken links"].append(f"{href}: {exc}")

# ---- 2..5 copy rules ---------------------------------------------------
print("checking copy...")


def visible_text(body):
    """Strip script, style and tags so only what a reader sees is left."""
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    return html.unescape(body)


TYPOS = [
    (r"\bteh\b", "teh"), (r"\brecieve", "recieve"), (r"\bseperate", "seperate"),
    (r"\boccured", "occured"), (r"\bdefinately", "definately"),
    (r"\baccomodat", "accomodat"), (r"\bpubicly\b", "pubicly"),
    (r"\benviroment", "enviroment"), (r"\bproperites\b", "properites"),
    (r"\bthier\b", "thier"), (r"\bwich\b", "wich"), (r"\bsucessful", "sucessful"),
]
PLACEHOLDERS = [r"\blorem ipsum\b", r"\bTODO\b", r"\bFIXME\b", r"\bXXX\b", r"\bTBD\b"]

for path, body in pages_html.items():
    text = visible_text(body)

    if "—" in text:
        for m in re.finditer(r".{40}—.{40}", text):
            problems["em-dash in copy"].append(f"{path}: ...{m.group(0).strip()}...")

    for pattern, name in TYPOS:
        if re.search(pattern, text, re.I):
            problems["typo"].append(f"{path}: {name}")

    for pattern in PLACEHOLDERS:
        if re.search(pattern, text):
            problems["placeholder text"].append(f"{path}: {pattern}")

    for m in re.finditer(r"\b(\w{4,})\s+\1\b", text, re.I):
        problems["doubled word"].append(f"{path}: '{m.group(0)}'")

    # stale counts: 23 or 38 checks should no longer appear
    for m in re.finditer(r"\b(\d+)\s+checks?\b", text, re.I):
        n = m.group(1)
        if n not in ("37", "22", "15", "1", "2", "3", "4", "5"):
            problems["check count"].append(f"{path}: '{m.group(0)}'")
    if re.search(r"twenty[- ]three checks", text, re.I):
        problems["check count"].append(f"{path}: 'twenty-three checks'")

# ---- 6. accessibility basics -------------------------------------------
print("checking accessibility basics...")
for path, body in pages_html.items():
    for img in re.findall(r"<img\b[^>]*>", body):
        if "alt=" not in img:
            problems["img without alt"].append(f"{path}: {img[:70]}")

    h1s = re.findall(r"<h1\b[^>]*>(.*?)</h1>", body, re.S)
    if len(h1s) == 0:
        problems["h1 count"].append(f"{path}: none")
    elif len(h1s) > 1:
        problems["h1 count"].append(f"{path}: {len(h1s)}")

    title = re.search(r"<title>(.*?)</title>", body, re.S)
    if not title or not title.group(1).strip():
        problems["empty title"].append(path)

    desc = re.search(r'<meta name="description" content="([^"]*)"', body)
    if not desc or len(desc.group(1).strip()) < 50:
        problems["weak meta description"].append(path)

    for inp in re.findall(r'<input\b[^>]*type="(?:text|email|password|search|number)"[^>]*>', body):
        has_id = re.search(r'id="([^"]+)"', inp)
        labelled = has_id and f'for="{has_id.group(1)}"' in body
        if not labelled and "aria-label" not in inp:
            problems["input without label"].append(f"{path}: {inp[:70]}")

# ---- report ------------------------------------------------------------
print("\n" + "=" * 68)
print(f"AUDIT: {len(pages_html)} pages, {len(checked)} distinct internal links")
print("=" * 68)
if not problems:
    print("\nclean")
for kind in sorted(problems):
    items = sorted(set(problems[kind]))
    print(f"\n{kind.upper()}  ({len(items)})")
    for line in items[:14]:
        print("  " + line)
    if len(items) > 14:
        print(f"  ... and {len(items) - 14} more")

print(f"\ntotal issue groups: {len(problems)}")
