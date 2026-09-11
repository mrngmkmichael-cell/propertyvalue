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


def test_the_map_labels_the_districts_inside_the_distance(client):
    """Step 2 of the catchment map: the districts whose centre falls
    inside the distance reach the map payload with their centres, and a
    school whose distance covers no district centre gets an empty list."""
    from app import db, main as app_main
    from app.models import School, SchoolAdmissionRadius, SchoolDetail
    from app.services import _cache
    with db.get_session() as session:
        session.merge(School(urn=900108, name="City Centre Academy", phase="Secondary", type_name="Academy", postcode="M1 1AE",
                             latitude=53.478, longitude=-2.24))
        session.merge(SchoolDetail(urn=900108, town="Manchester", local_authority="Manchester"))
        session.merge(SchoolAdmissionRadius(urn=900108, last_distance_miles=1.5, academic_year="2025/26", source_authority="Manchester"))
        session.commit()
    _cache._store.clear(); _cache._bytes = 0
    expected = app_main._outcodes_within(53.478, -2.24, 1.5)
    assert expected and "lat" in expected[0] and "lon" in expected[0]
    body = client.get("/school/900108/city-centre-academy").text
    assert "districts: [" in body and f'"code": "{expected[0]["outcode"]}"' in body
    assert "The labels are postcode districts whose centre falls inside it" in body
    lonely = client.get("/school/900107/lonely-academy").text
    assert "districts: []" in lonely and "The labels are postcode districts" not in lonely


def test_the_distance_exists_as_a_real_image(client):
    """Step 3 of the catchment map: a school with a published distance
    has a PNG of the ring, drawn to scale, which is also the page's share
    image; an unknown school has neither."""
    from app import db
    from app.models import School, SchoolAdmissionRadius, SchoolDetail
    from app.services import _cache, og_image
    with db.get_session() as session:
        session.merge(School(urn=900105, name="Riverbank Primary School", phase="Primary", type_name="Community school", postcode="M14 5TG",
                             latitude=53.4501, longitude=-2.2201))
        session.merge(SchoolDetail(urn=900105, town="Manchester", local_authority="Manchester"))
        session.merge(SchoolAdmissionRadius(urn=900105, last_distance_miles=0.62, academic_year="2025/26", source_authority="Manchester"))
        session.commit()
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/school/900105/riverbank-primary-school").text
    assert 'content="https://testserver/school/900105/catchment.png"' in body
    assert "catchment area map: the 0.62 mile admission distance, 2025/26" in body
    assert 'download="riverbank-primary-school-admission-distance.png"' in body
    r = client.get("/school/900105/catchment.png", follow_redirects=False)
    if og_image.is_available():
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(r.content) > 5000
    else:
        assert r.status_code == 302
    assert client.get("/school/900105/catchment.png", follow_redirects=False).status_code in (200, 302)
    # School pages exist only for schools with a published distance (the
    # profile query joins the distance table), so the only "no picture"
    # case is a school that is not on the register at all.
    assert client.get("/school/900199/catchment.png", follow_redirects=False).status_code in (404, 302)


def test_a_distance_beyond_a_school_run_is_not_shown_as_a_catchment(client):
    """91 councils publish a last-admitted distance that is not a
    catchment: Brent's 621.37 miles is exactly 1000 km, and the widest is
    868.30. The page used to print it as a figure, put it in the title
    and draw it as a circle. It now says what the figure implies, and the
    picture that would draw the ring to scale answers 404."""
    from app import db, main as app_main
    from app.models import School, SchoolAdmissionRadius, SchoolDetail
    from app.services import _cache
    with db.get_session() as session:
        session.merge(School(urn=900110, name="No Limit High School", phase="Secondary", type_name="Academy",
                             postcode="M14 5TG", latitude=53.4501, longitude=-2.2201))
        session.merge(SchoolDetail(urn=900110, town="Manchester", local_authority="Manchester"))
        session.merge(SchoolAdmissionRadius(urn=900110, last_distance_miles=868.3, academic_year="2025/26",
                                            source_authority="Cheshire West and Chester"))
        session.commit()
    _cache._store.clear(); _cache._bytes = 0

    body = client.get("/school/900110/no-limit-high-school").text
    title = body.split("<title>")[1].split("</title>")[0]
    assert "868" not in title and "distance did not limit entry" in title
    og = body.split('property="og:title" content="')[1].split('"')[0]
    assert "868" not in og and "distance did not limit entry" in og
    assert "No limit" in body and "868.3 miles away, which is further than any school run" in body
    assert "noLimit: true" in body                         # the map draws no ring
    assert "No circle to draw" in body
    assert "Postcode districts within" not in body         # meaningless without a limit
    assert 'content="https://testserver/og/school/900110.png"' in body   # the plain card, not the ring picture
    assert client.get("/school/900110/catchment.png", follow_redirects=False).status_code in (404, 302)

    badge = client.get("/school/900110/badge.svg")
    assert badge.status_code == 200 and "Distance did not limit entry" in badge.text and "868" not in badge.text

    checked = client.get("/school/900110/no-limit-high-school?check=M14+5TG").text
    assert "Very likely" in checked and "Distance did not limit entry" in checked
    assert "comfortably inside" not in checked

    # A real distance is untouched: its own figure, title and share title
    # still quote it. ("No limit" does appear on that page now, in the
    # nearby-schools table, which is the row for the school above.)
    ordinary = client.get("/school/900101/riverside-primary-school").text
    assert "0.62 miles" in ordinary
    assert "0.62 miles" in ordinary.split("<title>")[1].split("</title>")[0]
    assert '<span class="score-tile-value">0.62 mi</span>' in ordinary


def test_the_threshold_is_stated_once(client):
    from app import main as app_main
    assert app_main.NO_DISTANCE_LIMIT_MILES == 20
    assert app_main._school_labels({"miles": 20.5, "academic_year": "2025/26"})["no_distance_limit"] is True
    assert app_main._school_labels({"miles": 19.5, "academic_year": "2025/26"})["no_distance_limit"] is False
    assert app_main._school_labels({"miles": None, "academic_year": ""})["no_distance_limit"] is False
