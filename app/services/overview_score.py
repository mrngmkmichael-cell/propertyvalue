"""Synthesizes the ~20 individual due-diligence signals already
fetched for a property page into one headline Overview Score.

This project has more raw categories than any single competitor
(sold prices, EPC, flood, crime, radon, broadband, mobile,
deprivation, census demographics, council tax, planning/heritage,
noise, air quality, schools) but previously made a user read all of
them one card at a time to form a verdict. This module is that
synthesis step.

Deliberately rule-based and deterministic, not an LLM call - no new
external API/cost, and it mirrors the exact thresholds already used
for the property page's "N things worth checking" attention banner
(property.html's {% set %}_status blocks), just computed once here in
Python so the same result can also feed the browser-extension JSON
API without duplicating the logic in two places.

18 Sep 2026 (first-visitor audit item D3): that mirroring had drifted.
YO1 7HH's verdict said "balanced against 2 things worth checking" and the
banner under it "3 things worth checking on this property", because the
banner also counted the council's exceptional financial support, and
BN1 1EE read 3 against 4. The template's list is gone: attention_items()
below is the one list, the verdict and the score are built from it, and
the report's banner is built from it too (property_search passes it to
the page), so the two counts cannot differ. It now holds everything the
banner held: the council finance flag, Flood Re, and the three locked
checks (development nearby, buses, GP list sizes), those last three
counted for a free reader and never named, like the other locked ones.
"""
from app.services import council_finance, crime, flood_re

CONCERN_LABELS = {
    "prosperity": "Area house prices falling",
    "extension": "Possible unrecorded extension/change",
    "flood": "Flood risk",
    "surface_water": "High surface water flood risk",
    "noise": "High noise levels",
    "radon": "Elevated radon risk",
    "air_quality": "Air quality well above WHO guideline",
    "landfill": "Historic landfill on/near site",
    "coal_mining": "In a Coal Mining Reporting Area",
    "sewage": "Frequent sewage discharges nearby",
    "clay_risk": "Rising subsidence risk from climate change",
    "planning": "Planning constraints present",
    "environmental": "Environmental designations present",
    "broadband": "Poor broadband availability",
    "mobile": "Poor mobile coverage",
    "deprivation": "Among more deprived areas nationally",
    # Said in full by council_finance.flag_sentence, with the council and
    # the year; this is only its fallback.
    "council_finance": "Council under exceptional financial support",
    "brownfield": "Development site on the brownfield register nearby",
    "bus": "Few or no scheduled buses nearby",
    "health": "Nearest GP practice well above the national list size per GP",
}

# The flood concern when the only reason is that Flood Re would not cover
# this home, which is not the same as the home being in a flood zone.
FLOOD_RE_LABEL = "Flood Re insurance not available for this home"

# The card each concern's reason opens on the report (16 Sep 2026):
# the verdict names its drivers, so each one is a way into its card.
CONCERN_MODALS = {
    "prosperity": "modal-sold-price-history",
    "extension": "modal-extension",
    "flood": "modal-flood",
    "surface_water": "modal-surface-water",
    "noise": "modal-noise",
    "radon": "modal-radon",
    "air_quality": "modal-air-quality",
    "landfill": "modal-historic-landfill",
    "coal_mining": "modal-coal-mining",
    "sewage": "modal-sewage",
    "clay_risk": "modal-clay-risk",
    "planning": "modal-planning",
    "environmental": "modal-environmental",
    "broadband": "modal-broadband",
    "mobile": "modal-mobile",
    "deprivation": "modal-deprivation",
    "council_finance": "modal-council-tax",
    "brownfield": "modal-brownfield",
    "bus": "modal-bus",
    "health": "modal-health",
}

GRADE_BANDS = [
    (85, "Excellent"),
    (70, "Good"),
    (50, "Fair"),
    (30, "Below average"),
    (0, "Poor"),
]

_CONCERN_PENALTY = 7
_POSITIVE_BONUS = 6
_BASE_SCORE = 70

