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
"""

CONCERN_LABELS = {
    "prosperity": "Area house prices falling",
    "extension": "Possible unrecorded extension/change",
    "flood": "Flood risk",
    "noise": "High noise levels",
    "radon": "Elevated radon risk",
    "air_quality": "Air quality well above WHO guideline",
    "landfill": "Historic landfill on/near site",
    "coal_mining": "In a Coal Mining Reporting Area",
    "planning": "Planning constraints present",
    "environmental": "Environmental designations present",
    "broadband": "Poor broadband availability",
    "mobile": "Poor mobile coverage",
    "deprivation": "Among more deprived areas nationally",
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
_PREMIUM_ONLY_CONCERNS = {"extension", "air_quality", "landfill", "coal_mining"}


def _find_concerns(context: dict, premium_unlocked: bool) -> list[str]:
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

    flood_zone = context.get("flood_zone")
    if context.get("flood_warnings") or (flood_zone and flood_zone.get("zone", 0) >= 3):
        concerns.append("flood")

    noise = context.get("noise")
    if noise:
        noise_max = max(noise.get("road_db") or 0, noise.get("rail_db") or 0, noise.get("airport_db") or 0)
        if noise_max >= 65:
            concerns.append("noise")

    radon = context.get("radon")
    if radon and int(radon.get("class") or 0) >= 4:
        concerns.append("radon")

    air_quality = context.get("air_quality")
    if air_quality and air_quality.get("pollutants"):
        aq_worst = max(p["times_guideline"] for p in air_quality["pollutants"])
        if aq_worst >= 3:
            concerns.append("air_quality")

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


def _find_positives(context: dict) -> list[str]:
    positives = []

    landscape = context.get("school_landscape")
    if landscape and landscape.get("good_or_better_pct") is not None:
        if landscape["good_or_better_pct"] >= _STRONG_SCHOOLS_THRESHOLD_PCT:
            positives.append(f"{landscape['good_or_better_pct']}% of nearby schools rated Outstanding or Good")

    certificates = context.get("certificates")
    if certificates and certificates[0].get("rating") in _EFFICIENT_EPC_BANDS:
        positives.append(f"Energy-efficient property (EPC {certificates[0]['rating']})")

    comparison = context.get("crime_comparison")
    if comparison:
        lower = sum(1 for row in comparison if row["trend"] == "lower")
        higher = sum(1 for row in comparison if row["trend"] == "higher")
        if lower > higher:
            positives.append("Lower crime than the surrounding area")

    hpi = context.get("hpi")
    prosperity_area = None
    if hpi:
        prosperity_area = hpi.get("local_authority") or hpi.get("region")
    if prosperity_area and prosperity_area.get("annual_change_pct", 0) > 0:
        positives.append(f"Area prices rising ({prosperity_area['name']}, +{prosperity_area['annual_change_pct']:.1f}% YoY)")

    return positives


def _grade_for(score: int) -> str:
    for threshold, label in GRADE_BANDS:
        if score >= threshold:
            return label
    return GRADE_BANDS[-1][1]


def _verdict_sentence(grade: str, concerns: list[str], positives: list[str]) -> str:
    concern_labels = [CONCERN_LABELS[c] for c in concerns]
    if not concerns and not positives:
        return "No major signals either way from the data available for this property."
    if not concerns:
        return f"{grade} overall — " + "; ".join(positives) + "."
    concern_text = f"{len(concerns)} thing{'s' if len(concerns) != 1 else ''} worth checking ({', '.join(concern_labels)})"
    if not positives:
        return f"{grade} overall — {concern_text}."
    return f"{grade} overall — {'; '.join(positives)}, balanced against {concern_text}."


def compute(context: dict, premium_unlocked: bool = False) -> dict:
    """Returns {"score": int, "grade": str, "verdict": str, "positives": [...],
    "concerns": [...], "premium_extra_checks": int}. premium_extra_checks is
    how many additional concern checks Premium would factor in - shown as an
    upsell hint, not a specific finding, so it can't leak what those checks
    found."""
    all_concerns = _find_concerns(context, premium_unlocked=True)
    concerns = _find_concerns(context, premium_unlocked=premium_unlocked)
    positives = _find_positives(context)

    score = _BASE_SCORE - len(concerns) * _CONCERN_PENALTY + len(positives) * _POSITIVE_BONUS
    score = max(0, min(100, score))
    grade = _grade_for(score)

    return {
        "score": score,
        "grade": grade,
        "verdict": _verdict_sentence(grade, concerns, positives),
        "positives": positives,
        "concerns": [CONCERN_LABELS[c] for c in concerns],
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
