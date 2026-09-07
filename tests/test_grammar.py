"""The grammar school layer: the register's selective state secondaries,
the page that lists them, and the report's nearby section."""
from app import db
from app.models import School, SchoolAdmissionRadius, SchoolDetail
from app.services import _cache, grammar


def _seed():
    with db.get_session() as session:
        session.merge(School(urn=900001, name="Testshire Grammar School", phase="Secondary", type_name="Academy converter", postcode="M14 5TG",
                             latitude=53.452, longitude=-2.222, ofsted_rating=1, ofsted_rating_label="Outstanding"))
        session.merge(SchoolDetail(urn=900001, town="Manchester", website="https://example.test", gender="Girls", admissions_policy="Selective", local_authority="Testshire"))
        session.merge(SchoolAdmissionRadius(urn=900001, last_distance_miles=2.4, academic_year="2025/26", source_authority="Testshire"))
        session.merge(School(urn=900002, name="Private Selective School", phase="Not applicable", type_name="Other independent school", postcode="M14 5TG",
                             latitude=53.453, longitude=-2.223))
        session.merge(SchoolDetail(urn=900002, admissions_policy="Selective", local_authority="Testshire"))
        session.commit()
    _cache._store.clear(); _cache._bytes = 0


def test_only_state_secondaries_count_and_the_distance_comes_along(client):
    _seed()
    rows = grammar.all_grammar_schools()
    names = [r["name"] for r in rows]
    assert "Testshire Grammar School" in names and "Private Selective School" not in names
    row = next(r for r in rows if r["urn"] == 900001)
    assert row["last_distance_miles"] == 2.4 and row["distance_year"] == "2025/26" and row["slug"] == "testshire-grammar-school"
    near = grammar.schools_near(53.45, -2.22)
    assert near and near[0]["urn"] == 900001 and near[0]["distance_m"] < 400
    assert grammar.schools_near(51.5, 0.5) == []


def test_the_page_lists_them_by_council_with_the_official_papers(client):
    _seed()
    body = client.get("/schools/grammar").text
    assert "Grammar schools in England" in body and "Testshire Grammar School" in body and "2.4 mi (2025/26)" in body
    assert "11plus.gl-assessment.co.uk/pages/free-materials" in body and "csse.org.uk" in body and "kent-test" in body
    assert "Private Selective School" not in body
