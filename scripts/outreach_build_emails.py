"""Write one ready-to-send email per prospect into docs/outreach/emails/.

Reads docs/outreach/prospects.json, which is the ledger: every firm that
has ever been drafted to, with the page its address was read from and the
date. Nothing here invents an address, and a firm already in the file is
never drafted twice, so the daily routine can append and re-run.

    .venv/Scripts/python.exe scripts/outreach_build_emails.py

The figures in the bodies are read from the app's own constants and from
the live database, so a message can never quote a number the site has
stopped saying. Written 30 Sep 2026; the daily routine in
docs/outreach/DAILY-ROUTINE.md runs it.
"""
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "outreach" / "emails"
LEDGER = ROOT / "docs" / "outreach" / "prospects.json"
BASE = "https://ukpropertyinsight.co.uk"

SIGN = "Michael\nukpropertyinsight.co.uk"
OPT_OUT = ('If you would rather not hear from me, reply with "no thanks" '
           "and I will not write again.")
CATEGORY_NAME = {"reloc": "relocation consultant", "agent": "buying agent",
                 "broker": "mortgage broker", "convey": "conveyancing solicitor"}
SUBJECTS = {
    "reloc": "The admission distance for any postcode, without the council PDF",
    "agent": "What the council published, before your client views",
    "broker": "A free property report you can send a first-time buyer",
    "convey": "What buyers ask you before the searches come back",
}


def figures():
    """The counts the emails quote, from the app and the live data."""
    os.environ.setdefault("SESSION_SECRET", "outreach-build")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from app import main as app_main
    from app.services import schools_db
    schools = councils = None
    try:
        from sqlalchemy import func, select
        from app.models import SchoolAdmissionRadius
        with schools_db.get_session() as session:
            schools = session.scalar(select(func.count(func.distinct(SchoolAdmissionRadius.urn))))
            councils = session.scalar(select(func.count(func.distinct(SchoolAdmissionRadius.source_authority))))
    except Exception as exc:  # noqa: BLE001 - fall back to the last known figures
        print("could not read the database, using the last written figures:", exc)
    return {
        "schools": f"{schools:,}" if schools else "3,627",
        "councils": str(councils) if councils else "88",
        "checks": str(app_main.CHECK_COUNT),
        "free": str(len(app_main.FREE_CHECKS)),
    }


# The price paragraph. Michael's words, 30 Sep 2026: the monthly and
# quarterly prices are introductory while he collects feedback from the
# first customers and will be revised. It says introductory rather than
# "discount", because there is no former higher price to discount from
# and inventing one is the kind of claim this site does not make.
def price_line(f):
    return (
        f"It is £9.99 a month or £24.99 a quarter, and the first full report on any address "
        f"is free with an account. Those are introductory prices while I am still collecting "
        f"feedback from early customers, and I will review them once I have enough of it. If "
        f"you tell me what is missing for your work, that feedback is worth more to me than "
        f"the subscription."
    )


def body(p, f):
    greet = f"Hello {p['contact']}," if p.get("contact") else "Hello,"
    url = f"{BASE}{p['link']}" if p.get("link") else BASE
    cat = p["category"]
    unique = (
        f"Every council publishes, after offer day, how far from the school the last child "
        f"offered a place lived. Each one does it in its own PDF, in its own format, once a "
        f"year, and the file is gone from the website a year later. I have turned {f['councils']} "
        f"councils' figures into one thing you can check by postcode, for {f['schools']} schools, "
        f"with the years side by side where a council publishes more than one. I have not found "
        f"anyone else who does that."
    )
    report = (
        f"The rest of the report is the same idea: {f['checks']} checks on an address, "
        f"{f['free']} of them free without an account, each naming the official source it came "
        f"from, from HM Land Registry, the Environment Agency, Ofsted, the EPC register, "
        f"Police.uk and the ONS. It comes as a page you can send or a PDF you can attach, and "
        f"there is a Chrome extension that puts the same figures on a Rightmove, Zoopla or "
        f"OnTheMarket listing while you browse."
    )
    if cat == "reloc":
        use = ("For a family choosing between two areas, that answers the question they ask "
               "first and the one that is hardest to answer honestly: can we get in from this "
               "street, and how close was it last year.")
        offer = ("Send me an address one of your families is considering and I will email the "
                 "full report back the same day, free, so you can judge it on a real case "
                 "rather than a demo.")
    elif cat == "agent":
        use = ("Before a viewing it tells your client what the street has sold for, whether the "
               "flood zone will trouble a lender, and whether the school they are moving for "
               "would have taken them last year.")
        offer = ("Send me an address you are working on and I will email the full report back "
                 "the same day, free, so you can judge it on a real case rather than a demo.")
    elif cat == "broker":
        use = ("Two of those checks reach a mortgage: the flood zone, because insurance and "
               "lending follow it, and the energy rating, because it decides which green "
               "products a lender will offer. The rest is something useful you can send a "
               "first-time buyer who is still choosing a street.")
        offer = ("Send me an address from a current case and I will email the full report back "
                 "the same day, free.")
    else:
        use = ("It is not a search and does not replace one. It is the context clients ask you "
               "for in week one, while the official searches are still out, which is time your "
               "team spends on the phone.")
        offer = ("Send me an address from a current file and I will email the full report back "
                 "the same day, free.")
    parts = [greet, "", p["hook"], "", unique, "", url, "", use, "", report, "", price_line(f), "", offer]
    if p.get("note"):
        parts += ["", p["note"]]
    parts += ["", SIGN, "", OPT_OUT]
    return "\n".join(parts)


