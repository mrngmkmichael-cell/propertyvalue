"""People counted when the page itself sees a person (5 Oct 2026).

The people figure on /admin had been estimated from the shape of each
day's page views. From 6 Oct it is the views where a short script on
the page saw someone move, tap, scroll or type, posted to /seen with the
page's path and nothing else.
"""
import datetime
import pathlib

from app import db
from app import main as app_main
from app.models import PageSeen

BROWSER = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"}


def _seen_paths():
    with db.get_session() as session:
        return [p for (p,) in session.query(PageSeen.path).all()]


def _clear():
    with db.get_session() as session:
        session.query(PageSeen).delete()
        session.commit()


def test_a_person_on_a_page_is_recorded_with_its_path_and_nothing_else(client):
    _clear()
    try:
        reply = client.post("/seen", content=b"/school/990731/eden-bank-academy", headers=BROWSER)
        assert reply.status_code == 204
        assert _seen_paths() == ["/school/990731/eden-bank-academy"]
        assert set(PageSeen.__table__.columns.keys()) == {"id", "path", "created_at"}
    finally:
        _clear()


def test_crawlers_our_own_checks_other_sites_and_odd_paths_are_not_people(client):
    _clear()
    try:
        cases = [
            ({"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"}, b"/area/M20"),
            ({**BROWSER, "X-Internal-Check": "1"}, b"/area/M20"),
            ({**BROWSER, "Origin": "https://elsewhere.example"}, b"/area/M20"),
            (BROWSER, b"//elsewhere.example/x"),
            (BROWSER, b"https://elsewhere.example/x"),
            (BROWSER, b"/login"),
            (BROWSER, b"/static/css/style.css"),
            (BROWSER, b"/api/postcode-suggest"),
            (BROWSER, b"/" + b"a" * 300),
            (BROWSER, b""),
        ]
        for headers, body in cases:
            assert client.post("/seen", content=body, headers=headers).status_code == 204
        assert _seen_paths() == []
        # The same browser from this site's own page is counted, query string dropped.
        client.post("/seen", content=b"/area/M20?x=1", headers={**BROWSER, "Origin": "http://testserver"})
        assert _seen_paths() == ["/area/M20"]
    finally:
        _clear()


def test_every_page_carries_the_script_but_errors_and_the_wait_do_not(client):
    home = client.get("/").text
    script = home.split("A person on this page, said by the page itself", 1)[1].split("</script>", 1)[0]
    assert "navigator.sendBeacon('/seen', path)" in script
    for event in ("'pointerdown'", "'pointermove'", "'keydown'", "'touchstart'", "'wheel'", "'focusin'"):
        assert event in script, event
    assert "'load'" not in script and "DOMContentLoaded" not in script   # never on load: renderers run that
    assert "sendBeacon('/seen'" not in client.get("/no-such-page-at-all").text
    assert "sendBeacon('/seen'" not in client.get("/signup").text        # it has its own counter
    for name in ("404.html", "500.html", "503_data.html", "report_building.html", "signup.html", "login.html"):
        source = (pathlib.Path(app_main.__file__).parent / "templates" / name).read_text(encoding="utf-8")
        assert "{% block page_seen %}{% endblock %}" in source, name


def test_the_people_figure_reads_the_confirmed_views_from_the_switch_day(client, monkeypatch):
    _clear()
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        with db.get_session() as session:
            for _ in range(3):
                session.add(PageSeen(path="/area/M20", created_at=now))
            session.commit()
            monkeypatch.setattr(app_main, "PEOPLE_SEEN_FROM", now.date() - datetime.timedelta(days=1))
            after = app_main._admin_metrics(session, now)
            monkeypatch.setattr(app_main, "PEOPLE_SEEN_FROM", now.date() + datetime.timedelta(days=1))
            before = app_main._admin_metrics(session, now)
    finally:
        _clear()
    today = after["daily_pageviews"][-1]
    assert today["confirmed"] is True and today["audience"] == 3
    assert today["crawl"] == max(0, today["count"] - 3)
    assert after["audience_today"] == 3 and after["people_confirmed_today"] is True
    assert after["daily_funnel"][-1]["people"] == 3
    assert after["people_chart"]["end_people"]["v"] == 3
    # Before the switch day the estimate stands, untouched.
    assert before["daily_pageviews"][-1]["confirmed"] is False
    assert before["people_confirmed_today"] is False


def test_the_privacy_policy_says_so(client):
    body = " ".join(client.get("/privacy").text.split())
    assert ("a short script of our own on each page sends that page's path once you move, tap, "
            "scroll or type on it, and nothing else.") in body
