"""What to look at with your own eyes, at the viewing.

Different from the solicitor questions the report already builds: those
are things to ask later, in writing, through someone else. This is the
half hour you get inside the building, and it is the only part of the
process where the buyer is the instrument.

Every item is triggered by something this property's own report found,
so the list is short and specific rather than the generic twenty points
every estate agent blog carries. A property with nothing flagged gets
told that plainly instead of being given filler.

Locking is handled by reusing overview_score._find_concerns, which
already knows which findings a free reader is allowed to see, so a
checklist can never leak a Premium finding.

18 Sep 2026 (first-visitor audit item D4): the checklist also prints the
questions to ask, and they are the report's own list for the same reader
(solicitor_questions.for_reader), not a second set. Until then the two
were built apart, and a finding could raise a question on one and not
the other. What to look at in the room stays this module's own.

18 Sep 2026 (first-visitor audit F2): every item can be ticked, answered
and marked to follow up, on the device and, signed in, on the account
(app/checklist_ticks.py). Each item carries a key that names it across
visits, so a tick finds its item again next time: "look-" and the
concern for what to look at, "every-" and the heading for the five
asked at every viewing, and "ask-" and a hash of the question's own
words for the report's questions, which have no other name. A key is
only ever rendered beside its item, so a locked check's item, which is
never on the page for a reader who has not opened the home, never has
its key there either.
"""
import hashlib
import re

from app.services import overview_score, solicitor_questions

# One entry per concern the score can raise. The wording is deliberately
# about looking, not about judging: a buyer at a viewing can see whether
# there is a tide mark on a wall, not whether a flood claim was made.
LOOK_FOR = {
    "flood": (
        "Signs of past water",
        "Tide marks or fresh paint low on walls, replaced skirting, a musty smell in "
        "ground-floor rooms or cupboards, and where the airbricks sit relative to the path.",
    ),
    "surface_water": (
        "Where rainwater goes",
        "Gullies and drain covers around the building, whether the garden or drive slopes "
        "towards the house, and whether the path outside is higher than the floor inside.",
    ),
    "sewage": (
        "The watercourse nearby",
        "Walk to the nearest stream or river and look at it. Discharge records tell you it "
        "happens; standing there tells you how close it is and whether you would notice.",
    ),
    "noise": (
        "Listen with the windows open",
        "Stand at an open window in every room that faces the road or railway. Ask when the last train "
        "runs. Traffic at a Sunday viewing is not traffic on a Tuesday morning.",
    ),
    "air_quality": (
        "The road at the front",
        "How close the nearest busy road is, whether windows on that side open, and whether "
        "there is any ventilation other than opening them.",
    ),
    "radon": (
        "Ask what has been tested",
        "Whether a radon test has ever been done and whether any sump or membrane was fitted. "
        "A test kit is inexpensive and takes three months, so ask early.",
    ),
    "clay_risk": (
        "Cracks, and which way they run",
        "Diagonal cracks around window and door corners, doors that no longer shut square, and "
        "any large trees close to the walls. Photograph anything you find.",
    ),
    "landfill": (
        "The ground and the garden",
        "Made-up ground, uneven settlement in the garden or drive, and any venting pipes. Ask "
        "what was on the site before the houses.",
    ),
    "coal_mining": (
        "Movement in the structure",
        "Sloping floors, stepped cracking in brickwork, and previous underpinning. Ask whether "
        "a mining report has been done and whether there has been any claim.",
    ),
    "extension": (
        "Where the old house stops",
        "Where the original building ends and the newer part begins: changes in brick, floor "
        "level or ceiling height. Ask for the building regulations completion certificate.",
    ),
    "prosperity": (
        "How the street is holding up",
        "Empty properties, boarded windows, how many are for sale on the same road, and how "
        "long the boards have been up.",
    ),
    "deprivation": (
        "Walk it at a different hour",
        "Come back on a weekday evening. An area reads very differently at 11am on a Saturday "
        "than it does when everyone is home.",
    ),
    "broadband": (
        "Test it while you are there",
        "Run a speed test on the wifi and check your phone has a signal in the rooms you would "
        "work in. Ask which provider they actually use.",
    ),
    "mobile": (
        "Signal, room by room",
        "Check bars in the kitchen, the back bedroom and the garden. Coverage maps are modelled "
        "at street level and thick walls are not in the model.",
    ),
    "planning": (
        "What the constraint means in practice",
        "If it is a conservation area or a listed setting, ask what the neighbours have and have "
        "not been allowed to do. Windows and roofs are where it usually bites.",
    ),
    "environmental": (
        "The land around the boundary",
        "What the protected land next door is, who maintains it, and whether it brings walkers, "
        "livestock or standing water up against the boundary.",
    ),
}

# Asked at every viewing regardless of what was flagged, because they
# are the questions a report cannot answer and a buyer routinely
# forgets. Kept short on purpose.
ALWAYS = [
    ("Water pressure", "Run the shower and a tap at the same time, upstairs."),
    ("The boiler", "Its age, when it was last serviced, and whether the paperwork exists."),
    ("What is included", "Which appliances, carpets and curtains are staying. Get it in writing."),
    ("Parking", "Whether the space is allocated, shared or first-come, and what visitors do."),
    ("Which way it faces", "Where the sun is now, and where it will be at the time of day you are home."),
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30]


def question_key(question: str) -> str:
    """A question's key: its own words, hashed. The same question on the
    same home gets the same key on every visit and on every device."""
    return "ask-" + hashlib.sha1(question.encode("utf-8")).hexdigest()[:10]


def build(context: dict, premium_unlocked: bool = False, questions: dict | None = None) -> dict:
    """{"flagged": [...], "always": [...], "questions": {...},
    "question_keys": {...}, "keys": [...]}, with flagged driven by what
    this property's report actually found. "questions" is
    solicitor_questions.for_reader for this reader, the list the report
    shows; the route passes the one it built for the report's own
    context, and without it the same function builds it. It is left as
    it came, so the two stay equal; each question's key is looked up in
    "question_keys" by its words. "keys" is every item on the page, in
    page order, which the ticks are counted against (18 Sep 2026, F2)."""
    if questions is None:
        questions = solicitor_questions.for_reader(context, premium_unlocked)
    concerns = overview_score._find_concerns(context, premium_unlocked=premium_unlocked)
    flagged = []
    for key in concerns:
        item = LOOK_FOR.get(key)
        if item:
            flagged.append({
                # The report banner's own words for the finding (18 Sep
                # 2026, D3 review): a zone 2 home built after 2009 read
                # "Flood risk" here and "Flood Re insurance not available
                # for this home" on the report.
                "finding": overview_score._concern_text(key, context),
                "heading": item[0],
                "detail": item[1],
                "key": "look-" + _slug(key),
            })
    always = [{"heading": h, "detail": d, "key": "every-" + _slug(h)} for h, d in ALWAYS]
    shown = [q for _, qs in (questions.get("found") or []) for q in qs] + list(questions.get("every_purchase") or [])
    question_keys: dict[str, str] = {}
    for q in shown:
        question_keys.setdefault(q["question"], question_key(q["question"]))
    keys = ([i["key"] for i in flagged] + [question_keys[q["question"]] for q in shown]
            + [i["key"] for i in always])
    return {"flagged": flagged, "always": always, "questions": questions,
            "question_keys": question_keys, "keys": list(dict.fromkeys(keys))}