# A school landscape with at least this share of Outstanding/Good
# schools counts as a positive - a flat, generous-enough approximation
# of "above the national average" (which sits in the high-60s%)
# without needing a live national-baseline query on every property
# page load.
_STRONG_SCHOOLS_THRESHOLD_PCT = 68
_EFFICIENT_EPC_BANDS = {"A", "B", "C"}

# Concern keys whose underlying dashboard card is Premium-gated
# (Extended or Modified, Air Quality, Historic Contamination). A
# free/non-premium score must not include these - showing "Air
# quality well above WHO guideline" in a free verdict would leak the
# gated card's actual finding without paying for it, undermining the
# lock on that card. Premium users get the full set.
_PREMIUM_ONLY_CONCERNS = {"extension", "air_quality", "landfill", "coal_mining", "sewage", "clay_risk",
                          "brownfield", "bus", "health"}


def _council_finance(context: dict) -> dict | None:
    """The council's finances for this address, looked up the way the
    report's council tax card looks them up (a local JSON read)."""
    location = context.get("location") or {}
    if not location:
        return None
    codes = location.get("codes") or {}
    return council_finance.for_council(
        codes.get("admin_district") or "",
        location.get("admin_district") or "",
        location.get("admin_county") or "",
    )


def _flood_re_action_needed(context: dict) -> bool:
    """Whether Flood Re would not cover this home in a place at risk, the
    flood card's own flood_re() reading."""
    detail = context.get("property_detail") or {}
    note = flood_re.assess(
        detail.get("year_built"),
        detail.get("dwelling_type") or "",
        context.get("flood_zone") or None,
        context.get("surface_water") or None,
    )
    return bool(note and note.get("action_needed"))


def _flood_zone_3_or_warned(context: dict) -> bool:
    flood_zone = context.get("flood_zone")
    return bool(context.get("flood_warnings") or (flood_zone and (flood_zone.get("zone") or 0) >= 3))


def _find_concerns(context: dict, premium_unlocked: bool) -> list[str]:
    """The keys of what is worth checking on this home, in the order the
    report lists them. Thresholds are the card statuses' in property.html,
    so a red-ringed card is always in the list and nothing else is."""
    concerns = []

    hpi = context.get("hpi")
    prosperity_area = None
    if hpi:
        prosperity_area = hpi.get("local_authority") or hpi.get("region")
    if prosperity_area and prosperity_area.get("annual_change_pct", 0) < 0:
        concerns.append("prosperity")

    ext = context.get("extension_signal")
    if ext and (ext.get("likely_extended") or (ext.get("change_pct") or 0) <= -15):
        concerns.append("extension")

    finance = _council_finance(context)
    if finance and finance.get("flag"):
        concerns.append("council_finance")

    # A home the Flood Re scheme would not cover, in a place at risk, is a
    # flood concern of its own, as it is on the flood card.
    flood_unread = context.get("flood_not_covered") or (context.get("flood_error") and context.get("flood_zone_error"))
    if not flood_unread and (_flood_zone_3_or_warned(context) or _flood_re_action_needed(context)):
        concerns.append("flood")

    surface_water = context.get("surface_water")
    if surface_water and surface_water.get("label") == "High risk":
        concerns.append("surface_water")

    noise = context.get("noise")
    if noise:
        noise_max = max(noise.get("road_db") or 0, noise.get("rail_db") or 0, noise.get("airport_db") or 0)
        if noise_max >= 65:
            concerns.append("noise")

    radon = context.get("radon")
    if radon and int(radon.get("class") or 0) >= 4:
        concerns.append("radon")

    # Both of these already flag their own card red on the report; they
    # were missing here, so a Premium reader could see a red-ringed card
    # the verdict never mentioned. Thresholds copied from the card
    # statuses in property.html so the two can't drift.
    clay = context.get("clay_risk")
    if clay and clay.get("class_2030") == "Probable":
        concerns.append("clay_risk")

    outfalls = context.get("sewage_outfalls")
    if outfalls and (outfalls[0].get("spill_count") or 0) >= 20:
        concerns.append("sewage")

    air_quality = context.get("air_quality")
    if air_quality and air_quality.get("pollutants"):
        aq_worst = max(p["times_guideline"] for p in air_quality["pollutants"])
        if aq_worst >= 3:
            concerns.append("air_quality")

    brownfield = context.get("brownfield")
    if brownfield and brownfield.get("covered") and brownfield.get("count") and (
        (brownfield.get("dwellings") or 0) >= 10
        or (brownfield.get("hectares") or 0) >= 0.5
        or brownfield.get("permissioned")
    ):
        concerns.append("brownfield")

    bus = context.get("bus_service")
    if bus and (not bus.get("count") or ((bus.get("best") or {}).get("weekday_day") or 0) < 12):
        concerns.append("bus")

    health = context.get("health")
    nearest = (health or {}).get("nearest") or {}
    if nearest.get("vs_median") and nearest["vs_median"] >= 1.3:
        concerns.append("health")

    landfill = context.get("historic_landfill")
    if landfill and landfill.get("status") != "clear":
        concerns.append("landfill")

    coal_mining = context.get("coal_mining")
    if coal_mining and coal_mining.get("present"):
        concerns.append("coal_mining")

    if context.get("planning_flags"):
        concerns.append("planning")

    if context.get("environmental_flags"):
        concerns.append("environmental")

    broadband = context.get("broadband")
    if broadband and (broadband.get("below_uso_pct") or 0) >= 5:
        concerns.append("broadband")

    mobile = context.get("mobile")
    if mobile and (mobile.get("no_4g_outdoor_pct") or 0) >= 5:
        concerns.append("mobile")

    deprivation = context.get("deprivation")
    if deprivation and deprivation.get("imd_decile") and deprivation["imd_decile"] <= 3:
        concerns.append("deprivation")

    if not premium_unlocked:
        concerns = [c for c in concerns if c not in _PREMIUM_ONLY_CONCERNS]

    return concerns


