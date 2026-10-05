"""Titles that fit what Google shows (5 Oct 2026).

The site audit found 27 of 84 sampled titles over 65 characters: every
comparison (78-80, the brand suffix on the end), council tax pages with
long council names, school pages with long school names, and every
admissions hub (a fixed 35-character tail). Google cuts a long title
mid-word, and the part it cuts is the answer: the distance, the band.
"""
import re

from app import main as app_main
from app.main import SEO_TITLE_LIMIT, fit_title


def _block(template_name, block, **context):
    template = app_main.templates.env.get_template(template_name)
    return "".join(template.blocks[block](template.new_context(context))).strip()


def test_fit_title_takes_the_first_form_that_fits():
    assert fit_title("a" * 70, "b" * 61, "c" * 50, "d" * 40) == "c" * 50


def test_fit_title_falls_back_to_the_shortest_when_nothing_fits():
    assert fit_title("a" * 80, "b" * 64, "c" * 70) == "b" * 64


def test_a_comparison_title_drops_the_brand_rather_than_run_to_80():
    for left, right in (("EC1M", "EC2Y"), ("BR1", "BR3"), ("M1", "M2")):
        title = _block("compare.html", "title", versus=True, left_code=left, right_code=right)
        assert len(title) <= SEO_TITLE_LIMIT, title
        assert title.startswith(f"{left} or {right}?")
        assert "UKPropertyInsight" not in title


def test_a_long_council_name_keeps_band_d_and_spares_the_list_of_bands():
    ct = {"authority": "North Northamptonshire", "year": "2026-27", "band_d": 2424.13, "top_band": "H"}
    title = _block("council_tax_council.html", "title", ct=ct)
    assert title == "Council Tax in North Northamptonshire 2026-27: Band D £2,424"


def test_a_short_council_name_is_not_changed_when_it_fits():
    ct = {"authority": "Bath", "year": "2026-27", "band_d": 2300.0, "top_band": "H"}
    title = _block("council_tax_council.html", "title", ct=ct)
    assert title == "Council Tax in Bath 2026-27: Band D £2,300, Bands A to H"


def _school(name, miles_label="4.38", year_phrase=", 2025/26", authority="Warwickshire", no_limit=False):
    return {"name": name, "authority": authority, "miles_label": miles_label,
            "year_phrase": year_phrase, "no_distance_limit": no_limit}


def test_a_short_school_name_keeps_its_authority_and_year():
    title = _block("school_admission.html", "title", school=_school("Ash School", "0.9", authority="Leeds"))
    assert title == "Ash School catchment area (Leeds): 0.9 miles, 2025/26"


def test_a_long_school_name_spares_authority_then_cofe_then_year():
    title = _block("school_admission.html", "title",
                   school=_school("Underwood Church of England Primary School"))
    # Authority gone, "CofE" for "Church of England", and the year last of all.
    assert title == "Underwood CofE Primary School catchment area: 4.38 miles"
    assert len(title) <= SEO_TITLE_LIMIT


def test_the_year_stays_when_cofe_alone_brings_the_title_in():
    title = _block("school_admission.html", "title",
                   school=_school("Ash Church of England Primary School", "0.5", ", 2025/26"))
    assert title == "Ash CofE Primary School catchment area: 0.5 miles, 2025/26"


def test_a_long_name_without_church_of_england_drops_the_year_not_the_name():
    title = _block("school_admission.html", "title",
                   school=_school("Whitehouse Common Primary School", "0.9", ", 2023/24", "Birmingham"))
    assert title == "Whitehouse Common Primary School catchment area: 0.9 miles"


def test_distance_did_not_limit_entry_is_never_reworded():
    """"No distance limit" would read as a standing fact about the
    school; the figure is one year's, so the careful words stay."""
    short = _block("school_admission.html", "title",
                   school=_school("Ash School", authority="Leeds", no_limit=True))
    assert short == "Ash School catchment area: distance did not limit entry"
    long = _block("school_admission.html", "title",
                  school=_school("Hartpury Church of England Primary School", no_limit=True))
    assert long == "Hartpury CofE Primary School catchment area: distance did not limit entry"


def test_an_apostrophe_is_measured_as_one_character_not_an_entity():
    """The title is measured before escaping, as Google displays it."""
    title = _block("school_admission.html", "title",
                   school=_school("St Peter's Church of England Middle School", "4.54"))
    assert "St Peter&#39;s CofE Middle School catchment area: 4.54 miles" == title
    assert len("St Peter's CofE Middle School catchment area: 4.54 miles") <= SEO_TITLE_LIMIT


def test_an_admissions_hub_says_catchment_areas_and_fits():
    for name in ("Nottinghamshire", "Bury", "Bracknell Forest"):
        title = _block("schools_admissions_council.html", "title",
                       council={"name": name}, hub={"phase_word": "primary "})
        assert title.startswith(f"{name} primary school catchment areas"), title
        assert len(title) <= SEO_TITLE_LIMIT, title
    assert _block("schools_admissions_council.html", "title", council={"name": "Bury"},
                  hub={"phase_word": "primary "}) == "Bury primary school catchment areas and admission distances"


def test_the_seo_check_measures_titles_unescaped():
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location(
        "seo_check", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "seo_check.py")
    seo_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seo_check)
    body = "<title>St Peter&#39;s &amp; All Saints</title>"
    assert seo_check.one("title", body) == "St Peter's & All Saints"
    assert re.fullmatch(r".{23}", seo_check.one("title", body))
