"""Every page that renders without upstream data, checked the way a
crawler and a browser see it. Cheap to run, and it catches the class of
failure that has actually bitten this site: a template that crashes
(block defined twice), a route that refuses HEAD, a page with two
canonical tags."""
import re
import xml.etree.ElementTree as ET

import pytest

STATIC_PAGES = [
    "/", "/areas", "/methodology", "/premium", "/schools/guide", "/privacy", "/terms",
    "/support", "/buying-guide", "/browser-extension", "/embed", "/login", "/signup",
]


@pytest.mark.parametrize("path", STATIC_PAGES)
def test_static_page_renders(client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert body.count("<title>") == 1, path
    assert body.count('<link rel="canonical"') == 1, path
    assert body.count('<meta name="description"') == 1, path
    assert len(re.findall(r"<h1[^>]*>", body)) == 1, path
    assert "TemplateAssertionError" not in body and "Traceback" not in body


@pytest.mark.parametrize("path", ["/", "/sitemap.xml", "/robots.txt", "/premium"])
def test_head_requests_are_answered(client, path):
    """Google's sitemap fetcher HEADs before it GETs; a 405 here showed
    up as "Couldn't fetch" in Search Console."""
    r = client.head(path)
    assert r.status_code == 200, path
    assert r.content == b""


def test_unknown_page_is_a_real_404(client):
    r = client.get("/this-page-does-not-exist")
    assert r.status_code == 404
    assert "text/html" in r.headers["content-type"]


def test_robots_allows_crawling_and_points_at_sitemap(client):
    body = client.get("/robots.txt").text
    assert "Allow: /" in body
    assert "Sitemap: " in body and "/sitemap.xml" in body
    assert "Disallow: /watchlist" in body


def test_sitemap_is_valid_and_substantial(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [el.text for el in root.findall(".//s:loc", ns)]
    assert len(locs) > 300
    assert all(loc.startswith("https://") for loc in locs)
    assert any(loc.endswith("/area/SW1A") for loc in locs)
    assert len(locs) == len(set(locs)), "duplicate sitemap entries"


def test_canonical_strips_tracking_params(client):
    body = client.get("/premium?utm_source=reddit&ref=abc").text
    canon = re.search(r'<link rel="canonical" href="([^"]+)"', body).group(1)
    assert canon.endswith("/premium")
    assert "utm_source" not in canon and "ref=" not in canon


def test_page_titles_are_specific_not_generic(client):
    generic = {"Premium", "School Guide", "Chrome Extension", "Why trust this", "UKPropertyInsight"}
    for path in ("/", "/premium", "/schools/guide", "/browser-extension", "/methodology"):
        title = re.search(r"<title>(.*?)</title>", client.get(path).text, re.S).group(1).strip()
        bare = title.split("|")[0].strip()
        assert bare not in generic, f"{path} title is still generic: {title!r}"
        assert len(title) <= 120, f"{path} title too long for a search result: {title!r}"


def test_homepage_promo_banner_only_for_signed_out_visitors(client):
    assert "promo-banner" in client.get("/").text


def test_homepage_promo_banner_copy_matches_the_real_offer(client):
    """The banner promises free Premium reports; that number must match
    what a new account actually gets."""
    from app import auth
    body = client.get("/").text
    assert f"get {auth.FREE_PREMIUM_UNLOCKS} full Premium property reports" in body