def _concern_text(key: str, context: dict) -> str:
    """The words for one concern, the same in the verdict and the banner."""
    if key == "council_finance":
        return council_finance.flag_sentence(_council_finance(context)) or CONCERN_LABELS[key]
    if key == "flood" and not _flood_zone_3_or_warned(context):
        return FLOOD_RE_LABEL
    return CONCERN_LABELS[key]


def attention_items(context: dict, premium_unlocked: bool = False) -> list[dict]:
    """The one list of things worth checking on this home: the verdict's
    concerns and the report's banner, both. Each item is {"key", "text",
    "modal"}, the modal being the card it opens. A locked check is left
    out for a reader who has not opened it (compute() counts how many,
    never which)."""
    return [
        {"key": key, "text": _concern_text(key, context), "modal": CONCERN_MODALS[key]}
        for key in _find_concerns(context, premium_unlocked=premium_unlocked)
    ]


def _find_positives(context: dict) -> list[str]:
    positives = []

    landscape = context.get("school_landscape")
    if landscape and landscape.get("good_or_better_pct") is not None:
        if landscape["good_or_better_pct"] >= _STRONG_SCHOOLS_THRESHOLD_PCT:
            positives.append(f"{landscape['good_or_better_pct']}% of nearby schools rated Outstanding or Good")

    certificates = context.get("certificates")
    if certificates and certificates[0].get("rating") in _EFFICIENT_EPC_BANDS:
        positives.append(f"Energy-efficient property (EPC {certificates[0]['rating']})")

    # Decided by crime.compare_counts on the two totals (18 Sep 2026),
    # the rule every surface uses. It counted the categories that were
    # lower and higher, and called 229 crimes against 230 lower.
    versus = crime.versus_area(context.get("crime"), context.get("district_crime"))
    if versus and versus["verdict"] == "lower":
        positives.append("Lower crime than the surrounding area")

    hpi = context.get("hpi")
    prosperity_area = None
    if hpi:
        prosperity_area = hpi.get("local_authority") or hpi.get("region")
    if prosperity_area and prosperity_area.get("annual_change_pct", 0) > 0:
        positives.append(f"Area prices rising ({prosperity_area['name']}, +{prosperity_area['annual_change_pct']:.1f}% YoY)")

    return positives


