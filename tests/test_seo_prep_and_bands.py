"""The two Search Console fixes of 16 Sep 2026.

1. Council private-school pages answer "prep school <town>" and "private
   primary schools <town>": schools grouped by stage from the register's
   age ranges, a stage sentence in the opening, and a question for each
   stage. The title keeps "Private schools in <council>" exactly, since
   that is the biggest query family.
2. Council tax pages put every band's amount in the search description,
   bands first, so a search for one band sees its figure; and every band
   is a question of its own."""
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SCHOOLS = [
    # urn, name, type, gender, age_low, age_high
    (990501, "Acorn Prep School", "Other independent school", "Mixed", 3, 11),
    (990502, "Beech Prep", "Other independent school", "Boys", 7, 13),
    (990503, "Cedar College", "Other independent school", "Girls", 11, 18),
    (990504, "Delta All-Through School", "Other independent school", "Mixed", 4, 18),
    (990505, "Elm Special School", "Other independent special school", "Mixed", 5, 19),
    (990506, "Fir House", "Other independent school", "Mixed", None, None),
]


def _seed_testshire():
    from app import db
    from app.models import School, SchoolDetail
    from app.services import _cache
    with db.get_session() as session:
        for urn, name, type_name, gender, low, high in SCHOOLS:
            if session.get(School, urn) is None:
                session.add(School(urn=urn, name=name, phase="Not applicable", type_name=type_name,
                                   postcode="TS1 1AA", latitude=54.57, longitude=-1.23))
                session.add(SchoolDetail(urn=urn, local_authority="Testshire", town="Testtown", gender=gender,
                                         religious_character="None", age_low=low, age_high=high))
        session.commit()
    _cache._store.clear()
    _cache._bytes = 0


# ---- 1. Private schools by stage ----------------------------------------------

def test_a_school_stage_comes_only_from_its_registered_age_range():
    from app.services.schools_db import independent_stage
    assert independent_stage(3, 11) == "prep_primary"
    assert independent_stage(7, 13) == "prep_primary"
    assert independent_stage(2, 16) == "all_through"
    assert independent_stage(4, 18) == "all_through"
    assert independent_stage(11, 18) == "senior_only"
    assert independent_stage(13, 18) == "senior_only"
    assert independent_stage(None, 18) == "age_unstated"
    assert independent_stage(3, None) == "age_unstated"


def test_names_read_as_plain_english():
    from app.main import _name_list
    assert _name_list([]) == ""
    assert _name_list(["Acorn"]) == "Acorn"
    assert _name_list(["Acorn", "Beech"]) == "Acorn and Beech"
    assert _name_list(list("ABCDEFG")) == "A, B, C, D, E and 2 more"


def test_the_council_page_groups_private_schools_by_stage(client):
    _seed_testshire()
    body = client.get("/schools/independent/testshire").text

    # The title still leads with the biggest query, exactly.
    assert "<title>Private schools in Testshire" in body
    assert "<h1>Private schools in Testshire</h1>" in body

    headings = re.findall(r"<h2>(.*?)</h2>", body)
    assert headings[:5] == [
        "Private primary and prep schools in Testshire",
        "All-through private schools in Testshire",
        "Private senior schools in Testshire",
        "Private schools in Testshire with no age range on the register",
        "Independent special schools in Testshire",
    ]

    def section(heading):
        return body.split(f"<h2>{heading}</h2>", 1)[1].split("</section>", 1)[0]

    prep = section("Private primary and prep schools in Testshire")
    assert "2 schools. Ages up to 11 or 13" in prep
    assert "Acorn Prep School" in prep and "Beech Prep" in prep and "Cedar College" not in prep
    assert "Cedar College" in section("Private senior schools in Testshire")
    assert "Delta All-Through School" in section("All-through private schools in Testshire")
    assert "Fir House" in section("Private schools in Testshire with no age range on the register")
    assert "Elm Special School" in section("Independent special schools in Testshire")

    dek = re.sub(r"\s+", " ", re.search(r'<p class="dek">(.*?)</p>', body, re.S).group(1))
    assert ("Of the mainstream schools, 2 take pupils up to 11 or 13, 1 runs from primary age "
            "into the senior years and 1 starts at 11 or 13.") in dek


def test_the_council_page_answers_the_prep_and_senior_questions(client):
    import json
    _seed_testshire()
    body = client.get("/schools/independent/testshire").text
    faq = next(json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
               if '"FAQPage"' in s)
    answers = {q["name"]: q["acceptedAnswer"]["text"] for q in faq["mainEntity"]}
    names = list(answers)
    assert names[1] == "Which private primary and prep schools are there in Testshire?"
    assert names[2] == "Which private senior schools are there in Testshire?"
    assert answers[names[1]] == (
        "2 independent schools in Testshire take pupils up to 11 or 13, by their registered age ranges: "
        "Acorn Prep School and Beech Prep. 1 all-through school also takes primary-age pupils."
    )
    assert answers[names[2]] == (
        "By their registered age ranges, 1 starts at 11 or 13 (Cedar College) and 1 runs through from primary age."
    )


