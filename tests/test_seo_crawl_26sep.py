"""Crawl budget, 26 Sep 2026.

Search Console that day: 3,104 pages "Discovered, currently not indexed",
1,509 URLs returning 404 (809 of them /property), and one sitemap of
6,017 URLs on which every entry claimed to have changed today. The three
school families are what earns clicks (school pages 82 clicks from 4,046
impressions at an average position of 9.5, the per-district guides 8 from
763 at 7.7, the council hubs 17 from 386 at 7.7), so the crawl is pointed
at them: report URLs and the admissions search are closed to crawlers,
the 151 per-council independent pages leave the sitemap, and the sitemap
becomes an index of one child per family, with a lastmod only where the
date is real.
"""
import xml.etree.ElementTree as ET

from app import main as app_main

NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
# The base the test client answers on, which is what the sitemap writes.
BASE = "http://testserver"


def _index_children(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    assert root.tag.endswith("sitemapindex"), "the sitemap is now an index of children"
    children = [el.text for el in root.findall(".//s:loc", NS)]
    base = children[0].rsplit("/sitemap-", 1)[0] if children else BASE
    return root, children, base


def test_robots_closes_the_report_and_the_search_box_to_crawlers(client):
    body = client.get("/robots.txt").text
    assert "Disallow: /property\n" in body
    assert "Disallow: /schools/admissions/search\n" in body
    # What earns the clicks stays open, and the index is still advertised.
    assert "Allow: /\n" in body
    assert "Disallow: /school/" not in body and "Disallow: /area" not in body
    assert f"Sitemap: " in body and "/sitemap.xml" in body


def test_the_sitemap_is_an_index_and_every_child_is_a_valid_urlset(client):
    _, children, base = _index_children(client)
    assert children, "the index lists at least one child"
    assert all(c.startswith(f"{base}/sitemap-") and c.endswith(".xml") for c in children), children
    seen = []
    for child in children:
        r = client.get(child.replace(base, ""))
        assert r.status_code == 200, child
        root = ET.fromstring(r.content)
        assert root.tag.endswith("urlset"), child
        locs = [el.text for el in root.findall(".//s:loc", NS)]
        assert locs, f"{child} is listed but empty"
        assert all(loc.startswith("https://") for loc in locs), child
        seen += locs
    assert len(seen) == len(set(seen)), "a URL appears in two children"
    # The children together are exactly what the one file used to be,
    # which is also what the IndexNow pinger submits.
    assert sorted(seen) == sorted(u for u, _ in app_main._sitemap_entries(base))


def test_each_url_lands_in_the_family_it_belongs_to(client):
    group = app_main._sitemap_group
    assert group(f"{BASE}/school/102156/fortismere-school") == "schools"
    assert group(f"{BASE}/schools/guide?q=N10") == "school-guides"
    assert group(f"{BASE}/schools/admissions/haringey") == "admissions"
    assert group(f"{BASE}/schools/independent") == "admissions"
    assert group(f"{BASE}/area/M20") == "areas"
    assert group(f"{BASE}/running-costs/council-tax/ashford") == "costs"
    assert group(f"{BASE}/estate-charges") == "costs"
    assert group(f"{BASE}/compare/M14/vs/M20") == "comparisons"
    assert group(f"{BASE}/market/house-prices/london") == "comparisons"
    assert group(f"{BASE}/") == "pages"
    assert group(f"{BASE}/premium") == "pages"
    # Every child the index can offer is a name the route answers to.
    for child in _index_children(client)[1]:
        assert child.rsplit("/sitemap-", 1)[1][:-4] in app_main.SITEMAP_GROUPS


def test_a_name_that_is_not_a_family_is_a_404(client):
    assert client.get("/sitemap-nonsense.xml").status_code == 404


def test_lastmod_is_only_where_the_date_is_real(client):
    """Every URL used to carry the deploy's date. Now only the family
    whose import date we hold carries one."""
    assert set(app_main.SITEMAP_LASTMOD) <= set(app_main.SITEMAP_GROUPS)
    index, children, base = _index_children(client)
    for el in index.findall("s:sitemap", NS):
        name = el.find("s:loc", NS).text.rsplit("/sitemap-", 1)[1][:-4]
        has = el.find("s:lastmod", NS) is not None
        assert has == (name in app_main.SITEMAP_LASTMOD), name
    for child in children:
        name = child.rsplit("/sitemap-", 1)[1][:-4]
        root = ET.fromstring(client.get(child.replace(base, "")).content)
        dates = {el.text for el in root.findall(".//s:lastmod", NS)}
        assert dates == ({app_main.SITEMAP_LASTMOD[name]} if name in app_main.SITEMAP_LASTMOD else set()), name


def test_the_per_council_independent_pages_are_no_longer_advertised(client):
    urls = [u for u, _ in app_main._sitemap_entries(BASE)]
    assert f"{BASE}/schools/independent" in urls, "the index itself stays"
    assert not [u for u in urls if u.startswith(f"{BASE}/schools/independent/")]
    # They are still pages, still linked, just not queue-jumped.
    assert client.get("/schools/independent").status_code == 200


def test_the_near_miss_list_is_twenty_distinct_schools():
    urns = app_main.NEAR_MISS_SCHOOL_URNS
    assert len(urns) == 20 and len(set(urns)) == 20
    assert all(isinstance(u, int) for u in urns)
