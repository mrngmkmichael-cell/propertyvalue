# UKPropertyInsight Overlay (browser extension)

Injects a UKPropertyInsight widget into Rightmove, Zoopla, and OnTheMarket
listing pages — a slim always-visible summary strip (Overview Score,
headline verdict) pinned to the top of the page, which expands into a full
tabbed report on click: Summary, Map, Market History, Comparables, Schools,
EPC, Demographics, and Crime.

## Before you use it

1. Deploy the site (already done on Render) and note its public URL.
2. In `manifest.json`, replace `https://YOUR-DEPLOYED-DOMAIN` under
   `host_permissions` with your real domain, e.g. `https://ukpropertyinsight.co.uk/*`.
3. In `content.js`, replace the `API_BASE` constant near the top with the
   same domain (no trailing slash), e.g. `https://ukpropertyinsight.co.uk`.

## Load it locally (Chrome/Edge, unpacked — for testing)

1. Go to `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and select this `browser-extension` folder.
4. Visit a Rightmove/Zoopla/OnTheMarket property listing page — a slim
   summary bar should appear pinned to the top of the page within a second
   or two. Click it to expand the full tabbed report.

## How it finds the property

Rather than hardcoding each site's HTML structure (which breaks on every
front-end redesign), the content script scans the page for a UK postcode
pattern — first in meta tags, then in the page title, then across the
visible text. This is slower to write than "grab this CSS class" but far
more durable across the three sites and their redesigns.

## Data source

All eight tabs are powered by one endpoint, `GET /api/extension-report`
(`app/main.py`) - a richer payload than the free tier of the main site
needs for a single glance, but still deliberately short of the full
property-page gather: no premium-gated signals (valuation estimate, risk
designations, coal mining, etc.). Cached server-side for an hour per
postcode, since this can be hit repeatedly as someone browses multiple
listings.

## Publishing to the Chrome Web Store

Not done here — that requires a one-time $5 Google developer account (a
real account/payment step, not something to automate) and a store listing
(icons, screenshots, description) beyond this MVP's scope. Until then,
"Load unpacked" is the only distribution path, which is fine for your own
testing but not for real users to install.

## What's NOT handled yet

- No toolbar icon/popup — this is a pure content-script overlay by design,
  to avoid needing generated PNG icons for an MVP.
- No SPA-route detection: if a site loads a new listing without a full page
  navigation, the widget won't refresh until the page is reloaded.
- No dismissal persistence — closing the widget only hides it for that page
  load, not future visits.
- The Map tab embeds an OpenStreetMap iframe - if a listing site's own CSP
  blocks framing third-party domains, that one tab may not render even
  though the rest of the widget works fine. Not something we can control
  from our side; untested against the three sites' real CSP headers since
  that requires the unpacked extension actually loaded against a live
  listing page, not something verifiable from a dev environment.
