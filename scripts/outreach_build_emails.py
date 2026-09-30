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
import hashlib
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
# A rotation, not a template. Two days of identical subject lines to
# neighbouring firms in the same trade is what bulk mail looks like, to a
# reader and to a spam filter, so each firm draws its own from its
# category's set (see pick()).
SUBJECTS = {
    "reloc": [
        "The admission distance for any postcode, without the council PDF",
        "How close did that school admit from last year",
        "School catchment figures for a family you are moving",
        "Council admission distances, checkable by postcode",
    ],
    "agent": [
        "What the council published, before your client views",
        "The school figure your buyer will ask you about",
        "Sold prices, flood zone and the school distance, in one page",
        "Due diligence on an address before the offer",
    ],
    "broker": [
        "A free property report you can send a first-time buyer",
        "The flood zone before the valuation comes back",
        "Something useful to send a client who is still choosing a street",
        "Official data on an address, free, for your clients",
    ],
    "convey": [
        "What buyers ask you before the searches come back",
        "Context for a client in week one, before the searches land",
        "The questions clients ring you about while searches are out",
        "Published data on an address, in seconds, not a search",
    ],
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
# and inventing one is the kind of claim this site does not make. Three
# ways of saying the same true thing, so a week of emails does not read
# as one paragraph pasted over and over.
PRICES = [
    "It is £9.99 a month or £24.99 a quarter, and the first full report on any address is "
    "free with an account. Those are introductory prices while I am still collecting feedback "
    "from early customers, and I will review them once I have enough of it. If you tell me "
    "what is missing for your work, that feedback is worth more to me than the subscription.",
    "The first full report on any address is free with an account, and after that it is £9.99 "
    "a month or £24.99 a quarter. Both are introductory: I am pricing low while the first "
    "customers tell me what this is worth to them, and I will revise once they have. Honest "
    "criticism from someone who does this for a living counts for more than the fee.",
    "Pricing, plainly: the first full report is free with an account, then £9.99 a month or "
    "£24.99 a quarter. Those are introductory figures set while I gather feedback from early "
    "customers, and they will be reviewed. I would rather hear what a professional finds "
    "missing than take the subscription and guess.",
]


def pick(options, p, salt=""):
    """Which variant this firm gets: settled by the firm's own address and
    the day it was added, so a batch never repeats yesterday's wording and
    the same firm always regenerates to the same email."""
    key = (p["email"] + "|" + str(p.get("added", "")) + "|" + salt).lower().encode()
    return options[int(hashlib.sha256(key).hexdigest(), 16) % len(options)]


def angles(f):
    """Four openings, each one true and each leading with a different part
    of the same product. The rotation is what keeps a week of emails from
    reading as one letter with the names changed."""
    return [
        f"Every council publishes, after offer day, how far from the school the last child "
        f"offered a place lived. Each does it in its own PDF, in its own format, once a year, "
        f"and takes the file down when the next one appears. I have turned {f['councils']} "
        f"councils' figures into one thing you can check by postcode, for {f['schools']} "
        f"schools, with the years side by side where a council publishes more than one. I have "
        f"not found anyone else who does that.",

        f"The question that is hardest to answer honestly about a house is whether the school "
        f"would have taken it. Councils do publish the answer, as the distance the last child "
        f"admitted lived from the gate, but it sits in a PDF that changes shape every year and "
        f"disappears when the next one lands. I hold {f['schools']} schools across "
        f"{f['councils']} councils, and a postcode check against each published year.",

        f"A school's admission distance moves more than people expect. One Haringey primary "
        f"admitted from 0.51 miles in 2022 and 1.59 in 2026, three times the range, so a family "
        f"judging by last year's figure alone rules out streets that would have worked. Where a "
        f"council publishes several years, I show them side by side, for {f['schools']} schools "
        f"across {f['councils']} councils.",

        f"Most property tools stop at sold prices and a crime count. The one figure that decides "
        f"where a family with children will actually buy is how far the school admitted from, "
        f"and that lives in {f['councils']} separate council PDFs. I have put all of them behind "
        f"one postcode box, {f['schools']} schools, with each published year kept rather than "
        f"overwritten.",
    ]


def body(p, f):
    greet = f"Hello {p['contact']}," if p.get("contact") else "Hello,"
    url = f"{BASE}{p['link']}" if p.get("link") else BASE
    cat = p["category"]
    unique = pick(angles(f), p, "angle")
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
    parts = [greet, "", p["hook"], "", unique, "", url, "", use, "", report, "", pick(PRICES, p, "price"), "", offer]
    if p.get("note"):
        parts += ["", p["note"]]
    parts += ["", SIGN, "", OPT_OUT]
    return "\n".join(parts)


def main():
    prospects = json.loads(LEDGER.read_text(encoding="utf-8"))
    check_one_firm_one_email(prospects)
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
            f"**Subject:** {pick(SUBJECTS[p['category']], p, 'subject')}\n\n"
            "---\n\n"
            f"{body(p, f)}\n\n"
            "---\n\n"
            f"Address read from {p['source']} on {p['added']}. Sent: ____  Replied: ____\n",
            encoding="utf-8")
        rows.append((i, p, name))
    write_index(rows, f)
    print(f"wrote {len(rows)} emails into {OUT}")


def check_one_firm_one_email(prospects):
    """A firm is written to once. Twice is what an inbox calls spam, and on
    30 Sep 2026 seven firms received the same email two or three times
    because the check lived in a mailbox folder that read as empty
    mid-sync. It lives here now, and it stops the build rather than
    warning: a second copy cannot be unsent."""
    seen_email, seen_domain = {}, {}
    for p in prospects:
        email = (p.get("email") or "").lower()
        if not email:
            continue  # form-only firms are recorded so they are not researched again
        if email in seen_email:
            raise SystemExit(f"{email} appears twice in the ledger ({seen_email[email]} and "
                             f"{p['firm']}); one firm, one email")
        seen_email[email] = p["firm"]
        domain = email.rsplit("@", 1)[-1]
        if domain in seen_domain and not p.get("allow_same_domain"):
            raise SystemExit(
                f"{p['firm']} is at {domain}, which {seen_domain[domain]} already uses. Write to "
                f"one person at a firm, or set \"allow_same_domain\": true on this entry if two "
                f"people there genuinely work separate patches.")
        seen_domain.setdefault(domain, p["firm"])


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
