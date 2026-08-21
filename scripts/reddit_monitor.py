"""Daily scan of UK property subreddits for threads worth replying to.

READ-ONLY. This script never posts, votes, or logs in - it reads public
RSS feeds and writes a Markdown digest to marketing/reddit/. You read the
digest, pick the two or three threads genuinely worth answering, and write
the reply yourself.

That restraint is deliberate, not a limitation: Reddit detects and
domain-bans promotional automation, and a ukpropertyinsight.co.uk
blacklist would silently remove other people's recommendations too.

Reddit closed its anonymous .json endpoints (they 403 regardless of
User-Agent), so this reads the Atom feeds at /r/<sub>/new.rss, which are
still open. They are rate limited hard - a burst of requests gets 429s -
so every fetch retries with a long backoff and the whole run takes a
couple of minutes. That is expected, not a fault.

RSS carries no comment count, so a thread that has already been answered
twenty times looks the same as a fresh one here. Open the link before
replying.

Usage (from the repo root):
    .venv/Scripts/python.exe scripts/reddit_monitor.py
    .venv/Scripts/python.exe scripts/reddit_monitor.py --days 7 --min-score 2

Threads shown in a previous run are skipped, so running it daily only
surfaces new material. Delete marketing/reddit/seen.json to start over.
"""
import argparse
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(REPO_ROOT, "marketing", "reddit")
SEEN_PATH = os.path.join(OUTPUT_DIR, "seen.json")

# Reddit's API rules ask for a descriptive User-Agent naming the tool.
HEADERS = {
    "User-Agent": "windows:ukpropertyinsight-thread-monitor:v1.0 (read-only digest)"
}

ATOM = "{http://www.w3.org/2005/Atom}"

# Reddit 429s the first request of a run surprisingly often, then serves
# the retry fine. A few attempts with a long wait covers it.
MAX_ATTEMPTS = 4
RETRY_WAIT = 20
BETWEEN_SUBS = 8

# Edit freely - these are the subs where people actually ask the questions
# the site answers. A quiet sub is worth keeping anyway: it costs one
# request a day.
SUBREDDITS = [
    "HousingUK",
    "UKPersonalFinance",
    "PropertyInvestingUK",
    "uklandlords",
    "LegalAdviceUK",
    "AskUK",
]

# Your own Reddit username(s), so your own posts don't come back as
# threads to reply to - the first run put the r/HousingUK launch post at
# the top of the digest. Format: "/u/yourname".
EXCLUDE_AUTHORS = [
    # "/u/your_username_here",
]

# A thread scores one point per group matched. Grouping matters more than
# raw keyword count: a post saying "flood" four times is still one flood
# question, while one touching flood + EPC + crime is someone doing
# exactly the research the site exists for.
KEYWORD_GROUPS = {
    "sold prices": [
        r"sold pric\w*", r"land registry", r"price paid", r"sold for",
        r"comparable sales", r"what did it sell for", r"previous sale",
    ],
    "flood": [
        r"flood risk", r"flood zone", r"flooding", r"flood insurance", r"flooded",
    ],
    "epc": [
        r"\bepc\b", r"energy rating", r"energy performance", r"energy efficiency",
    ],
    "crime": [
        r"crime rate", r"crime stat\w*", r"safe area", r"rough area",
        r"dodgy area", r"is .{0,20}area safe", r"antisocial", r"anti-social",
    ],
    "schools": [
        r"catchment", r"ofsted", r"school place", r"good schools",
    ],
    "connectivity": [
        r"broadband speed", r"full fibre", r"mobile signal", r"phone signal",
        r"no signal",
    ],
    "noise": [
        r"noise", r"flight path", r"flightpath", r"aircraft noise",
        r"road noise", r"noisy neighbour\w*",
    ],
    "environment": [
        r"\bradon\b", r"air quality", r"subsidence", r"contaminated land",
    ],
    "area research": [
        r"due diligence", r"before (i|we) buy", r"what (should i|to) check",
        r"research\w* (the |an )?area", r"local searches", r"conveyancing searches",
        r"good area", r"worth buying", r"red flags",
    ],
}

