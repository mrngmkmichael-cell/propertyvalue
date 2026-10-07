"""How far down people read (7 Oct 2026).

Michael asked, with the answer-first rewrite approved, for a way to tell
whether shorter pages are read further. Once the page has seen a person
(the /seen signal), it posts each quarter of its main content the
reader passes to /depth, as the step and the path; /admin shows, by kind
of page, the share of people who reached each quarter.
"""
import datetime

from app import db
from app import main as app_main
from app.models import PageDepth, PageSeen

BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"}


def _clear():
    with db.get_session() as session:
        session.query(PageDepth).delete()
        session.query(PageSeen).delete()
        session.commit()


def _depths():
    with db.get_session() as session:
        return sorted((p, d) for p, d in session.query(PageDepth.path, PageDepth.depth).all())


def test_a_step_is_recorded_with_its_path_and_nothing_else(client):
    _clear()
    try:
        assert client.post("/depth", content=b"50 /school/151031/marple-hall-school", headers=BROWSER).status_code == 204
        assert _depths() == [("/school/151031/marple-hall-school", 50)]
        assert set(PageDepth.__table__.columns.keys()) == {"id", "path", "depth", "created_at"}
    finally:
        _clear()


def test_only_the_four_steps_from_a_person_on_this_site_count(client):
    _clear()
    try:
        for headers, body in [
            (BROWSER, b"33 /area/M20"), (BROWSER, b"0 /area/M20"), (BROWSER, b"150 /area/M20"),
            (BROWSER, b"abc /area/M20"), (BROWSER, b"50"), (BROWSER, b"50 //elsewhere.example"),
            (BROWSER, b"50 /login"),
            ({"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}, b"50 /area/M20"),
            ({**BROWSER, "X-Internal-Check": "1"}, b"50 /area/M20"),
            ({**BROWSER, "Origin": "https://elsewhere.example"}, b"50 /area/M20"),
        ]:
            assert client.post("/depth", content=body, headers=headers).status_code == 204
        assert _depths() == []
        for step in (25, 50, 75, 100):
            client.post("/depth", content=f"{step} /area/M20?x=1".encode(), headers=BROWSER)
        assert _depths() == [("/area/M20", 25), ("/area/M20", 50), ("/area/M20", 75), ("/area/M20", 100)]
    finally:
        _clear()


def test_the_page_sends_steps_only_after_it_has_seen_a_person(client):
    home = client.get("/").text
    script = home.split("A person on this page, said by the page itself", 1)[1].split("</script>", 1)[0]
    assert "var steps = [25, 50, 75, 100]" in script
    assert "post('/depth', step + ' ' + window.location.pathname)" in script
    # Scrolling is listened to from inside send(), so a client that never
    # touched the page never reports a depth.
    send = script[script.index("function send()"):]
    assert "window.addEventListener('scroll', onScroll" in send
    assert script.index("function send()") > script.index("function measure()")
    assert "window.addEventListener('scroll'" not in script[:script.index("function send()")]


def test_admin_shows_the_share_of_people_reaching_each_quarter(client):
    _clear()
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        with db.get_session() as session:
            for n in range(5):
                session.add(PageSeen(path=f"/school/{n}/x", created_at=now))
            for step, count in ((25, 4), (50, 3), (75, 2), (100, 1)):
                for n in range(count):
                    session.add(PageDepth(path=f"/school/{n}/x", depth=step, created_at=now))
            for n in range(3):                       # too few people to show
                session.add(PageSeen(path=f"/area/A{n}", created_at=now))
            session.commit()
            rows = app_main._admin_metrics(session, now)["reading_depth"]
    finally:
        _clear()
    school = next(r for r in rows if r["family"] == "/school/…")
    assert school["label"] == "School pages" and school["people"] == 5
    assert school["steps"] == [80, 60, 40, 20]
    assert not any(r["family"] == "/area/…" for r in rows)


def test_the_privacy_policy_mentions_how_far_you_read(client):
    body = " ".join(client.get("/privacy").text.split())
    assert "and how far down the page you read, and nothing else." in body