def _positive_modal(text: str) -> str:
    """The card a positive opens, from the four fixed phrasings above."""
    if "schools" in text:
        return "modal-schools"
    if text.startswith("Energy-efficient"):
        return "modal-epc"
    if text.startswith("Lower crime"):
        return "modal-crime"
    return "modal-sold-price-history"


def _grade_for(score: int) -> str:
    for threshold, label in GRADE_BANDS:
        if score >= threshold:
            return label
    return GRADE_BANDS[-1][1]


def _verdict_sentence(grade: str, concern_labels: list[str], positives: list[str]) -> str:
    """The verdict in words. concern_labels are attention_items' texts,
    the banner's chips, so the count here is the banner's count."""
    concerns = concern_labels
    if not concerns and not positives:
        return "No major signals either way from the data available for this property."
    if not concerns:
        return f"{grade} overall: " + "; ".join(positives) + "."
    concern_text = f"{len(concerns)} thing{'s' if len(concerns) != 1 else ''} worth checking ({', '.join(concern_labels)})"
    if not positives:
        return f"{grade} overall: {concern_text}."
    return f"{grade} overall: {'; '.join(positives)}, balanced against {concern_text}."


def compute(context: dict, premium_unlocked: bool = False) -> dict:
    """Returns {"score": int, "grade": str, "verdict": str, "positives": [...],
    "concerns": [...], "premium_extra_checks": int}. premium_extra_checks is
    how many additional concern checks Premium would factor in - shown as an
    upsell hint, not a specific finding, so it can't leak what those checks
    found."""
    all_concerns = _find_concerns(context, premium_unlocked=True)
    concerns = attention_items(context, premium_unlocked=premium_unlocked)
    concern_texts = [c["text"] for c in concerns]
    positives = _find_positives(context)

    score = _BASE_SCORE - len(concerns) * _CONCERN_PENALTY + len(positives) * _POSITIVE_BONUS
    score = max(0, min(100, score))
    grade = _grade_for(score)

    return {
        "score": score,
        "grade": grade,
        "verdict": _verdict_sentence(grade, concern_texts, positives),
        # The same sentence as parts, each with the card it came from,
        # so the report can make every reason a way into its card.
        "reasons": {
            "positives": [{"text": t, "modal": _positive_modal(t)} for t in positives],
            "concerns": [{"text": c["text"], "modal": c["modal"]} for c in concerns],
        },
        "positives": positives,
        "concerns": concern_texts,
        "premium_extra_checks": len(all_concerns) - len(concerns) if not premium_unlocked else 0,
    }


def school_verdict(school: dict) -> str | None:
    """A one-line synthesis of a school's Ofsted rating, exam results
    and destinations into a single plain-English sentence, for the
    school profile modal header - same "breadth into one answer" idea
    as compute() above, just for a school entry instead of a property.
    Returns None when there's nothing to synthesize (no rating, no
    exam data) rather than a hollow sentence."""
    parts = []

    rating = school.get("ofsted_rating_label")
    if rating in ("Outstanding", "Good"):
        parts.append(f"Ofsted-rated {rating}")
    elif rating in ("Requires improvement", "Inadequate"):
        parts.append(f"Ofsted rating: {rating}")

    exam = school.get("exam_results")
    if exam and exam.get("headline_value") is not None:
        if exam["headline_label"] == "Progress 8":
            if exam["headline_value"] >= 0.3:
                parts.append("pupils progress well above average")
            elif exam["headline_value"] <= -0.3:
                parts.append("pupils progress below average")
        elif exam["headline_value"] >= 75:
            parts.append(f"{exam['headline_value']}% meeting the expected standard")

    destinations = school.get("destinations")
    if destinations is not None:
        continuing = sum(
            (getattr(destinations, f, None) or 0)
            for f in ("school_sixth_form_pct", "sixth_form_college_pct", "further_education_pct")
        )
        if continuing >= 90:
            parts.append(f"{round(continuing)}% of leavers continue into further study")

    if not parts:
        return None
    sentence = "; ".join(parts)
    return sentence[0].upper() + sentence[1:] + "."