# Starting points only. Never post one of these unedited - identical
# boilerplate across threads is the fastest way to get flagged as spam,
# by mods and readers alike. The [brackets] are there to force an edit.
DRAFT_TEMPLATES = {
    "sold prices": (
        "Land Registry price paid data is free and covers every sale in "
        "England and Wales since 1995 - [answer their specific question "
        "using it here]. Worth looking at the whole sale history rather than "
        "just the last price; somewhere that's changed hands three times in "
        "six years is telling you something."
    ),
    "flood": (
        "The Environment Agency's flood risk service is the authoritative one "
        "here and it's free - [answer their specific question]. Worth knowing "
        "surface water flooding is assessed separately from rivers and sea, "
        "and it's the one that catches people out on insurance."
    ),
    "epc": (
        "Every EPC since 2008 is on the open EPC register, so you can look up "
        "the certificate for a specific address before you even view it - "
        "[answer their question]. The recommendations page at the back is the "
        "useful bit: it lists what the assessor thought needed doing and "
        "roughly what it costs."
    ),
    "crime": (
        "police.uk publishes street-level crime data monthly, which beats "
        "anecdote for this - [answer their question]. One caveat worth "
        "flagging: it's mapped to anonymised points, so a town centre with "
        "nightlife always looks worse than a residential street half a mile "
        "away."
    ),
    "schools": (
        "[Answer their question first.] Ofsted ratings and the DfE performance "
        "tables are both free to search. Catchments are the harder part - most "
        "councils publish last year's furthest distance offered rather than a "
        "fixed boundary, so it moves year to year."
    ),
    "connectivity": (
        "Ofcom's Connected Nations data gives coverage down to individual "
        "postcode level for both broadband and mobile - [answer their "
        "question]. More reliable than the providers' own postcode checkers, "
        "which tend to be optimistic."
    ),
    "noise": (
        "[Answer their question first.] Defra publishes strategic noise "
        "mapping for road, rail and air, which is worth a look before "
        "committing - it won't capture a barking dog but it will capture a "
        "dual carriageway or a flight path."
    ),
    "environment": (
        "[Answer their question first.] The UKHSA radon map covers this by "
        "postcode and it's free - most of the country is low risk, but parts "
        "of the South West, Derbyshire and Northamptonshire aren't."
    ),
    "area research": (
        "[Answer their specific question first - this one needs a real answer, "
        "not a checklist.] For what it's worth, the things that actually catch "
        "people out tend to be flood risk, the EPC recommendations, and the "
        "sale history rather than anything the estate agent volunteers."
    ),
}

MENTION_NOTE = (
    "If - and only if - the link genuinely answers what they asked, a closing "
    "line like \"I built a free tool that pulls this together by postcode if "
    "it saves you the digging\" is fine. Answer first, mention second, and "
    "skip the mention entirely on most threads."
)

INTERROGATIVES = re.compile(
    r"\?|^(how|what|where|should|is|are|does|do|can|any|would|anyone)\b",
    re.IGNORECASE,
)

TAG_RE = re.compile(r"<[^>]+>")


def load_seen() -> set:
    if not os.path.exists(SEEN_PATH):
        return set()
    try:
        with open(SEEN_PATH, "r", encoding="utf-8") as fh:
            return set(json.load(fh))
    except (json.JSONDecodeError, OSError):
        print(f"Could not read {SEEN_PATH} - treating everything as new.")
        return set()


def save_seen(seen: set) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # Keep the file from growing without limit - ids only matter for as
    # long as a thread is still worth replying to.
    trimmed = sorted(seen)[-5000:]
    with open(SEEN_PATH, "w", encoding="utf-8") as fh:
        json.dump(trimmed, fh, indent=0)


def strip_html(raw: str) -> str:
    """RSS content is HTML, double-escaped. Flatten it to plain text."""
    text = html.unescape(raw or "")
    text = TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_subreddit(name: str, limit: int = 100) -> list:
    """Return normalised post dicts for one subreddit, or [] on failure."""
    url = f"https://www.reddit.com/r/{name}/new.rss?limit={limit}"
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = httpx.get(url, headers=HEADERS, timeout=30, follow_redirects=True)
        except httpx.HTTPError as exc:
            print(f"  r/{name}: request failed ({exc})")
            return []
        if resp.status_code == 200:
            return parse_feed(name, resp.text)
        if resp.status_code == 404:
            print(f"  r/{name}: no such subreddit - remove it from SUBREDDITS")
            return []
        if resp.status_code == 403:
            print(f"  r/{name}: private or quarantined, skipping")
            return []
        if resp.status_code == 429 and attempt < MAX_ATTEMPTS - 1:
            time.sleep(RETRY_WAIT)
            continue
        print(f"  r/{name}: HTTP {resp.status_code} after {attempt + 1} attempt(s)")
        return []
    return []


def parse_feed(name: str, xml_text: str) -> list:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"  r/{name}: could not parse feed ({exc})")
        return []

    posts = []
    for entry in root.findall(f"{ATOM}entry"):
        raw_id = entry.findtext(f"{ATOM}id", default="")
        # Reddit ids arrive as "t3_abc123"; the bare id is the useful part.
        post_id = raw_id.split("_", 1)[-1] if raw_id else ""
        if not post_id:
            continue
        link_el = entry.find(f"{ATOM}link")
        updated = entry.findtext(f"{ATOM}updated", default="")
        try:
            created = datetime.fromisoformat(updated)
        except ValueError:
            continue
        posts.append({
            "id": post_id,
            "title": entry.findtext(f"{ATOM}title", default="").strip(),
            "body": strip_html(entry.findtext(f"{ATOM}content", default="")),
            "author": entry.findtext(f"{ATOM}author/{ATOM}name", default=""),
            "url": link_el.get("href") if link_el is not None else "",
            "subreddit": name,
            "created": created,
        })
    return posts


