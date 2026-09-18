"""Questions to ask before you buy, generated from this property's own
report findings.

Pure rules over the context the report page already computed - no new
data source and no model involved, so every question can name the
finding that triggered it. Thresholds deliberately mirror the ones the
report cards use for their own attention states (see property.html),
so a question never appears without the card that explains it also
flagging up.

Each question dict: {"audience", "trigger", "question", "why", "cost",
"check"}. "cost" is a typical third-party search/report fee as a plain
string, only where one is well established, otherwise empty. "check" is
the report check whose finding triggered the question, set only where
that check is one of the locked ones, and empty otherwise.

18 Sep 2026 (first-visitor audit item D4): KT3 4HX said "4 questions
were generated for this property", three of them the fixed ones every
purchase gets, while findings the page itself shows raised none: YO1
7HH's council under exceptional support and listed buildings 9 yards
away, Likely school readings, an EPC D with £8,400 to £12,200 to reach
C. Five triggers were added from data the report already holds: a listed
building within LISTED_NEAR_M, the nearest Likely or Borderline school
reading, an EPC of D to G with the certificate's own cost to reach C, a
check whose service did not answer, and the council's finance flag.
for_reader() below is the one list both the report's "Before you offer"
section and the viewing checklist show, so the two cannot disagree.
"""
from app.services import council_finance, council_tax, overview_score

# "Check for yourself" added 18 Sep 2026 (D4) for the school question:
# the council's allocation figures are published, so nobody needs asking.
AUDIENCES = ("Ask the seller", "For your solicitor", "For your surveyor", "Check for yourself")

# The trigger of the three questions asked on every purchase. The report
# folds them under their own heading and counts them apart from the ones
# this home's findings raised (18 Sep 2026, D4).
EVERY_PURCHASE = "Every purchase"

# A listed building this close may be this home, or stand within its
# curtilage. The heritage check measures from the postcode's centre to a
# point on Historic England's list and cannot say which (see heritage.py),
# so the question goes to someone who can (18 Sep 2026, D4).
LISTED_NEAR_M = 25

# The EPC bands below C, the band the certificate's own plan is costed to.
_BELOW_C = ("D", "E", "F", "G")

# The checks that only open with a full report, by the same keys
# overview_score._PREMIUM_ONLY_CONCERNS uses. A question tagged with one
# of these states the locked check's finding in its own trigger, so it
# cannot be shown to a reader the report is locked for.
#
# 17 Sep 2026: added for the first-visitor audit. The locked cards and
# pop-ups stopped rendering their findings, but the "Questions to ask"
# teaser still named five of them in plain body text, one of them a
# figure. The teaser keeps its job from the questions that are left.
LOCKED_CHECKS = frozenset({"extension", "coal_mining", "landfill", "sewage", "clay_risk"})


def without_locked(questions: list[dict]) -> list[dict]:
    """The questions whose triggers give nothing away to a reader who
    has not unlocked the report."""
    return [x for x in questions if x.get("check") not in LOCKED_CHECKS]


def _noise_max(noise: dict | None) -> int | None:
    if not noise:
        return None
    return max(noise.get("road_db") or 0, noise.get("rail_db") or 0, noise.get("airport_db") or 0)


def _gbp(value) -> str:
    """Pounds as the report's gbp filter writes them (main._format_gbp):
    to the nearest pound, halves up, since 18 Sep 2026. It cut the pence
    off, so a cost of £1,240.60 read £1,240 here and £1,241 on the
    energy card."""
    return f"£{council_tax.whole_pounds(value):,}"


def _yards(metres) -> str:
    """Whole yards, rounded the way the report's listed buildings table
    rounds them (main._format_distance), so the question and the table
    give the same figure."""
    yards = int(round(float(metres) / 0.9144))
    return f"{yards} yard{'s' if yards != 1 else ''}"


