"""One public address (5 Oct 2026).

The site answered on its Render hostname with a 200 and a canonical
naming that hostname, a full second copy for Google to choose between,
and the page cache, keyed on path alone, carried that canonical onto the
real domain. These tests hold the fix: production always names the real
domain, the Render hostname redirects, and the three things a redirect
could break (Stripe's webhook, health checks, internal jobs) are left
alone.
"""
import pytest

from app import main as app_main

REAL = "https://ukpropertyinsight.co.uk"
RENDER = "propertyvalue-eg3v.onrender.com"


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(app_main, "IS_PRODUCTION", True)
    monkeypatch.setattr(app_main, "CANONICAL_ORIGIN", REAL)
    monkeypatch.setattr(app_main, "_CANONICAL_HOST", "ukpropertyinsight.co.uk")
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    yield
    _cache._store.clear(); _cache._bytes = 0


def test_a_page_on_the_render_hostname_redirects_to_the_real_domain(client, production):
    reply = client.get("/schools/entrance-tests?x=1", headers={"host": RENDER}, follow_redirects=False)
    assert reply.status_code == 301
    assert reply.headers["location"] == f"{REAL}/schools/entrance-tests?x=1"


def test_a_post_is_never_redirected_because_stripe_would_not_follow_it(client, production):
    reply = client.post("/webhooks/stripe", headers={"host": RENDER}, content=b"{}",
                        follow_redirects=False)
    assert reply.status_code != 301


def test_health_checks_and_internal_jobs_answer_on_any_host(client, production):
    for path in ("/healthz",):
        reply = client.get(path, headers={"host": RENDER}, follow_redirects=False)
        assert reply.status_code != 301, path


def test_an_internal_address_is_not_redirected(client, production):
    """Render's own health check reaches the app by an internal address;
    a redirect there could look like a failing service."""
    reply = client.get("/schools/entrance-tests", headers={"host": "10.0.0.7:10000"},
                       follow_redirects=False)
    assert reply.status_code == 200


def test_the_canonical_names_the_real_domain_whatever_the_host(client, production):
    reply = client.get("/schools/entrance-tests", headers={"host": "10.0.0.7:10000"})
    assert f'<link rel="canonical" href="{REAL}/schools/entrance-tests"' in reply.text


def test_a_page_rendered_for_another_host_never_reaches_the_cache(client, production):
    """The poisoning itself: render via a foreign host, then ask on the
    real domain, and the real domain must not be served that copy."""
    client.get("/schools/entrance-tests", headers={"host": "10.0.0.7:10000"})
    reply = client.get("/schools/entrance-tests", headers={"host": "ukpropertyinsight.co.uk"})
    assert reply.headers.get("x-anon-cache") != "hit", "the foreign render was cached"
    assert RENDER not in reply.text and "10.0.0.7" not in reply.text


def test_off_production_nothing_changes(client, monkeypatch):
    monkeypatch.setattr(app_main, "IS_PRODUCTION", False)
    monkeypatch.setattr(app_main, "CANONICAL_ORIGIN", "")
    monkeypatch.setattr(app_main, "_CANONICAL_HOST", "")
    reply = client.get("/schools/entrance-tests", headers={"host": RENDER}, follow_redirects=False)
    assert reply.status_code == 200
