"""The bus service and the embeddable badge on a school page."""
import datetime

from app import db
from app.models import BusStop, School, SchoolAdmissionRadius, SchoolDetail
from app.services import _cache


def _seed():
    with db.get_session() as session:
        session.query(BusStop).delete()  # test_bus_service leaves stops at these coordinates
        session.merge(School(urn=900101, name="Riverside Primary School", phase="Primary", type_name="Community school", postcode="M14 5TG",
                             latitude=53.4501, longitude=-2.2201, ofsted_rating=2, ofsted_rating_label="Good"))
        session.merge(SchoolDetail(urn=900101, town="Manchester", admissions_policy="Not applicable", local_authority="Manchester"))
        session.merge(SchoolAdmissionRadius(urn=900101, last_distance_miles=0.62, academic_year="2025/26", source_authority="Manchester"))
        session.merge(BusStop(atco_code="T001", name="Riverside Road", latitude=53.4505, longitude=-2.2205, weekday_day=72, weekday_eve=12, sunday_day=27,
                              weekday_first="06:10", weekday_last="23:05", routes='["42", "142"]',
                              feed_date=datetime.date(2026, 9, 7), ref_weekday=datetime.date(2026, 9, 8), ref_sunday=datetime.date(2026, 9, 13)))
        session.commit()
    _cache._store.clear(); _cache._bytes = 0


def test_the_school_page_shows_its_bus_service_and_offers_a_badge(client):
    _seed()
    r = client.get("/school/900101/riverside-primary-school")
    assert r.status_code == 200
    assert "Getting to Riverside Primary School by bus" in r.text and "6.0 buses an hour" in r.text and "42, 142" in r.text
    assert "/school/900101/badge.svg" in r.text and "Put Riverside Primary School" in r.text
    badge = client.get("/school/900101/badge.svg")
    assert badge.status_code == 200 and badge.headers["content-type"].startswith("image/svg+xml")
    assert "Admitted from 0.62 miles" in badge.text and "Riverside Primary School" in badge.text
    assert client.get("/school/900199/badge.svg").status_code == 404



def test_a_year_that_is_not_a_year_never_reaches_a_title(client):
    """832 profiles carry "varies" as the academic year, which is what the
    council publishes. It must never read "in varies" anywhere."""
    from app import db
    from app.models import School, SchoolAdmissionRadius, SchoolDetail
    from app.services import _cache
    with db.get_session() as session:
        session.merge(School(urn=900102, name="Varies Primary School", phase="Primary", type_name="Community school", postcode="N10 3HS",
                             latitude=51.59, longitude=-0.14))
        session.merge(SchoolDetail(urn=900102, town="London", admissions_policy="Not applicable", local_authority="Haringey"))
        session.merge(SchoolAdmissionRadius(urn=900102, last_distance_miles=1.8826, academic_year="varies", source_authority="Haringey"))
        session.commit()
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/school/900102/varies-primary-school").text
    assert "in varies" not in body and "varies" not in body.split("<title>")[1].split("</title>")[0]
    assert "<title>Varies Primary School catchment area" in body and "1.88 miles" in body
    assert "Admitted from, latest published year" in body
    badge = client.get("/school/900102/badge.svg").text
    assert "Admitted from 1.88 miles" in badge and "latest published year, Haringey" in badge and "varies" not in badge


def test_the_admissions_hub_links_the_near_miss_schools(client):
    from app import db, main as app_main
    from app.models import School, SchoolAdmissionRadius
    from app.services import _cache
    urn = app_main.NEAR_MISS_SCHOOL_URNS[9]                    # Fortismere, the page with the most impressions
    with db.get_session() as session:
        session.merge(School(urn=urn, name="Fortismere School", phase="Secondary", type_name="Academy converter", postcode="N10 1NE",
                             latitude=51.59, longitude=-0.15))
        session.merge(SchoolAdmissionRadius(urn=urn, last_distance_miles=1.8826, academic_year="varies", source_authority="Haringey"))
        session.commit()
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/schools/admissions").text
    assert "Schools people are checking" in body and f'href="/school/{urn}/fortismere-school"' in body and "1.88 mi" in body


def test_the_map_offers_the_nearest_schools_distances(client):
    """Step 1 of the catchment map: a school with published-distance
    neighbours gets a tick box, a legend and the rings' coordinates in
    the page's map payload; a school with none gets nothing."""
    from app import db
    from app.models import School, SchoolAdmissionRadius, SchoolDetail
    from app.services import _cache
    with db.get_session() as session:
        session.merge(School(urn=900105, name="Riverbank Primary School", phase="Primary", type_name="Community school", postcode="M14 5TG",
                             latitude=53.4501, longitude=-2.2201))
        session.merge(SchoolDetail(urn=900105, town="Manchester", local_authority="Manchester"))
        session.merge(SchoolAdmissionRadius(urn=900105, last_distance_miles=0.62, academic_year="2025/26", source_authority="Manchester"))
        session.merge(School(urn=900106, name="Nextdoor Junior School", phase="Primary", type_name="Community school", postcode="M14 6AA",
                             latitude=53.4560, longitude=-2.2300))
        session.merge(SchoolDetail(urn=900106, town="Manchester", local_authority="Manchester"))
        session.merge(SchoolAdmissionRadius(urn=900106, last_distance_miles=0.41, academic_year="2025/26", source_authority="Manchester"))
        session.merge(School(urn=900107, name="Lonely Academy", phase="Secondary", type_name="Academy", postcode="ZZ1 1AA",
                             latitude=56.0, longitude=1.5))
        session.merge(SchoolDetail(urn=900107, town="Aberdeen", local_authority="Aberdeen City"))
        session.merge(SchoolAdmissionRadius(urn=900107, last_distance_miles=2.0, academic_year="2025/26", source_authority="Aberdeen City"))
        session.commit()
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/school/900105/riverbank-primary-school").text
    assert 'id="nearby-rings-toggle"' in body and "Nextdoor Junior School" in body
    assert '"lat": 53.456' in body and '"miles": 0.41' in body and 'href="/school/900106/nextdoor-junior-school"' in body
    assert "admitted from 0.41 mi" in body
    lonely = client.get("/school/900107/lonely-academy").text
    assert 'id="nearby-rings-toggle"' not in lonely and 'nearby: []' in lonely