def build(context: dict) -> list[dict]:
    q: list[dict] = []

    def add(audience, trigger, question, why, cost="", check=""):
        q.append({"audience": audience, "trigger": trigger, "question": question, "why": why,
                  "cost": cost, "check": check})

    flood_zone = context.get("flood_zone")
    if (flood_zone and flood_zone.get("zone", 1) >= 2) or context.get("flood_warnings"):
        zone_label = flood_zone.get("label", "a flood risk area") if flood_zone else "a flood risk area"
        add("Ask the seller", f"Flood: {zone_label}",
            "Has the property ever flooded, and has any flood insurance claim been made?",
            "Sellers must answer honestly on the TA6 property information form. A past claim can make insurance expensive or carry a high excess, and that follows the property.")
        add("For your solicitor", f"Flood: {zone_label}",
            "Order a flood risk report and check the insurer will offer cover under Flood Re.",
            "A standard search does not always include detailed flood data. Flood Re caps premiums for homes built before 2009 only.",
            "around £25")

    surface_water = context.get("surface_water")
    if surface_water and surface_water.get("label") == "High risk":
        add("For your surveyor", "High surface water flood risk",
            "Check where rainwater runs and drains around the property, and the condition of gullies and drains.",
            "Surface water flooding comes from heavy rain overwhelming drainage, not rivers, so it does not show up in river flood zones.")

    radon = context.get("radon")
    if radon and int(radon.get("class", 0) or 0) >= 4:
        add("Ask the seller", "Elevated radon risk area",
            "Has the property been tested for radon, and were any protective measures installed?",
            "A three-month test kit is cheap, and sumps or extra ventilation fix most problems. What matters is knowing.",
            "test kit around £50")

    coal = context.get("coal_mining")
    if coal and coal.get("present"):
        add("For your solicitor", "Coal Mining Reporting Area",
            "Order a CON29M coal mining search.",
            "It reports past and planned mining, shafts, and subsidence claims. Lenders normally insist on it in these areas.",
            "around £40", check="coal_mining")

    landfill = context.get("historic_landfill")
    if landfill and landfill.get("status") != "clear":
        add("For your solicitor", "Historic landfill on or near the site",
            "Order an environmental search and ask whether contaminated land liability could pass to the buyer.",
            "Under Part 2A the current owner can inherit clean-up liability if the original polluter cannot be found.",
            "around £50-£110", check="landfill")

    outfalls = context.get("sewage_outfalls")
    if outfalls and (outfalls[0].get("spill_count") or 0) >= 20:
        add("For your solicitor", "Frequent sewage discharges nearby",
            "Order the CON29DW drainage and water search, and check where the property's foul water drains.",
            "It confirms mains connection, shared drains and who maintains what, and it names the sewerage undertaker responsible for problems.",
            "around £40-£60", check="sewage")

    noise_max = _noise_max(context.get("noise"))
    if noise_max is not None and noise_max >= 65:
        add("Ask the seller", f"Modelled noise up to {noise_max} dB(A)",
            "Have you ever complained about noise, or has any neighbour complained about you?",
            "Noise complaints must be declared on the TA6 form. Visit at rush hour and late evening before deciding.")

    clay = context.get("clay_risk")
    if clay and clay.get("class_2030") == "Probable":
        add("For your surveyor", "Rising clay subsidence risk",
            "Look specifically for movement: cracks over doors and windows, sticking frames, and how close large trees stand to the walls.",
            "Shrink-swell clay moves with wet and dry years. Past underpinning or a subsidence claim also raises insurance sharply, so ask the insurer about street history.",
            check="clay_risk")

    ext = context.get("extension_signal")
    if ext and ext.get("likely_extended"):
        add("Ask the seller", f"Floor area grew about {ext.get('change_pct', 0):+.0f}% between energy certificates",
            "Which works were done, and can you provide the planning permission and building regulations completion certificates?",
            "Works without sign-off become the buyer's problem. Indemnity insurance covers enforcement, not safety.",
            check="extension")

    for flag in context.get("planning_flags") or []:
        label = flag.get("label", "")
        if label == "Conservation Area":
            add("For your solicitor", "In a Conservation Area",
                "Check whether any Article 4 direction removes permitted development rights, and that past external works had consent.",
                "In conservation areas even small changes like windows or render can need permission, and enforcement passes to the new owner.")
        if label == "Green Belt":
            add("For your solicitor", "Green Belt",
                "Confirm how Green Belt policy limits extensions or outbuildings here.",
                "Extending in the Green Belt is possible but tightly capped. If you plan to extend, know the ceiling before you offer.")

    if context.get("lead_plumbing_era"):
        add("For your surveyor", "Built before 1970",
            "Check the incoming water main and internal pipework for lead, and the consumer unit and wiring age.",
            "Lead supply pipes and pre-1970s wiring are the two most common surprise costs in older homes.")

    if context.get("mees_compliant") is False:
        add("Ask the seller", "EPC rated F or G",
            "What would it take to raise the energy rating to E or better?",
            "An F or G rating cannot legally be let, which shrinks the resale market to owner-occupiers and signals high running costs.")

    # From here to the failed checks, the triggers of 18 Sep 2026 (D4),
    # each from a free card, so every reader sees them in full.

    # The nearest listed building, when it is close enough to be this
    # home or to stand within its curtilage. The Listed Buildings card is
    # free and its table gives the same name, grade and distance.
    heritage = context.get("heritage") or []
    listed = next((b for b in heritage if b.get("distance_m") is not None and b["distance_m"] <= LISTED_NEAR_M), None)
    if listed:
        grade = f", Grade {listed['grade']}" if listed.get("grade") else ""
        add("For your solicitor", f"Listed building {_yards(listed['distance_m'])} away: {listed.get('name') or 'Unnamed'}{grade}",
            "Ask your solicitor whether the home is listed or within the curtilage of a listed building.",
            f"Historic England's list places it {_yards(listed['distance_m'])} from the centre of the postcode, the point this report "
            "measures from. A distance cannot say whether this home is part of the listing. Altering a listed building, or a "
            "structure within its curtilage, without listed building consent is an offence, and putting it right falls to the owner.")

    # The nearest school the report reads as Likely or Borderline. Named,
    # because one school and one reading is what every school's own page
    # gives anyone free (/school/<urn>?check=); the reading for every
    # school at once stays with the locked School Catchment Areas card.
    # school_verdicts is the free Schools Nearby card's own summary.
    verdicts = context.get("school_verdicts") or {}
    school = next((r for r in verdicts.get("schools") or [] if r.get("level") in ("likely", "borderline")), None)
    if school:
        estimated = school.get("kind") == "estimated"
        add("Check for yourself",
            f"School places: {school['label']} for {school['name']}{' (estimated distance)' if estimated else ''}",
            f"Check the distance {school['name']} last offered places to in the council's allocation figures before relying on it.",
            "Likely and Borderline compare this address with the furthest distance the school offered places to last time"
            + (", here a modelled estimate because no council figure is held for it" if estimated else "")
            + ". It is never a guarantee: that distance moves every year with demand, and places go first to children "
            "who meet the school's other criteria, such as siblings.")

    # An EPC below C with the certificate's own cost of reaching it, the
    # figure the Energy Efficiency card shows. Without a house number the
    # certificate is the postcode's newest, which may be a neighbour's.
    detail = context.get("property_detail") or {}
    band = (detail.get("current_band") or "").upper()
    to_c = (detail.get("improvements") or {}).get("to_c") or {}
    if band in _BELOW_C and to_c.get("cost_low") is not None and to_c.get("cost_high") is not None:
        cost = f"{_gbp(to_c['cost_low'])} to {_gbp(to_c['cost_high'])}"
        own = bool(context.get("house_number"))
        add("Ask the seller",
            f"EPC Band {band}{'' if own else ' on the newest certificate at this postcode'}, {cost} to reach C",
            "Ask the seller which of the certificate's recommended improvements have been done.",
            "The cost is the certificate's own, from the assessor's recommended measures. Work done since the assessment does "
            "not show until a new certificate is made, and it changes both the running costs and what is left to spend."
            + ("" if own else " On a postcode search, first check the certificate is for the home you are buying."))

    # The council's finances, when the council tax card flags them.
    finance_flag = council_finance.flag_sentence(overview_score._council_finance(context))
    if finance_flag:
        add("Ask the seller", finance_flag,
            "Ask the seller what the council tax bill was this year and last.",
            "Councils in this position tend to cut services and set the largest council tax rises the rules allow. "
            "Two years of bills show what that has meant for this home so far.")

    # A check whose own service did not answer. The report says it could
    # not be checked; this says what a buyer does instead, which is the
    # search a solicitor orders for the same question. A failure is not a
    # finding, so these go to every reader, the locked checks' included: a
    # locked card already says "Could not be checked just now" to a
    # signed-out visitor (LOCKED_CARD_UNAVAILABLE in main.py), and the
    # question says no more than that card does.
    if context.get("coal_mining_error"):
        add("For your solicitor", "Coal mining could not be checked just now",
            "Ask your solicitor whether a CON29M coal mining search is needed, because the online check could not run.",
            "The Mining Remediation Authority's map of coal mining reporting areas did not answer, so this report cannot say "
            "whether the home is in one. Lenders normally insist on the search where it is.",
            "around £40")
    if context.get("flood_zone_error") and not context.get("flood_not_covered"):
        add("For your solicitor", "The flood zone could not be checked just now",
            "Ask your solicitor whether a flood risk report is needed, because the online flood zone check could not run.",
            "The Environment Agency's flood map did not answer, so this report cannot say which flood zone the home is in.",
            "around £25")
    if context.get("historic_landfill_error"):
        add("For your solicitor", "Former landfill could not be checked just now",
            "Ask your solicitor whether an environmental search is needed, because the online contamination check could not run.",
            "The Environment Agency's historic landfill register did not answer. Under Part 2A the current owner can inherit "
            "clean-up liability if the original polluter cannot be found.",
            "around £50-£110")
    if context.get("heritage_error"):
        add("For your solicitor", "Listed buildings could not be checked just now",
            "Ask your solicitor whether the home is listed or within the curtilage of a listed building, because the online check could not run.",
            "Historic England's list did not answer. Altering a listed building without listed building consent is an "
            "offence, and putting it right falls to the owner.")
    if context.get("designations_error"):
        add("For your solicitor", "Planning designations could not be checked just now",
            "Ask your solicitor to confirm from the local authority search whether the home is in a conservation area or the "
            "Green Belt, because the online check could not run.",
            "In a conservation area even windows or render can need permission, and in the Green Belt extensions are tightly capped.")

    # Every purchase, regardless of findings. Kept short on purpose.
    add("For your solicitor", EVERY_PURCHASE,
        "Confirm the tenure. If leasehold: years remaining, ground rent terms, service charge history and any planned major works.",
        "A lease under about 80 years or a doubling ground rent can cost tens of thousands to fix and some lenders refuse them.")
    add("For your solicitor", EVERY_PURCHASE,
        "Check the title plan boundaries match what you saw, and who owns and maintains each fence, wall, and any shared access.",
        "Boundary surprises are the most common post-completion dispute, and they are nearly free to catch before exchange.")
    add("Ask the seller", EVERY_PURCHASE,
        "Why are you selling, how long has it been on the market, and what exactly is included in the sale?",
        "The answers shape your negotiating position more than any survey. Fixtures and fittings go on the TA10 form, hold them to it.")

    return q


