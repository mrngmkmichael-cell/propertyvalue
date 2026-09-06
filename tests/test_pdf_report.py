"""The premium PDF: the document renders from a fixture with every part
present, and the at-a-glance checklist says something for every check,
in words, when data is missing."""
import datetime

from app import main as app_main
from app.services import pdf_checklist, pdf_export


LOCATION = {
    "postcode": "KT3 4HX", "outcode": "KT3", "admin_district": "Kingston upon Thames", "region": "London",
    "country": "England", "latitude": 51.4025, "longitude": -0.2498,
}


def _report():
    return {
        "location": LOCATION,
        "overview": {"score": 81, "grade": "Good", "verdict": "Good overall: 90% of nearby schools rated Outstanding or Good.",
                     "positives": ["90% of nearby schools rated Outstanding or Good"], "concerns": ["High surface water flood risk"]},
        "transactions": [{"address": "36 MALDEN HILL GARDENS", "date": "1996-06-28", "amount": "116000", "tenure": "Freehold", "new_build": False}],
        "valuation": {"estimate": 928000.0, "low": 893000.0, "high": 963000.0, "sample_size": 2, "years_window": 1, "floor_area_variance_pct": 5},
        "price_trend": {"area_name": "Kingston upon Thames", "start_price": 534433.0, "current_price": 594498.0, "pct_change": 11.2,
                        "projections": [{"months_ahead": 12, "price": 577730.0}]},
        "hpi": {"local_authority": {"name": "Kingston upon Thames", "average_price": 594498.0, "annual_change_pct": 2.9, "period": "2026-06"},
                "region": {"name": "London", "average_price": 553870.0, "annual_change_pct": -2.5, "period": "2026-06"},
                "country": {"name": "England", "average_price": 293262.0, "annual_change_pct": 1.8, "period": "2026-06"}},
        "rental": {"la_name": "Kingston upon Thames", "period": "2026-06", "price_all": 1807, "change_all_pct": -0.4,
                   "by_bedroom": [{"label": "1 bed", "price": 1375, "change_pct": -0.2}]},
        "council_tax": {"authority": "Kingston upon Thames", "year": "2026-27", "band_d": 2609.2,
                        "bands": {"A": 1739.47, "B": 2029.38, "C": 2319.29, "D": 2609.2, "E": 3189.02, "F": 3768.84, "G": 4348.67, "H": 5218.4},
                        "basis": "Band D is the authority's published average area charge (MHCLG)."},
        "property_detail": {"dwelling_type": "Semi-detached house", "total_floor_area": 130, "habitable_room_count": 8, "year_built": "1930–1949",
                            "current_score": 51, "potential_score": 71, "current_band": "E", "potential_band": "C", "inspection_date": "2026-01-28",
                            "valid_until": "2036-01-28", "heating_cost_current": 1792, "heating_cost_potential": 1184, "lighting_cost_current": 72,
                            "lighting_cost_potential": 72, "hot_water_cost_current": 328, "hot_water_cost_potential": 328},
        "certificates": [{"address": "36 Malden Hill Gardens", "rating": "E", "date": "2026-01-28"}],
        "mees_compliant": True, "lead_plumbing_era": True,
        "orientation": {"front_facing": "North", "rear_facing": "South", "nearest_road": "Malden Hill Gardens"},
        "flood_zone": {"zone": 1, "label": "Zone 1 (low probability)"}, "flood_warnings": [],
        "surface_water": {"label": "High risk", "probability": "Greater than 1 in 30 (3.3%)"},
        "sewage_outfalls": [{"name": "Westway Close SPS", "water_company": "Thames Water", "year": "2025", "spill_count": 1, "duration_hrs": 0.5, "receiving_water": "Old Pyl Ditch", "distance_m": 977}],
        "noise": {"road_db": 56, "road_label": "Moderate", "rail_db": None, "rail_label": None, "airport_db": None, "airport_label": None},
        "crime": {"total": 229, "month": "2026-07", "by_category": [{"category": "violent crime", "count": 68}, {"category": "burglary", "count": 12}]},
        "district_crime": {"total": 230},
        "radon": {"class": "1", "label": "Low (under 1% of homes above the Action Level)"},
        "clay_risk": {"label_2030": "Improbable", "label_2050": "Possible"},
        "coal_mining_error": True, "historic_landfill": {"status": "clear"},
        "air_quality": {"year": 2024, "pollutants": [{"label": "NO2", "value": 15.6, "who_guideline": 10, "times_guideline": 1.6}]},
        "planning_flags": [], "environmental_flags": [], "designations": {"bua": {"label": "Built-up Area", "group": "other", "present": True}},
        "heritage": [{"name": "New Malden War Memorial", "grade": "II", "distance_m": 448}],
        "school_landscape": {"radius_miles": 3, "total_schools": 76, "good_or_better_pct": 90, "special_count": 5,
                             "by_rating": [{"label": "Outstanding", "count": 7}, {"label": "Good", "count": 29}],
                             "independent_schools": [{"name": "Westbury House School", "age_low": 2, "age_high": 11, "gender": "Mixed", "selective": False, "religious_character": "None", "distance_m": 1069}],
                             "higher_education": [], "higher_education_names": []},
        "schools": {"Primary": [{"name": "Burlington Junior School", "type": "Community school", "distance_m": 368, "ofsted_rating_label": "Good", "ofsted_note": "",
                                 "fsm_eligible_pct": 17.7, "exam_results": {"headline_label": "Meeting expected standard", "headline_value": 72.0, "academic_year": "2024/25"}}]},
        "catchment_distance_schools": [{"name": "Burlington Infant and Nursery School", "phase_group": "Primary", "radius_miles": 0.59, "is_real": True,
                                        "academic_year": "2024/25", "source_authority": "Kingston upon Thames", "property_distance_miles": 0.23,
                                        "verdict": {"level": "likely", "label": "Likely", "why": "comfortably inside"}}],
        "stations_list": {"rail": [{"name": "New Malden", "network": "National Rail", "distance_m": 443, "walking_duration_min": 8}], "tube": [], "tram": [],
                          "bus": [{"name": "Queens Road", "distance_m": 385}]},
        "broadband": {"gigabit_pct": 100.0, "superfast_pct": 100.0, "below_uso_pct": 0.0, "label": "Gigabit-capable"},
        "mobile": {"la_name": "Kingston upon Thames", "coverage_4g_outdoor_all_pct": 99.99, "coverage_4g_indoor_all_pct": 98.84, "coverage_5g_outdoor_pct": 61.05},
        "amenities": {"supermarket": [{"name": "Lidl", "distance_m": 373}], "pharmacy": [], "gp": [{"name": "Roselawn Surgery", "distance_m": 650}]},
        "household_income": {"here": 92372, "la_name": "Kingston upon Thames", "la_average": 91149, "region_name": "London", "region_average": 75745},
        "deprivation": {"imd_decile": 8, "domains": [{"label": "Income", "decile": 7}]}, "imd_label": "Less deprived than average",
        "occupation": {"professional_pct": 55.6, "breakdown": [{"label": "Managers", "pct": 21.4}]},
        "qualification": {"degree_pct": 61.3, "breakdown": [{"label": "Level 4+", "pct": 61.3}]},
        "age_profile": {"under_25_pct": 31.2, "breakdown": [{"label": "Under 15", "pct": 22.7}]},
        "housing": {"owned_pct": 77.6, "type_breakdown": [{"label": "Detached", "pct": 7.1}], "tenure_breakdown": [{"label": "Owned outright", "pct": 34.3}]},
        "background": {"born_abroad_pct": 32.9, "country_of_birth_breakdown": [{"label": "United Kingdom", "pct": 67.1}]},
        "wellbeing": {"good_health_pct": 88.0, "health_breakdown": [{"label": "Very good health", "pct": 56.6}], "nssec_breakdown": []},
        "new_build_stat": {"pct": 21, "count": 37, "total": 177, "years": 3},
    }