def match_groups(text: str) -> list:
    hits = []
    for group, patterns in KEYWORD_GROUPS.items():
        if any(re.search(p, text, re.IGNORECASE) for p in patterns):
            hits.append(group)
    return hits


def age_hours(created: datetime) -> float:
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds() / 3600


def score_post(post: dict, groups: list) -> int:
    score = len(groups)
    if INTERROGATIVES.search(post["title"]):
        score += 1
    # A question with some detail is far more answerable than a one-line
    # title, and far more likely to welcome a real reply.
    if len(post["title"]) + len(post["body"]) > 400:
        score += 1
    return score


def build_digest(candidates: list, days: float) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"# Reddit digest - {today}",
        "",
        f"{len(candidates)} new thread(s) from the last {days:g} day(s) "
        "matching the topics the site covers.",
        "",
        "**Read before posting anything:** answer the question properly or "
        "don't reply at all. The drafts below are starting points with gaps "
        "you have to fill - posting them as-is across several threads is what "
        "gets accounts and domains banned. Two good replies a week beats "
        "twenty generic ones. Open each link first: this feed carries no "
        "comment count, so an already-answered thread looks identical to a "
        "fresh one here.",
        "",
        "---",
        "",
    ]
    if not candidates:
        lines.append(
            "Nothing new worth a reply today. That's a normal result - most "
            "days there genuinely isn't anything."
        )
        return "\n".join(lines)

    for i, item in enumerate(candidates, 1):
        post = item["post"]
        groups = item["groups"]
        hours = item["age_hours"]
        age = f"{hours:.0f}h ago" if hours < 48 else f"{hours / 24:.0f}d ago"
        body = post["body"] or "(link post, no text)"
        if len(body) > 400:
            body = body[:400] + "..."

        lines += [
            f"## {i}. {post['title']}",
            "",
            f"- **r/{post['subreddit']}** | {age} | score {item['score']}",
            f"- **Topics:** {', '.join(groups)}",
            f"- **Link:** {post['url']}",
            "",
            f"> {body}",
            "",
            "**Draft (edit before posting):**",
            "",
            DRAFT_TEMPLATES.get(groups[0], "[Write a reply from scratch.]"),
            "",
            f"_{MENTION_NOTE}_",
            "",
            "---",
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only Reddit digest for ukpropertyinsight.co.uk"
    )
    parser.add_argument(
        "--days", type=float, default=3,
        help="only consider threads posted in the last N days (default 3)",
    )
    parser.add_argument(
        "--min-score", type=int, default=2,
        help="drop threads scoring below this (default 2)",
    )
    parser.add_argument(
        "--limit", type=int, default=30,
        help="maximum threads in the digest (default 30)",
    )
    parser.add_argument(
        "--ignore-seen", action="store_true",
        help="include threads shown in previous runs",
    )
    args = parser.parse_args()

    previously_seen = load_seen()
    skip = set() if args.ignore_seen else previously_seen
    all_ids = set(previously_seen)
    excluded = {a.lower() for a in EXCLUDE_AUTHORS}
    candidates = []

    print(f"Scanning {len(SUBREDDITS)} subreddits (slow by design)...")
    for idx, name in enumerate(SUBREDDITS):
        posts = fetch_subreddit(name)
        kept = 0
        for post in posts:
            all_ids.add(post["id"])
            if post["id"] in skip:
                continue
            if post["author"].lower() in excluded:
                continue
            hours = age_hours(post["created"])
            if hours > args.days * 24:
                continue
            groups = match_groups(f"{post['title']} {post['body']}")
            if not groups:
                continue
            score = score_post(post, groups)
            if score < args.min_score:
                continue
            candidates.append(
                {"post": post, "groups": groups, "score": score, "age_hours": hours}
            )
            kept += 1
        if posts:
            print(f"  r/{name}: {len(posts)} scanned, {kept} worth a look")
        if idx < len(SUBREDDITS) - 1:
            time.sleep(BETWEEN_SUBS)

    candidates.sort(key=lambda c: (-c["score"], c["age_hours"]))
    candidates = candidates[: args.limit]

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    out_path = os.path.join(OUTPUT_DIR, f"digest-{today}.md")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(build_digest(candidates, args.days))

    save_seen(all_ids)

    print(f"\n{len(candidates)} thread(s) in the digest.")
    print(f"Written to {out_path}")
    for item in candidates[:5]:
        print(f"  [{item['score']}] r/{item['post']['subreddit']}: "
              f"{item['post']['title'][:70]}")


if __name__ == "__main__":
    main()