def test_a_council_with_no_senior_only_school_says_so(client):
    import json
    from app import db
    from app.models import School, SchoolDetail
    from app.services import _cache
    with db.get_session() as session:
        for urn, name, low, high in ((990511, "Oak Prep", 3, 11), (990512, "Ash School", 3, 18), (990513, "Yew School", 4, 16)):
            if session.get(School, urn) is None:
                session.add(School(urn=urn, name=name, phase="Not applicable", type_name="Other independent school",
                                   postcode="NW1 1AA", latitude=51.5, longitude=-0.1))
                session.add(SchoolDetail(urn=urn, local_authority="Nosenior", town="Town", gender="Mixed",
                                         religious_character="None", age_low=low, age_high=high))
        session.commit()
    _cache._store.clear()
    _cache._bytes = 0
    body = client.get("/schools/independent/nosenior").text
    faq = next(json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
               if '"FAQPage"' in s)
    answers = {q["name"]: q["acceptedAnswer"]["text"] for q in faq["mainEntity"]}
    assert answers["Which private senior schools are there in Nosenior?"] == (
        "None registered in Nosenior starts at 11 or 13, but 2 all-through schools take senior pupils: Ash School and Yew School."
    )
    assert "Private senior schools in Nosenior" not in body


def test_the_district_page_title_no_longer_promises_fees(client):
    template = (ROOT / "app" / "templates" / "area_private_schools.html").read_text(encoding="utf-8")
    title = template.split("{% block title %}", 1)[1].split("{% endblock %}", 1)[0]
    assert "fees" not in title
    assert ": ages, selection and distance" in title


# ---- 2. Council tax bands in the search result --------------------------------

def _description(body: str) -> str:
    return html.unescape(re.search(r'<meta name="description" content="([^"]*)"', body).group(1))


def test_the_council_tax_description_leads_with_every_band(client):
    from app.services import council_tax
    data = council_tax.page("basildon")
    body = client.get("/running-costs/council-tax/basildon").text
    description = _description(body)
    bands = "".join(re.findall(r"\b([A-I]) £", description))
    assert bands == "ABCDEFGH"
    assert description.startswith(f"Basildon council tax {data['year']}: Band A £{data['bands']['A']:,.0f}, B £")
    assert f"C £{data['bands']['C']:,.0f}" in description
    # Every band comes before the rank and the source.
    assert description.index(f"H £") < description.index("The ") < description.index("Source:")


def test_wales_lists_its_ninth_band(client):
    body = client.get("/running-costs/council-tax/cardiff").text
    assert "".join(re.findall(r"\b([A-I]) £", _description(body))) == "ABCDEFGHI"


def test_every_council_tax_page_shows_all_its_bands_before_google_cuts_the_snippet():
    """Google shows about 155 characters of a description on desktop. The
    bands lead so that only the rank and source are ever cut; this guards
    against a long council name pushing a band past the cut."""
    from app.main import templates
    from app.services import council_tax
    source = (ROOT / "app" / "templates" / "council_tax_council.html").read_text(encoding="utf-8")
    block = templates.env.from_string(source.split("{% block meta_description %}", 1)[1].split("{% endblock %}", 1)[0])
    everyone = council_tax.all_authorities()
    checked = 0
    for nation in ("england", "wales", "scotland"):
        for row in everyone[nation]:
            data = council_tax.page(row["slug"])
            if not data:
                continue
            text = html.unescape(block.render(ct=data))
            assert text.index(". The ") <= 155, (data["authority"], text)
            checked += 1
    assert checked > 300


def test_every_band_is_a_question_of_its_own(client):
    import json
    from app.services import council_tax
    data = council_tax.page("basildon")
    body = client.get("/running-costs/council-tax/basildon").text
    faq = next(json.loads(s) for s in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
               if '"FAQPage"' in s)
    second = faq["mainEntity"][1]
    assert second["name"] == f"What are the council tax bands in Basildon for {data['year']}?"
    answer = second["acceptedAnswer"]["text"]
    assert answer.startswith(f"Band A £{data['bands']['A']:,.2f}, Band B £{data['bands']['B']:,.2f}")
    assert f"Band H £{data['bands']['H']:,.2f}. A home's band is shown on its council tax bill." in answer
