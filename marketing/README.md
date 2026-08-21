# Marketing

## Reddit thread monitor

Finds UK property threads where the site's data would genuinely answer the
question, so you're not scrolling subreddits looking for them. Run it daily:

```
.venv/Scripts/python.exe scripts/reddit_monitor.py
```

It writes `marketing/reddit/digest-YYYY-MM-DD.md` — a shortlist with the
thread link, what they asked, and a rough draft to start from. Read it,
pick the two or three worth answering, post in your own words.

The run takes a couple of minutes. That's deliberate: Reddit rate limits
its RSS feeds hard, so the script waits between subreddits rather than
hammering them.

### First thing to do

Open `scripts/reddit_monitor.py` and put your Reddit username in
`EXCLUDE_AUTHORS` near the top, otherwise your own posts show up in your
own digest.

### What it will not do

Post, reply, vote, or log in. Reddit domain-bans sites caught automating
promotion, and that ban would also silently remove other people's genuine
recommendations of ukpropertyinsight.co.uk. Every reply goes out from your
account, written by you.

### Tuning

Everything worth changing sits at the top of the script:

- `SUBREDDITS` — add or remove subs
- `KEYWORD_GROUPS` — the topics that make a thread interesting
- `DRAFT_TEMPLATES` — the starting-point replies

Useful flags: `--days 7` to look further back, `--min-score 3` for a
shorter and stricter list, `--ignore-seen` to re-include threads from
previous runs.
