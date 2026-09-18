"""Copy rules shared by audit_site.py and the test suite.

audit_site.py fetches pages the moment it is imported, so the patterns
live here where a test can load them without the network.

Dates in the site's own form (18 Sep 2026): "3 Jul 2026" for a day and
"July 2026" for a month, from the day_label and month_label filters,
never "2026-07-03" or "2026-07". Sale dates on reports and comparables,
the base rate history, GP list dates, the council finances line, the
market report and the accuracy log all printed the ISO form, which reads
as a database dump, and nothing checked. A financial year such as
"2008-09" is not a date: its second half is the next year, which an ISO
month never is from 2012 on.

Counts with a thousands separator: "1330 crimes recorded" sat on the
SE15 report beside "£2,402" and "1,657 residents" on the same card.
"""
import re

ISO_DAY = re.compile(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b")
ISO_MONTH = re.compile(r"\b((?:19|20)\d{2})-(0[1-9]|1[0-2])\b(?!-)")
BARE_COUNT = re.compile(
    r"(?<![\d,.£$#])(\d{4,})(?![\d,.])\s+"
    r"(crimes?|sales|homes|schools|pupils|residents|households|people|dwellings|"
    r"records|offences|properties|patients|views|reports|accounts|sites)\b"
)


def date_and_count_problems(text: str) -> list[tuple[str, str]]:
    """(rule, what was found) for each date in ISO form and each count
    without a thousands separator in one block of visible copy."""
    found = [("date in ISO form", m.group(0)) for m in ISO_DAY.finditer(text)]
    for m in ISO_MONTH.finditer(text):
        year, month = int(m.group(1)), int(m.group(2))
        if month == (year + 1) % 100:
            continue
        found.append(("date in ISO form", m.group(0)))
    for m in BARE_COUNT.finditer(text):
        if 1900 <= int(m.group(1)) <= 2099:
            continue
        found.append(("count without a thousands separator", " ".join(m.group(0).split())))
    return found
