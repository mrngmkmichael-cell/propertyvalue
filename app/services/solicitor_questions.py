"""Questions to ask before you buy, generated from this property's own
report findings.

Pure rules over the context the report page already computed - no new
data source and no model involved, so every question can name the
finding that triggered it. Thresholds deliberately mirror the ones the
report cards use for their own attention states (see property.html),
so a question never appears without the card that explains it also
flagging up.

Each question dict: {"audience", "trigger", "question", "why", "cost"}.
"cost" is a typical third-party search/report fee as a plain string,
only where one is well established, otherwise empty.
"""

AUDIENCES = ("Ask the seller", "For your solicitor", "For your surveyor")


def _noise_max(noise: dict | None) -> int | None:
    if not noise:
        return None
    return max(noise.get("road_db") or 0, noise.get("rail_db") or 0, noise.get("airport_db") or 0)


def build(context: dict) -> list[dict]:
    q: list[dict] = []

    def add(audience, trigger, question, why, cost=""):
        q.append({"audience": audience, "trigger": trigger, "question": question, "why": why, "cost": cost})

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
            "around £40")

    landfill = context.get("historic_landfill")
    if landfill and landfill.get("status") != "clear":
        add("For your solicitor", "Historic landfill on or near the site",
            "Order an environmental search and ask whether contaminated land liability could pass to the buyer.",
            "Under Part 2A the current owner can inherit clean-up liability if the original polluter cannot be found.",
            "around £50-£110")

    outfalls = context.get("sewage_outfalls")
    if outfalls and (outfalls[0].get("spill_count") or 0) >= 20:
        add("For your solicitor", "Frequent sewage discharges nearby",
            "Order the CON29DW drainage and water search, and check where the property's foul water drains.",
            "It confirms mains connection, shared drains and who maintains what, and it names the sewerage undertaker responsible for problems.",
            "around £40-£60")

    noise_max = _noise_max(context.get("noise"))
    if noise_max is not None and noise_max >= 65:
        add("Ask the seller", f"Modelled noise up to {noise_max} dB(A)",
            "Have you ever complained about noise, or has any neighbour complained about you?",
            "Noise complaints must be declared on the TA6 form. Visit at rush hour and late evening before deciding.")

    clay = context.get("clay_risk")
    if clay and clay.get("class_2030") == "Probable":
        add("For your surveyor", "Rising clay subsidence risk",
            "Look specifically for movement: cracks over doors and windows, sticking frames, and how close large trees stand to the walls.",
            "Shrink-swell clay moves with wet and dry years. Past underpinning or a subsidence claim also raises insurance sharply, so ask the insurer about street history.")

    ext = context.get("extension_signal")
    if ext and ext.get("likely_extended"):
        add("Ask the seller", f"Floor area grew about {ext.get('change_pct', 0):+.0f}% between energy certificates",
            "Which works were done, and can you provide the planning permission and building regulations completion certificates?",
            "Works without sign-off become the buyer's problem. Indemnity insurance covers enforcement, not safety.")

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

    # Every purchase, regardless of findings. Kept short on purpose.
    add("For your solicitor", "Every purchase",
        "Confirm the tenure. If leasehold: years remaining, ground rent terms, service charge history and any planned major works.",
        "A lease under about 80 years or a doubling ground rent can cost tens of thousands to fix and some lenders refuse them.")
    add("For your solicitor", "Every purchase",
        "Check the title plan boundaries match what you saw, and who owns and maintains each fence, wall, and any shared access.",
        "Boundary surprises are the most common post-completion dispute, and they are nearly free to catch before exchange.")
    add("Ask the seller", "Every purchase",
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
