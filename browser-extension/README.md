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

What's already done, in this repo:
- **Icons** — `icons/icon16.png`, `icon48.png`, `icon128.png` (referenced in
  `manifest.json`), plus a spare `icon512.png` if the store listing form
  wants a bigger promotional image.
- **Manifest V3** — already the case; MV2 submissions are rejected outright.
- **Privacy policy** — `/privacy` on the live site now has a dedicated
  "browser extension" section (Chrome requires a privacy policy URL for
  any extension handling user data - this one reads page content and
  makes network requests, so it needs one). Use
  `https://ukpropertyinsight.co.uk/privacy` as the policy URL in the
  listing form.
- **Submission package** — `ukpropertyinsight-extension.zip` in this
  folder (manifest + content.js + icons), ready to upload as-is. Rebuild
  it after any content.js/manifest change:
  ```
  python -c "
  import zipfile
  files = ['manifest.json', 'content.js', 'icons/icon16.png', 'icons/icon32.png', 'icons/icon48.png', 'icons/icon128.png']
  with zipfile.ZipFile('browser-extension/ukpropertyinsight-extension.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
      for f in files: zf.write(f'browser-extension/{f}', arcname=f)
  "
  ```

What still needs a human (Claude can't create accounts, enter payment
details, or submit on your behalf):
1. **Register as a Chrome Web Store developer** at
   https://chrome.google.com/webstore/devconsole — one-time $5 fee, needs
   your own Google account and a card.
2. **Take real screenshots** — load this folder as an unpacked extension
   (see above), visit an actual Rightmove/Zoopla/OnTheMarket listing, and
   screenshot the widget open on a real page. The store listing needs at
   least one (1280x800 or 640x400px); the automated browser tooling used
   to build this extension can't reliably load a real Chrome extension
   against those specific live sites, so this step needs a real browser.
3. **Fill in the listing form**: upload the zip, paste in the description
   below, upload your screenshot(s) and `icon128.png`, set the privacy
   policy URL above, pick a category (Productivity or Shopping both fit),
   and list the single permission (`storage`) with a one-line justification
   ("remembers your login between visits to a listing page").
4. **Submit for review** — typically a few days to a couple of weeks for a
   first submission. Chrome's reviewers most commonly reject on: unclear
   permission justification, a privacy policy that doesn't match what the
   code actually does, or trademarked site names used in a way that implies
   official partnership - the description below is written to state
   compatibility as a fact ("works on X"), not affiliation.

### Suggested store listing copy

**Short description** (132 char limit):
> Free UK property reports overlaid on Rightmove, Zoopla & OnTheMarket listings — sold prices, schools, crime, flood risk & more.

**Detailed description:**
> See official UK property data on any listing without leaving the page.
>
> UKPropertyInsight Overlay adds a summary bar to Rightmove, Zoopla and
> OnTheMarket listing pages, pulling sold price history, EPC ratings,
> flood risk, crime stats, nearby schools and more from official UK
> government data sources - not the listing site's own marketing copy.
>
> - Works automatically on supported listing pages, no setup needed
> - Free tier included; log in with your UKPropertyInsight account to
>   unlock the full report
> - Sourced from HM Land Registry, the EPC register, the Environment
>   Agency, Police.uk, Ofsted and ONS Census data
>
> Not affiliated with, endorsed by, or officially connected to Rightmove,
> Zoopla or OnTheMarket - this extension simply reads the postcode from
> listing pages you're already viewing and shows public data alongside it.

That last disclaimer line matters for the review - Chrome's policy team
specifically checks that using a trademarked name in a description doesn't
imply an official partnership that doesn't exist.

## What's NOT handled yet

- No toolbar icon/popup — this is a pure content-script overlay by design.
  The icons added above are for the Web Store listing and the
  chrome://extensions page, not a popup UI.
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
