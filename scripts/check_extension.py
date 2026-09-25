"""Run an unpacked build of the browser extension against the live site or
the dev server, and print what a reader without an account would see.

    python scripts/check_extension.py                       # this folder's build, live
    python scripts/check_extension.py --server http://localhost:8012
    python scripts/check_extension.py --build path/to/unpacked --postcode "KT3 4HX"

Written on 21 Sep 2026 to check extension 2.4.0 before its upload, and the
published build against the server's feed; 2.4.0 went live on 25 Sep
2026. Rightmove is never visited: the listing
is a local page served at a Rightmove address, with the postcode in its
title as on the real portal, so the content script runs as it would there.
Every call the extension makes to ukpropertyinsight.co.uk goes to production
with X-Internal-Check, or is sent to the dev server instead with --server.

Signed out only. Signing in would mean typing a password into the panel,
which a script must not do; the signed-in feed is pinned by
tests/test_leaks_closed_21sep.py.

It needs a browser that still loads unpacked extensions from the command
line. Chrome itself stopped (version 137), so the default is Microsoft Edge;
--channel chromium uses Playwright's own Chromium where the machine allows
it to run (this one refuses it). Screenshots go to --shots, a temporary
folder by default. Exits 1 when the panel never loads, the feed fails, or
the page throws.
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
LIVE = "https://ukpropertyinsight.co.uk"
LISTING = "https://www.rightmove.co.uk/properties/100000001"

parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
parser.add_argument("--build", default=str(ROOT / "browser-extension"), help="unpacked extension folder")
parser.add_argument("--server", default="live", help="live, or a dev server such as http://localhost:8012")
parser.add_argument("--postcode", default="BS10 6RG", help="the listing's postcode")
parser.add_argument("--channel", default="msedge", help="msedge (default) or chromium")
parser.add_argument("--shots", default=None, help="folder for screenshots")
args = parser.parse_args()

build = Path(args.build).resolve()
server = args.server.rstrip("/")
shots = Path(args.shots) if args.shots else Path(tempfile.mkdtemp(prefix="extension-check-"))
shots.mkdir(parents=True, exist_ok=True)
listing_page = f"""<!doctype html><html><head><meta charset="utf-8">
<title>3 bedroom semi-detached house for sale in Example Road, {args.postcode}</title>
</head><body style="font-family:sans-serif;background:#f4f4f4;margin:0;padding:40px">
<h1>Example listing (a local page, not Rightmove)</h1>
<p>Only the title carries the postcode, as on the real portal.</p></body></html>"""
feed_calls = []


def to_site(route, request):
    headers = {**request.headers, "x-internal-check": "1"}
    url = request.url if server == "live" else server + request.url[len(LIVE):]
    response = route.fetch(url=url, headers=headers, timeout=120000)
    body = response.body()
    if "/api/" in request.url:
        try:
            feed_calls.append((request.url[len(LIVE):], response.status, json.loads(body)))
        except ValueError:
            feed_calls.append((request.url[len(LIVE):], response.status, None))
    route.fulfill(response=response, body=body)


def lines(text):
    return " | ".join(line.strip() for line in text.splitlines() if line.strip())


def panel_text(page):
    return page.locator("#pv-overlay-host .pv-tab-content").inner_text()


profile = Path(tempfile.mkdtemp(prefix="extension-profile-"))
errors, overview, schools, costs = [], "", "", ""
try:
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile), channel=args.channel, headless=True, viewport={"width": 1280, "height": 900},
            args=[f"--disable-extensions-except={build}", f"--load-extension={build}"])
        context.route("https://www.rightmove.co.uk/**",
                      lambda route, request: route.fulfill(status=200, content_type="text/html", body=listing_page))
        context.route(LIVE + "/**", to_site)
        page = context.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(LISTING, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("#pv-overlay-host .pv-tabs button", state="attached", timeout=60000)
            for _ in range(120):  # the overview waits on the feed
                if not page.locator("#pv-overlay-host .pv-tab-content .pv-loading-block").count():
                    break
                time.sleep(1)
            time.sleep(1.5)
            overview = panel_text(page)
            page.screenshot(path=str(shots / "overview.png"))
            card = page.locator("#pv-overlay-host .pv-tab-content *", has_text="Costs & Affordability").last
            if card.count():
                card.scroll_into_view_if_needed()
                page.screenshot(path=str(shots / "value-and-market.png"))
                try:
                    card.click(timeout=3000)
                    time.sleep(1.0)
                    if page.locator("#pv-overlay-host .pv-modal-backdrop").is_visible():
                        costs = page.locator("#pv-overlay-host .pv-modal").inner_text()
                        page.screenshot(path=str(shots / "costs-calculator.png"))
                        page.locator("#pv-overlay-host .pv-modal-close").click()
                except Exception:  # a locked card: its lock takes the click
                    costs = "(locked)"
            page.locator("#pv-overlay-host .pv-tabs button", has_text="Schools").first.click()
            time.sleep(1.0)
            schools = panel_text(page)
            page.screenshot(path=str(shots / "schools.png"))
        except Exception as exc:
            errors.append(f"the panel did not load: {exc}")
        context.close()
finally:
    shutil.rmtree(profile, ignore_errors=True)

feed = next((data for url, status, data in feed_calls if url.startswith("/api/extension-report")), None) or {}
print(f"build {build.name} ({json.loads((build / 'manifest.json').read_text())['version']}) against {server}")
print("feed calls:", [(url[:70], status) for url, status, _ in feed_calls])
print("free cards in the feed:", [card.get("title") for card in feed.get("free_cards") or []])
print("locked words in the feed:", feed.get("schools_locked_label"))
for row in (feed.get("schools") or [])[:2]:
    print("school row:", {key: row.get(key) for key in ("name", "admission_miles", "admission_year", "verdict")})
print("\nOVERVIEW:", lines(overview)[:2000])
print("\nCOSTS & AFFORDABILITY:", lines(costs)[:600])
print("\nSCHOOLS:", lines(schools)[:900])
print("\nscreenshots:", shots)
failed = bool(errors) or not overview or not any(status == 200 for _, status, _ in feed_calls)
if errors:
    print("page errors:", errors)
sys.exit(1 if failed else 0)
