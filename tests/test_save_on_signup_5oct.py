"""Saving a school as part of signing in (5 Oct 2026).

"Sign up to save this school" sent a parent to the sign-up form and back
to the school page, where Save waited to be pressed a second time. Four
schools had been saved by three accounts since the shortlist launched.
The intent now rides along as save_school, through a password sign-up,
a log-in and a Google, Facebook or LinkedIn sign-in alike, and the form
says what it will save before it does.
"""
import re
import uuid
from urllib.parse import parse_qs, urlparse

from app import auth, db, school_shortlist
from app import main as app_main
from tests.test_audit_fixes_17sep import E3_PAGE, _e3_page, _e3_school

SCHOOL = 990731          # Eden Bank Academy, Cumberland, seeded by _e3_page
PARTNER = 990733         # Caldew Vale School, also Cumberland


def _seed():
    _e3_school(SCHOOL, "Eden Bank Academy", 54.89, -2.93, 1.2)
    _e3_school(PARTNER, "Caldew Vale School", 54.884, -2.922, 0.95)


def _email():
    return f"save-{uuid.uuid4().hex[:10]}@customer.test"


def _account(email):
    with db.get_session() as session:
        return auth.find_user_by_email(session, email)


def test_the_sign_up_form_says_which_school_it_will_save_and_carries_it(client):
    _seed()
    body = client.get(f"/signup?next={E3_PAGE}&save_school={SCHOOL}").text
    note = re.sub(r"\s+", " ", re.search(r'<p class="dek signup-for">(.*?)</p>', body, re.S).group(1))
    assert note == ("Signing up also saves <strong>Eden Bank Academy</strong> to your shortlist, "
                    "so you are told when Cumberland republishes its admission distance.")
    assert f'<input type="hidden" name="save_school" value="{SCHOOL}">' in body
    # Already have an account? goes to log in with both the way back and the school.
    login = re.search(r'Already have an account\? <a href="([^"]+)">', body).group(1)
    assert f"save_school={SCHOOL}" in login and "next=/school/990731/eden-bank-academy" in login


def test_signing_up_saves_the_school_and_returns_to_the_page(client):
    _seed()
    email = _email()
    reply = client.post("/signup", data={"email": email, "password": "password123", "next": E3_PAGE,
                                         "save_school": str(SCHOOL)}, follow_redirects=False)
    assert reply.status_code == 303 and reply.headers["location"] == E3_PAGE
    assert school_shortlist.saved_urns(_account(email).id) == {SCHOOL}


def test_a_pair_is_saved_together_and_named_by_its_one_council(client):
    _seed()
    body = client.get(f"/signup?next={E3_PAGE}&save_school={SCHOOL}%2C{PARTNER}").text
    note = re.sub(r"\s+", " ", re.search(r'<p class="dek signup-for">(.*?)</p>', body, re.S).group(1))
    assert note == ("Signing up also saves <strong>Eden Bank Academy</strong> and <strong>Caldew Vale School</strong> "
                    "to your shortlist, so you are told when Cumberland republishes their admission distances.")
    email = _email()
    client.post("/signup", data={"email": email, "password": "password123", "next": E3_PAGE,
                                 "save_school": f"{SCHOOL},{PARTNER}"})
    assert school_shortlist.saved_urns(_account(email).id) == {SCHOOL, PARTNER}


def test_anything_that_is_not_a_known_school_is_ignored_not_refused(client):
    _seed()
    for junk in ("abc", "99999999999", "424242", "-5", f"{SCHOOL},{SCHOOL},{PARTNER}"):
        email = _email()
        reply = client.post("/signup", data={"email": email, "password": "password123", "next": "/",
                                             "save_school": junk}, follow_redirects=False)
        assert reply.status_code == 303, junk
        saved = school_shortlist.saved_urns(_account(email).id)
        # A repeated URN counts once, and the cap is two entries read.
        assert saved == ({SCHOOL} if junk.startswith(str(SCHOOL)) else set()), (junk, saved)
    assert 'name="save_school"' not in client.get("/signup?save_school=abc").text


def test_logging_in_saves_it_too_and_keeps_a_note_already_written(client):
    _seed()
    email = _email()
    client.post("/signup", data={"email": email, "password": "password123", "next": "/"})
    user_id = _account(email).id
    school_shortlist.save_item(user_id, SCHOOL, "Visit on Saturday")
    client.post("/logout")
    body = client.get(f"/login?next={E3_PAGE}&save_school={SCHOOL}%2C{PARTNER}").text
    assert "Logging in also saves <strong>Eden Bank Academy</strong>" in body
    assert f'<input type="hidden" name="save_school" value="{SCHOOL},{PARTNER}">' in body
    reply = client.post("/login", data={"email": email, "password": "password123", "next": E3_PAGE,
                                        "save_school": f"{SCHOOL},{PARTNER}"}, follow_redirects=False)
    assert reply.status_code == 303
    assert school_shortlist.saved_urns(user_id) == {SCHOOL, PARTNER}
    note = next(i["note"] for i in school_shortlist.list_items(user_id) if i["urn"] == SCHOOL)
    assert note == "Visit on Saturday"


def test_a_provider_sign_in_carries_the_school_through_the_round_trip(client, monkeypatch):
    _seed()
    monkeypatch.setenv("LINKEDIN_OAUTH_CLIENT_ID", "li-id")
    monkeypatch.setenv("LINKEDIN_OAUTH_CLIENT_SECRET", "li-secret")
    email = _email()

    async def _verified(provider, code, redirect_uri):
        return email

    monkeypatch.setattr(app_main.oauth_providers, "fetch_verified_email", _verified)
    login = client.get(f"/login?next={E3_PAGE}&save_school={SCHOOL}").text
    start = re.search(r'href="(/auth/linkedin\?[^"]+)"', login).group(1).replace("&amp;", "&")
    assert f"save_school={SCHOOL}" in start
    out = client.get(start, follow_redirects=False)
    state = parse_qs(urlparse(out.headers["location"]).query)["state"][0]
    back = client.get(f"/auth/linkedin/callback?code=abc&state={state}", follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"] == E3_PAGE
    assert school_shortlist.saved_urns(_account(email).id) == {SCHOOL}


def test_the_school_page_asks_to_sign_up_with_the_school_in_the_link(client):
    body = _e3_page(client, "?check=CA3+9AA")
    link = re.search(r'<a href="(/signup\?next=/school/990731[^"]+)">Sign up</a>\s*to save this school', body)
    assert link and f"save_school={SCHOOL}" in link.group(1)


def test_a_signed_out_save_goes_to_log_in_carrying_the_school(client):
    reply = client.post("/schools/shortlist/save", data={"urn": str(SCHOOL), "next": E3_PAGE, "also": str(PARTNER)},
                        follow_redirects=False)
    assert reply.status_code == 303
    location = reply.headers["location"]
    assert location.startswith("/login?next=") and f"save_school={SCHOOL}%2C{PARTNER}" in location
