"""The Medium findings of the 16 Sep 2026 first-time visitor audit
(docs/audits/2026-09-16-first-visitor-audit.md): card lines a first-timer
can read, a real question in the buyer-questions teaser, the sign-up page
naming the property it will unlock, the grade note beside Ofsted badges,
and the school page pointing on to the house report."""
import re

from tests.test_pages import _seed_admission_school
from tests.test_property_page import _report


def test_card_lines_say_which_way_the_scale_runs(client, fake_report):
    body = _report(client, fake_report)
    # Deprivation: decile 3 in the fake, and the card says what a decile is.
    assert "Decile 3 of 10" in body
    assert "1 is the most deprived tenth of England, 10 the least" in body
    # Noise: 47 dB(A) in the fake is the Low band, said in words from the popup's scale.
    assert "dB(A) at its loudest: a quiet residential street" in body


def test_the_buyer_questions_teaser_shows_a_question_not_a_heading(client, fake_report):
    # 18 Sep 2026 (first-visitor audit of 17 Sep, item D4): the one-sample
    # teaser inside .bq-locked gave way to every question a free card
    # raised, in full, for every reader, so the section is read whole.
    body = _report(client, fake_report)
    teaser = body.split('id="buyer-questions"', 1)[1].split("</section>", 1)[0]
    assert "were generated for this property." in teaser
    assert 'class="bq-locked-list"' not in teaser  # the bare list of triggers is gone
    questions = re.findall(r'<p class="bq-question">(.*?)</p>', teaser, re.S)
    assert questions and all(len(q.strip()) > 20 for q in questions)


def test_the_sign_up_page_names_the_property_it_will_unlock(client):
    body = client.get("/signup?next=/property%3Fpostcode%3DKT3%204HX%26house_number%3D36").text
    assert "Your free full report will be <strong>36, KT3 4HX</strong>" in body
    body = client.get("/signup?next=/property%3Fpostcode%3Dm14%205tg").text
    assert "Your free full report will be <strong>M14 5TG</strong>" in body
    # Anything that is not a report names nothing, and nothing is echoed back.
    for next_value in ("/", "/premium", "/schools/admissions/manchester", "javascript:alert(1)",
                       "/property%3Fpostcode%3D%3Cscript%3Ealert(1)%3C/script%3E"):
        body = client.get(f"/signup?next={next_value}").text
        assert "Your free full report will be" not in body, next_value
        assert "<script>alert" not in body


def test_the_schools_guide_key_explains_report_card_and_no_current_grade(client):
    # The key is only drawn for an area with schools; the bare class name
    # also sits in the inlined stylesheet, so look for the markup.
    body = client.get("/schools/guide?q=M1").text
    if 'class="school-guide-map-key"' in body:
        assert "Report card: inspected since Ofsted stopped one-word grades in late 2024" in body
        assert "No current grade: Ofsted's register holds no graded inspection" in body
    # Whatever the test data holds, the note is in the template beside the key.
    from pathlib import Path
    template = (Path(__file__).resolve().parent.parent / "app" / "templates" / "schools_guide.html").read_text(encoding="utf-8")
    key_at = template.index('class="school-guide-map-key"')
    assert "Report card: inspected since Ofsted stopped one-word grades" in template[key_at:key_at + 2000]


def test_a_school_page_points_on_to_the_house_report(client, monkeypatch):
    from app import main as app_main

    _seed_admission_school()
    landing = client.get("/school/990002/riverside-academy").text
    assert "After the answer, the full report on that postcode is one tap away" in landing

    async def _lookup(_pc):
        return {"postcode": "M1 2AA", "latitude": 53.4945, "longitude": -2.24}

    monkeypatch.setattr(app_main, "lookup_postcode", _lookup)
    answered = client.get("/school/990002/riverside-academy?check=M1+2AA").text
    assert "After the answer" not in answered  # the landing box gives way to the answer
    assert "Run the full report on M1 2AA" in answered
