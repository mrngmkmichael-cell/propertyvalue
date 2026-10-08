"""A slow moment must not take a comparison out of search (5 Oct 2026).

Each side of a district comparison asks for its sales with an
eight-second cap. A side that timed out was cached anyway, without its
median, for the life of the cache entry, and a missing median makes the
page noindex. The site audit found BH1 against BH4 listed in the
sitemap and noindexed, though both districts have medians on their own
area guides.
"""
from app import main as app_main
import pytest

from tests.conftest import fake_location


@pytest.fixture(autouse=True)
def _comparisons_offered(monkeypatch):
    """These pin how a comparison is judged when the pages are offered to
    search; since 8 Oct 2026 they are not (VERSUS_OFFERED_TO_SEARCH), and
    test_brainstorm_8oct holds that."""
    from app import main as app_main
    monkeypatch.setattr(app_main, "VERSUS_OFFERED_TO_SEARCH", True)


def _run(client, monkeypatch, sales_result, guide_medians):
    left, right = sorted(["M20", app_main._neighbour_outcodes("M20")[0]])
    stored = []

    async def _resolve(outcode):
        return fake_location(postcode=f"{outcode} 1AA", outcode=outcode), True

    async def _summary(postcode, house_number):
        return {"flood_zone": "Zone 1 (low probability)"}

    async def _sales(lat, lon):
        if isinstance(sales_result, Exception):
            raise sales_result
        return sales_result

    monkeypatch.setattr(app_main, "_resolve_extension_location", _resolve)
    monkeypatch.setattr(app_main, "_comparison_summary", _summary)
    monkeypatch.setattr(app_main, "_outcode_sales", _sales)
    monkeypatch.setattr(app_main, "_district_price_rows_by_outcode",
                        lambda: {oc: {"median": guide_medians, "count": 30}
                                 for oc in (left, right)} if guide_medians else {})
    real_get = app_main._cache.get_persistent
    monkeypatch.setattr(app_main._cache, "get_persistent",
                        lambda key, ttl: None if isinstance(key, tuple) and key and key[0] == "area_vs"
                        else real_get(key, ttl))
    monkeypatch.setattr(app_main._cache, "set_persistent",
                        lambda key, value: stored.append(key) if key and key[0] == "area_vs" else None)
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get(f"/compare/{left}/vs/{right}").text
    return body, stored


def test_a_timed_out_lookup_is_not_kept(client, monkeypatch):
    import asyncio
    body, stored = _run(client, monkeypatch, asyncio.TimeoutError(), guide_medians=None)
    assert stored == [], "a comparison missing a side because of a timeout was cached"


def test_a_complete_answer_is_kept_as_before(client, monkeypatch):
    sales = {"enough_for_median": True, "median": 320000, "count": 41}
    body, stored = _run(client, monkeypatch, sales, guide_medians=None)
    assert len(stored) == 1
    assert "noindex" not in body.lower()


def test_a_missing_median_takes_the_district_figure_from_the_area_guides(client, monkeypatch):
    """The same sales query the guide publishes, so the comparison is
    indexable and shows a price instead of a gap."""
    import asyncio
    body, stored = _run(client, monkeypatch, asyncio.TimeoutError(), guide_medians=305000)
    assert "noindex" not in body.lower()
    assert "305,000" in body


def test_without_any_median_the_page_stays_out_of_the_index(client, monkeypatch):
    body, stored = _run(client, monkeypatch, {"enough_for_median": False}, guide_medians=None)
    assert '<meta name="robots" content="noindex, follow">' in body