def grouped(questions: list[dict]) -> list[tuple[str, list[dict]]]:
    """Stable audience order, empty audiences dropped."""
    return [
        (audience, [x for x in questions if x["audience"] == audience])
        for audience in AUDIENCES
        if any(x["audience"] == audience for x in questions)
    ]


def for_reader(context: dict, premium_unlocked: bool) -> dict:
    """The questions one reader is shown: the report's "Before you offer"
    section and the viewing checklist both print this, so the two cannot
    disagree (18 Sep 2026, first-visitor audit item D4).

    "found" is what this home's findings raised, grouped by audience, and
    "every_purchase" the three asked on every purchase. A reader the
    report is locked for gets every question a free card raised in full;
    one a locked check raised is left out, because its trigger states
    that check's finding, and only counted in "locked". "total" is every
    question generated and "from_findings" how many of them this home's
    findings raised, both counting the locked ones: how many questions
    there are is not a finding.
    """
    questions = build(context)
    shown = questions if premium_unlocked else without_locked(questions)
    return {
        "found": grouped([x for x in shown if x["trigger"] != EVERY_PURCHASE]),
        "every_purchase": [x for x in shown if x["trigger"] == EVERY_PURCHASE],
        "total": len(questions),
        "from_findings": sum(1 for x in questions if x["trigger"] != EVERY_PURCHASE),
        "locked": len(questions) - len(shown),
    }