def _running_costs():
    return {
        "postcode": "KT3 4HX", "district": "Kingston upon Thames", "outcode": "KT3", "house_number": "36",
        "home": {"address": "36 Malden Hill Gardens", "epc_date": "2026-01-28", "band": "E", "energy_now": 2192, "energy_potential": 1584,
                 "tenure": "freehold", "sale_year": "1996"},
        "energy": {"certificates": 25, "priced": 8, "low": 462, "high": 2192, "median": 974, "median_potential": 671},
        "sales": {"sales": 29, "counts": {"freehold": 13, "leasehold": 16}, "latest_year": "2025", "latest_amount": 823500.0, "median_recent": 412000.0, "recent_n": 10},
        "district_prices": {"outcode": "KT3", "median": 475000, "count": 92},
        "stamp_duty": {"price": 116000.0, "basis": "this home's last sale", "standard": 0, "first_time": 0, "additional": 5800},
        "typical_year": 4801, "typical_share_pct": 5.2, "income_value": 92372,
    }


def test_the_pdf_renders_every_part_with_the_running_costs_in_it():
    ctx = app_main._pdf_context(_report(), _running_costs(), LOCATION, "36")
    html = app_main.templates.get_template("pdf_report_full.html").render(ctx)
    for part in ("Summary", "Value and market", "Running costs", "The property", "Risk and safety", "Planning and heritage",
                 "Schools", "Getting around and staying connected", "Area and community", "Questions to ask", "Sources and method"):
        assert f"<h1>{part}</h1>" in html, part
    # Running costs, on the page and in the checklist, from the same numbers the site shows.
    assert "£2,609 a year" in html and "£2,192 a year" in html
    # Stamp duty is worked on the valuation, not the 1996 sale.
    assert "£36,550" in html and "the valuation estimate" in html
    assert "Every check, at a glance" in html and "Storm overflows nearby" in html
    assert ctx["generated_date"] == f"{datetime.date.today().day} {datetime.date.today():%B %Y}"
    pdf = _render_in_a_fresh_process(html)
    assert pdf and pdf[:4] == b"%PDF" and len(pdf) > 40_000


