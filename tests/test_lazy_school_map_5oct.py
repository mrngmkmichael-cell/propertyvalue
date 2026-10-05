"""The school page map loads as it nears the screen (5 Oct 2026).

It was about 280 KB of Google Maps on every school page view, though
most readers stop at the verdict above it, and each load counts towards
the Maps allowance. Both branches load on demand: Google in production,
Leaflet in development, so the dev server behaves as production does.
"""
from tests.test_audit_fixes_17sep import _e3_page


def test_google_maps_is_asked_for_by_the_page_only_when_the_map_is_near(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "lazy-test-key")
    body = _e3_page(client)
    assert '<script src="https://maps.googleapis.com/maps/api/js' not in body
    loader = body[body.index("window.whenMapNear = function"):]
    loader = loader[:loader.index("</script>")]
    assert "IntersectionObserver" in loader and "rootMargin: '600px 0px'" in loader
    assert "if (!('IntersectionObserver' in window)) { load(); return; }" in loader
    call = body[body.index("window.whenMapNear(document.getElementById('school-page-map')"):]
    call = call[:call.index("</script>")]
    assert "encodeURIComponent(\"lazy-test-key\")" in call
    assert "&callback=initSchoolPageMap&loading=async" in call
    assert "function initSchoolPageMap()" in body               # the map itself is unchanged


def test_leaflet_is_fetched_the_same_way_in_development(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    body = _e3_page(client)
    assert '<script src="/static/vendor/leaflet/leaflet.js"></script>' not in body
    assert '<link rel="stylesheet" href="/static/vendor/leaflet/leaflet.css">' not in body
    assert "function initSchoolPageLeaflet()" in body
    call = body[body.rindex("window.whenMapNear(document.getElementById('school-page-map')"):]
    call = call[:call.index("</script>")]
    assert "lib.src = '/static/vendor/leaflet/leaflet.js';" in call and "lib.onload = initSchoolPageLeaflet;" in call
    assert "css.href = '/static/vendor/leaflet/leaflet.css';" in call


def test_the_box_keeps_its_size_while_it_waits():
    """Nothing moves when the map arrives: the box has its height before."""
    import pathlib
    css = (pathlib.Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "style.css").read_text(encoding="utf-8")
    block = css[css.index(".school-page-map {"):]
    block = block[:block.index("}")]
    assert "height: 380px;" in block and "background: var(--surface-2);" in block