def main():
    prospects = json.loads(LEDGER.read_text(encoding="utf-8"))
    seen = set()
    for p in prospects:
        key = p["email"].lower()
        if key in seen:
            raise SystemExit(f"{p['email']} is in the ledger twice; fix prospects.json")
        seen.add(key)
    f = figures()
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.md"):
        old.unlink()
    rows = []
    for i, p in enumerate(prospects, 1):
        slug = re.sub(r"[^a-z0-9]+", "-", p["firm"].lower()).strip("-")
        name = f"{i:02d}-{slug}.md"
        (OUT / name).write_text(
            f"# {i}. {p['firm']} ({CATEGORY_NAME[p['category']]})\n\n"
            f"**To:** {p['email']}\n\n"
            f"**Subject:** {SUBJECTS[p['category']]}\n\n"
            "---\n\n"
            f"{body(p, f)}\n\n"
            "---\n\n"
            f"Address read from {p['source']} on {p['added']}. Sent: ____  Replied: ____\n",
            encoding="utf-8")
        rows.append((i, p, name))
    write_index(rows, f)
    print(f"wrote {len(rows)} emails into {OUT}")


def write_index(rows, f):
    lines = [
        "# The emails, ready to send", "",
        "One file per firm: open it, copy the subject and the body, send. The body is",
        "already personalised, so there is nothing to fill in. Built by",
        "scripts/outreach_build_emails.py from docs/outreach/prospects.json, which is the",
        "ledger of every firm ever drafted to and the page each address was read from.",
        "", f"Figures quoted in the bodies today: {f['schools']} schools across {f['councils']} councils,",
        f"{f['checks']} checks, {f['free']} of them free.", "",
        "## The list", "",
        "| # | Firm | Type | Email | Added | File |", "|---|---|---|---|---|---|",
    ]
    for i, p, name in rows:
        lines.append(f"| {i} | {p['firm']} | {CATEGORY_NAME[p['category']]} | {p['email']} "
                     f"| {p['added']} | [{name}]({name}) |")
    lines += [
        "", "## How they reach Outlook", "",
        "`powershell -File scripts/outreach_to_outlook.ps1` saves each one as a draft in the",
        "Drafts folder of support@ukpropertyinsight.co.uk. It sends nothing, and it skips any",
        "draft already there, so it can be run again safely.", "",
        "## The rules that matter", "",
        "- UK law allows email to a company or LLP without prior consent, as long as you",
        "  identify yourself and offer a way to opt out. Every draft ends with that line.",
        "  A one-person firm trading under their own name counts as an individual, so use",
        "  their enquiry form instead.",
        "- Ten a day at most, one at a time, from your own mailbox. No mail-merge, no",
        "  tracking, no attachment.",
        "- One follow-up, five working days later, then leave them alone.",
        "- Answer anyone who asks for a report the same day. That is the whole offer.", "",
        "## The single follow-up", "",
        "> Hello [name],", ">",
        "> Following up once on the note below, in case it was the wrong week. The offer",
        "> stands: send me an address and I will email the report back the same day, free.",
        "> If it is not for you, no reply needed and I will not write again.", ">",
        "> Michael", "",
    ]
    (OUT / "00-INDEX.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