def _render_in_a_fresh_process(html: str) -> bytes:
    """The engine runs in its own interpreter here. Inside the full suite
    on Windows the same call hit an access violation about one run in
    three, inside xhtml2pdf's CSS matching, never on its own; on the
    server it runs on a worker thread of a long-lived process, which
    this mirrors more honestly than sharing pytest's."""
    import pathlib
    import subprocess
    import sys
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "report.html"
        out = pathlib.Path(tmp) / "report.pdf"
        src.write_text(html, encoding="utf-8")
        code = (
            "import pathlib, sys; sys.path.insert(0, '.'); from app.services import pdf_export; "
            "pdf = pdf_export.html_to_pdf(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8')); "
            "pathlib.Path(sys.argv[2]).write_bytes(pdf or b'')"
        )
        subprocess.run([sys.executable, "-c", code, str(src), str(out)], check=True, timeout=120)
        return out.read_bytes()


def test_the_checklist_says_what_is_missing_in_words():
    rows = pdf_checklist.build({"transactions": [], "school_landscape": None}, {})
    by_check = {r["check"]: r for r in rows}
    assert by_check["Sold prices at this postcode"]["result"] == "No recorded sales at this postcode"
    assert "Not enough comparable sales" in by_check["Valuation estimate"]["result"]
    assert by_check["Schools nearby"]["result"] == "No school data for this address"
    assert by_check["Coal mining reporting area"]["result"] == "Not in a reporting area"
    assert all(r["result"] for r in rows), "every row says something"
    assert all(r["status"] in ("good", "warn", "bad", "neutral") for r in rows)


def test_the_checklist_reads_the_full_report():
    rows = pdf_checklist.build(_report(), _running_costs(), stamp_duty={"price": 928000.0, "standard": 36550, "first_time": None, "additional": 82950})
    by_check = {r["check"]: r for r in rows}
    assert by_check["Surface water flooding"]["status"] == "bad"
    assert by_check["Flood zone, rivers and sea"]["status"] == "good"
    assert "£36,550 moving home" in by_check["Stamp duty, one-off"]["result"]
    assert by_check["Admission distances"]["result"].startswith("1 schools with a published") or "1 school" in by_check["Admission distances"]["result"]
    assert len(rows) >= 40
