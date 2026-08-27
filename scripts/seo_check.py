"""Is the site actually indexable, and is what it offers Google distinct?

Search Console answers "did Google index this" weeks after the fact.
This answers the part that is ours to control, today: that every URL we
submit resolves, canonicals to itself, is not quietly noindexed, and
carries a title, description and H1 that are not the same as the next
district's.

    .venv/Scripts/python.exe scripts/seo_check.py
    .venv/Scripts/python.exe scripts/seo_check.py --sample 120

Samples the sitemap rather than walking all 1,734 URLs: this is a check,
not a crawl, and hammering the live site to prove it is healthy is
self-defeating.

The duplication measure is shingles (overlapping 8-word runs), not a
bag-of-words overlap. Two area guides share almost every word by
construction - the same headings, the same source names, the same
sentence frames - so word overlap reads ~90% for pages Google is right
to treat as distinct. Shared *phrases* is what near-duplicate detection
actually keys on.
"""
import argparse
import collections
import json
import random
import re
import sys
import time
import xml.etree.ElementTree as ET

import httpx

# Googlebot, because that is whose experience is being measured: the
# report page serves crawlers the finished render and everyone else a
# "building your report" placeholder.
UA = {"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}

SHINGLE = 8            # words per shingle
DUPLICATE_ALARM = 0.30  # share of shingles in common before it is a worry
PAUSE_S = 0.3


def family(url: str) -> str:
    path = re.sub(r"^https?://[^/]+", "", url)
    if path.startswith("/area/") and path.endswith("/private-schools"):
        return "/area/*/private-schools"
    if path.startswith("/area/"):
        return "/area/*"
    if path.startswith("/schools/guide"):
        return "/schools/guide"
    if path.startswith("/school/"):
        return "/school/*"
    return "static"


def shingles(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(words[i:i + SHINGLE]) for i in range(len(words) - SHINGLE + 1)}


def visible(body: str) -> str:
    body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", body, flags=re.S | re.I)
    return re.sub(r"<[^>]+>", " ", body)


def one(field: str, body: str) -> str:
    patterns = {
        "title": r"<title[^>]*>(.*?)</title>",
        "description": r'<meta\s+name="description"\s+content="([^"]*)"',
        "canonical": r'<link\s+rel="canonical"\s+href="([^"]*)"',
        "robots": r'<meta\s+name="robots"\s+content="([^"]*)"',
    }
    m = re.search(patterns[field], body, re.S | re.I)
    return (m.group(1).strip() if m else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?", default="https://ukpropertyinsight.co.uk")
    ap.add_argument("--sample", type=int, default=80)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    client = httpx.Client(timeout=60, headers=UA, follow_redirects=False)
    problems = collections.defaultdict(list)

    print(f"seo check: {base}\n")

    # ---- robots.txt ----
    r = client.get(f"{base}/robots.txt")
    robots_body = r.text if r.status_code == 200 else ""
    print(f"robots.txt          {r.status_code}")
    if "Sitemap:" not in robots_body:
        problems["robots"].append("no Sitemap: line, so a crawler has to guess the location")
    for blocked in re.findall(r"Disallow:\s*(\S+)", robots_body):
        print(f"  disallows         {blocked}")

    # ---- sitemap ----
    r = client.get(f"{base}/sitemap.xml")
    if r.status_code != 200:
        print(f"sitemap.xml         {r.status_code}  FATAL, nothing else can be checked")
        return 1
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as exc:
        print(f"sitemap.xml         does not parse: {exc}")
        return 1
    urls = [e.text for e in root.findall(".//s:loc", NS)]
    print(f"sitemap.xml         {len(urls)} URLs, {len(set(urls))} distinct")
    if len(urls) != len(set(urls)):
        problems["sitemap"].append(f"{len(urls) - len(set(urls))} duplicate URLs")
    off = [u for u in urls if not u.startswith(base + "/") and u != base + "/"]
    if off:
        problems["sitemap"].append(f"{len(off)} URLs point off the canonical host, e.g. {off[0]}")

    by_family = collections.defaultdict(list)
    for u in urls:
        by_family[family(u)].append(u)
    print("\n  family                        in sitemap   sampled")
    sample = []
    for fam, members in sorted(by_family.items()):
        take = min(len(members), max(4, args.sample * len(members) // max(1, len(urls))))
        picked = random.sample(members, take)
        sample.extend(picked)
        print(f"    {fam:<28} {len(members):>8}   {take:>7}")

    # ---- fetch the sample ----
    print(f"\nfetching {len(sample)} pages as Googlebot...")
    pages = {}
    for i, url in enumerate(sample, 1):
        try:
            resp = client.get(url)
        except Exception as exc:  # noqa: BLE001
            problems["unreachable"].append(f"{url}: {exc}")
            continue
        if resp.status_code != 200:
            problems["not 200"].append(f"{url} -> {resp.status_code}")
            continue
        pages[url] = resp.text
        if i % 20 == 0:
            print(f"  {i}/{len(sample)}")
        time.sleep(PAUSE_S)

    # ---- per-page checks ----
    titles, descriptions = collections.defaultdict(list), collections.defaultdict(list)
    for url, body in pages.items():
        title, desc = one("title", body), one("description", body)
        canonical, robots_meta = one("canonical", body), one("robots", body)

        if "noindex" in robots_meta.lower():
            problems["noindex but in the sitemap"].append(url)
        if not canonical:
            problems["no canonical"].append(url)
        elif canonical.rstrip("/") != url.rstrip("/"):
            problems["canonical points elsewhere"].append(f"{url} -> {canonical}")
        if not title:
            problems["no title"].append(url)
        elif len(title) > 65:
            problems["title over 65 chars (Google truncates)"].append(f"{len(title)}  {title[:70]}")
        if not desc:
            problems["no meta description"].append(url)
        elif len(desc) > 165:
            problems["description over 165 chars"].append(f"{len(desc)}  {url}")

        h1s = re.findall(r"<h1[^>]*>(.*?)</h1>", body, re.S | re.I)
        if len(h1s) != 1:
            problems[f"{len(h1s)} h1 elements"].append(url)

        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S):
            try:
                json.loads(block)
            except ValueError as exc:
                problems["invalid JSON-LD"].append(f"{url}: {exc}")

        titles[title].append(url)
        descriptions[desc].append(url)

    for title, where in titles.items():
        if len(where) > 1 and title:
            problems["duplicate title"].append(f"{len(where)}x  {title[:60]}")
    for desc, where in descriptions.items():
        if len(where) > 1 and desc:
            problems["duplicate meta description"].append(f"{len(where)}x  {where[0]}")

    # ---- near-duplicate body text, within each family ----
    print("\nnear-duplicate check (shared 8-word phrases, within family):")
    for fam, members in sorted(by_family.items()):
        have = [u for u in members if u in pages]
        if len(have) < 2:
            continue
        if fam == "static":
            # Not a family: 16 unrelated pages that are supposed to
            # differ from each other. Measuring /premium against
            # /compare and calling the overlap duplication is noise,
            # and Google is not choosing between them.
            continue
        pairs, worst, worst_pair = [], 0.0, None
        for i in range(min(len(have), 8)):
            for j in range(i + 1, min(len(have), 8)):
                a, b = shingles(visible(pages[have[i]])), shingles(visible(pages[have[j]]))
                if not a or not b:
                    continue
                overlap = len(a & b) / min(len(a), len(b))
                pairs.append(overlap)
                if overlap > worst:
                    worst, worst_pair = overlap, (have[i], have[j])
        if not pairs:
            continue
        avg = sum(pairs) / len(pairs)
        flag = "  <-- too similar" if avg > DUPLICATE_ALARM else ""
        print(f"  {fam:<28} avg {avg * 100:>5.1f}%   worst {worst * 100:>5.1f}%{flag}")
        if avg > DUPLICATE_ALARM and worst_pair:
            problems["near-duplicate pages"].append(
                f"{fam}: {avg * 100:.0f}% shared phrasing, worst pair "
                f"{worst_pair[0].split('/')[-1]} vs {worst_pair[1].split('/')[-1]}"
            )

    # ---- report ----
    print(f"\n{'=' * 70}")
    if not problems:
        print(f"clean: {len(pages)} pages sampled, nothing to fix")
        return 0
    total = sum(len(v) for v in problems.values())
    print(f"{total} issue(s) across {len(problems)} kinds, {len(pages)} pages sampled")
    for kind, items in sorted(problems.items(), key=lambda kv: -len(kv[1])):
        print(f"\n{kind.upper()}  ({len(items)})")
        for item in items[:6]:
            print(f"  {item}")
        if len(items) > 6:
            print(f"  ... and {len(items) - 6} more")
    return 1


if __name__ == "__main__":
    sys.exit(main())
