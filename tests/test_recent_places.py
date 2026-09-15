"""Pick up where you left off, under every box that asks for an address
or a school (15 Sep 2026; the homepage had it alone since 26 Aug).

The lists are filled in the browser from this device's own localStorage,
so these pin the server's side of it: the empty list and its one script
on each page that offers one, and the writer on each page that records."""
import re

from tests.test_pages import _seed_admission_school


def _target(body):
    found = re.search(r'var TARGET = "([^"]*)";', body)
    return found.group(1) if found else None


def test_the_address_list_sits_under_every_address_box(client):
    for path, target in (("/", "/property"), ("/areas", "/property"), ("/running-costs", "/running-costs")):
        body = client.get(path).text
        assert body.count('id="lx-recent"') == 1, path
        assert "Pick up where you left off" in body and "Kept on this device only." in body, path
        # One script reads the list. The homepage had its own copy until
        # the lists became one implementation; two would draw every chip twice.
        assert body.count("localStorage.getItem('uki-recent')") == 1, path
        assert _target(body) == target, path


def test_the_school_list_sits_under_both_school_searches(client):
    for path in ("/schools/guide", "/schools/admissions"):
        body = client.get(path).text
        assert body.count('id="school-recent"') == 1, path
        assert body.count("localStorage.getItem('uki-recent-schools')") == 1, path


def test_a_school_page_remembers_itself_on_this_device(client):
    _seed_admission_school()
    body = client.get("/school/990002/riverside-academy").text
    assert "var KEY = 'uki-recent-schools';" in body
    assert "urn: 990002," in body


def test_a_report_still_remembers_its_address_with_its_score(client, fake_report):
    fake_report()
    body = client.get("/property?postcode=M14%205TG").text
    assert "var KEY = 'uki-recent';" in body
    assert 'pc: "M14 5TG",' in body
    assert re.search(r"score: (\d+|null),", body)
