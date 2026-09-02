"""Write docs/press/press-kit.md from the live admission-distance data.

The kit is what Michael sends, not what the site shows: a national
press release with the real numbers filled in, three pitch emails
(local paper, education press, parents' forum) and one ready-made
paragraph per council so a local pitch takes a copy and a paste.
Re-run after any admissions import so the figures match the page:

    .venv/Scripts/python.exe scripts/press_kit.py

Source of every figure: /schools/tightest-catchments, which in turn
names the council publication behind each distance.
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from app.services import schools_db  # noqa: E402

SITE = "https://ukpropertyinsight.co.uk"
OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "press" / "press-kit.md"


def school_link(s: dict) -> str:
    return f"[{s['name']}]({SITE}/school/{s['urn']}/{s['slug']})"


def council_link(c: dict) -> str:
    return f"[{c['name']}]({SITE}/schools/admissions/{c['slug']})"


def main() -> None:
    d = schools_db.tightest_catchments()
    today = dt.date.today().strftime("%d %B %Y").lstrip("0")
    top = d["tightest"][:10]
    widest = d["widest"][:5]
    tight_councils = d["councils"][:10]
    loose_councils = list(reversed(d["councils"]))[:5]
    story = f"{SITE}/schools/tightest-catchments"

    lines: list[str] = []
    w = lines.append
    w(f"# Press kit: England's tightest school catchments")
    w("")
    w(f"Generated {today} from the live data. Every figure below is on {story}, and each school and council name links to the page that shows where it came from. Re-run `scripts/press_kit.py` after an admissions import.")
    w("")
    w("## What you can say, and what you cannot")
    w("")
    w("- Say: \"the last child offered a place lived X miles away\", \"admitted from X miles\", \"the tightest gate in [council]\".")
    w("- Do not say \"catchment area\" as if it were a boundary: most English schools have none. The distance is the council's published figure for the last child admitted under the distance criterion, and it moves every year.")
    w("- Only oversubscribed schools have a figure. A school missing from the list took everyone who applied.")
    w("- Councils measure differently (straight line for most, walking route for a few). Comparisons between councils are indicative; comparisons within a council are exact.")
    w("- Name the source every time: \"published by [council] after the [year] offer day, collated by UKPropertyInsight\".")
    w("")
    w("## Press release (national)")
    w("")
    w(f"**Headline:** {d['under_half']} English schools filled every place from within half a mile of the gate, new analysis of council admissions data shows")
    w("")
    w(f"**Standfirst:** Parents' talk of \"living on the doorstep\" to get a school place is borne out by the councils' own figures. Of {d['total']:,} oversubscribed schools across {d['council_count']} councils that publish how far the last child offered a place lived, {d['under_quarter']} admitted nobody from beyond a quarter of a mile and {d['under_half']} nobody from beyond half a mile. The middle school's figure was {d['median_miles']} miles.")
    w("")
    w("**The tightest ten:**")
    w("")
    for i, s in enumerate(top, 1):
        w(f"{i}. {school_link(s)}, {s['authority']}: {s['miles']} miles ({s['academic_year']})")
    w("")
    w("**The councils where places went to the nearest doors** (middle school's distance):")
    w("")
    for c in tight_councils:
        w(f"- {council_link(c)}: {c['median_miles']} miles, {c['share_under_a_mile']}% of its oversubscribed schools filled from under a mile")
    w("")
    w("**And where they went furthest:**")
    w("")
    for c in loose_councils:
        w(f"- {council_link(c)}: {c['median_miles']} miles")
    w("")
    w("**The widest gates in England:**")
    w("")
    for s in widest:
        w(f"- {school_link(s)}, {s['authority']}: {s['miles']} miles ({s['academic_year']})")
    w("")
    w("**Method:** After each admissions round, councils publish the distance from home to school of the last child offered a place at every oversubscribed school. UKPropertyInsight collected those publications (PDFs, spreadsheets and Freedom of Information releases) from every council that issues them in a usable form, matched each school to the Department for Education register, and ranked the figures. No distance is estimated or modelled; councils that publish nothing are absent. Full method and every figure: " + story)
    w("")
    w("**About:** UKPropertyInsight is a free due-diligence report for UK home buyers: sold prices, flood risk, crime, schools and school admissions for any postcode, from official sources only. Contact: support@ukpropertyinsight.co.uk")
    w("")
    w("## Pitch 1: local paper or news site")
    w("")
    w("Subject: The [council] school where the last child admitted lived [X] miles away")
    w("")
    w("Hi [name],")
    w("")
    w("I run UKPropertyInsight, a free property due-diligence site. We have just collated something [council] publishes but nobody reads: how far from the gate the last child admitted to each oversubscribed school lived. For [council] the tightest is [school] at [X] miles; the middle school's figure is [Y] miles, and [Z]% of its oversubscribed schools filled from under a mile. The whole table, school by school, is here: [council hub link].")
    w("")
    w("Happy to give you the spreadsheet, a quote, or the figures for any school you want to check. Every number comes from the council's own publication after offer day, and we link to it.")
    w("")
    w("Michael, UKPropertyInsight, support@ukpropertyinsight.co.uk")
    w("")
    w("*(Use the council paragraph below to fill the brackets.)*")
    w("")
    w("## Pitch 2: education press (Schools Week, Tes, the Guardian education desk)")
    w("")
    w(f"Subject: {d['under_half']} English schools admitted nobody from beyond half a mile")
    w("")
    w("Hi [name],")
    w("")
    w(f"We have collated the last-distance-offered figures that {d['council_count']} councils publish after offer day, {d['total']:,} oversubscribed schools in all, into one ranked table. {d['under_quarter']} schools filled from under a quarter of a mile; the middle school's figure nationally is {d['median_miles']} miles; the councils vary from {tight_councils[0]['median_miles']} miles ({tight_councils[0]['name']}) to {loose_councils[0]['median_miles']} miles ({loose_councils[0]['name']}). It is the closest thing to a national map of where the doorstep matters, and as far as we know nobody has assembled it before.")
    w("")
    w(f"The page: {story}. I can send the underlying spreadsheet and the per-council method notes. No modelling: every figure is a council's own, linked.")
    w("")
    w("Michael, UKPropertyInsight, support@ukpropertyinsight.co.uk")
    w("")
    w("## Pitch 3: Mumsnet or a local parents' Facebook group (post, not a pitch)")
    w("")
    w("Title: How far from the gate did the last child admitted live? Your council's figures, school by school")
    w("")
    w("Every year the council publishes how far away the last child offered a place at each oversubscribed school lived, and every year it is buried in a PDF. I put them in one place, per council, tightest first, with a postcode checker on each school page so you can see whether an address is inside last year's distance: [council hub link]. It is free and there is no sign-up. Two caveats: the figure moves every year, and a school that is not listed took everyone who applied. Happy to look up any school that is missing.")
    w("")
    w("## One paragraph per council")
    w("")
    w("Copy the paragraph for the council you are pitching. Each links to the hub page that carries the whole table.")
    w("")
    for c in sorted(d["councils"], key=lambda c: c["name"]):
        t = c["tightest"]
        wd = c["widest"]
        w(f"**{c['name']}** ({c['count']} schools with a figure): the tightest gate is {school_link(t)} at {t['miles']} miles ({t['academic_year']}); the middle school's distance is {c['median_miles']} miles; {c['under_a_mile']} of {c['count']} oversubscribed schools ({c['share_under_a_mile']}%) filled from under a mile; places went furthest at {school_link(wd)}, {wd['miles']} miles. Full table: {SITE}/schools/admissions/{c['slug']}")
        w("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(d['councils'])} councils, {d['total']:,} schools)")


if __name__ == "__main__":
    main()
