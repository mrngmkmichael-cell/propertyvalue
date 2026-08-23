"""The property report, rendered from faked upstream results (see
conftest.fake_gather). These are the behaviours that have been added
or fixed by hand and had no check until now."""
import re

from tests.conftest import fake_gather, fake_location


def _report(client, fake_report, **kw):
    fake_report(**kw)
    r = client.get("/property?postcode=M14%205TG")
    assert r.status_code == 200
    return r.text


def test_report_renders_end_to_end(client, fake_report):
    body = _report(client, fake_report)
    assert "Traceback" not in body and "TemplateAssertionError" not in body
    assert body.count('<link rel="canonical"') == 1
    assert 'content="https://' in body or 'href="http://testserver/property?postcode=M14%205TG"' in body


def test_invalid_postcode_is_a_404(client, monkeypatch):
    from app import main as app_main

    async def _none(_pc):
        return None

    monkeypatch.setattr(app_main, "lookup_postcode", _none)
    r = client.get("/property?postcode=ZZ99%209ZZ")
    assert r.status_code == 404
    assert "couldn't find that postcode" in r.text


def test_highlights_strip_shows_real_unlocked_facts(client, fake_report):
    body = _report(client, fake_report)
    assert "What stands out" in body
    values = re.findall(r'<span class="highlight-value">(.*?)</span>', body)
    assert 2 <= len(values) <= 4
    assert "79%" in values          # schools
    assert "+4.1%" in values        # area prices
    assert "Lower" in values        # crime comparison: 2 lower vs 1 higher


def test_highlights_never_leak_locked_values(client, fake_report):
    """Valuation and price trend are Premium-only. Even when present in
    the gather, they must not surface in the free highlights strip."""
    body = _report(client, fake_report, gather=fake_gather(
        valuation={"estimate": 999999, "low": 900000, "high": 1100000},
    ))
    strip = body.split("What stands out", 1)[1].split("Tap a card", 1)[0]
    assert "999,999" not in strip
    # ...while the locked card itself is still on the page (tagged, not shown)
    assert "dashboard-card-locked" in body


def test_locked_cards_are_tagged_not_blurred(client, fake_report):
    body = _report(client, fake_report)
    assert "dashboard-card-locked" in body
    assert "Sign up: 3 free full reports" in body
    assert "blur(5px)" not in body  # the old locked-card treatment


def test_keep_exploring_links_and_figures(client, fake_report):
    body = _report(client, fake_report)
    assert 'href="/area/M14"' in body
    assert 'href="/property/comparables?postcode=M14%205TG"' in body
    assert 'href="/schools/guide?q=M14"' in body
    assert "<strong>40</strong> sales within a short walk" in body
    assert "<strong>12</strong> schools nearby" in body
    assert "<strong>+4.1%</strong> prices this year in Manchester" in body


def test_epc_hero_uses_certificate_detail(client, fake_report):
    body = _report(client, fake_report)
    assert 'class="epc-hero"' in body
    assert "Potential rating" in body
    assert "£1,099" in body and "£216" in body  # heating, hot water costs


def test_scotland_gets_the_data_gap_notice(client, fake_report):
    body = _report(client, fake_report, location=fake_location(country="Scotland", postcode="EH1 1BB", outcode="EH1"))
    assert 'id="modal-scotland-notice"' in body
    assert "British Transport Police" in body
    assert "getElementById('modal-scotland-notice')" in body and "showModal()" in body


def test_england_does_not_get_the_scotland_notice(client, fake_report):
    body = _report(client, fake_report)
    assert "modal-scotland-notice" not in body
