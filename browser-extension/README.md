# PropertyValue Overlay (browser extension)

Injects a small PropertyValue widget into Rightmove, Zoopla, and OnTheMarket
listing pages — Overview Score, average sold price, flood risk, crime, and
schools — with a link through to the full report.

## Before you use it

1. Deploy the site (already done on Render) and note its public URL.
2. In `manifest.json`, replace `https://YOUR-DEPLOYED-DOMAIN` under
   `host_permissions` with your real domain, e.g. `https://propertyvalue.onrender.com/*`.
3. In `content.js`, replace the `API_BASE` constant near the top with the
   same domain (no trailing slash), e.g. `https://propertyvalue.onrender.com`.

## Load it locally (Chrome/Edge, unpacked — for testing)

1. Go to `chrome://extensions` (or `edge://extensions`).
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and select this `browser-extension` folder.
4. Visit a Rightmove/Zoopla/OnTheMarket property listing page — the widget
   should appear in the bottom-right corner within a second or two.

## How it finds the property

Rather than hardcoding each site's HTML structure (which breaks on every
front-end redesign), the content script scans the page for a UK postcode
pattern — first in meta tags, then in the page title, then across the
visible text. This is slower to write than "grab this CSS class" but far
more durable across the three sites and their redesigns.

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
