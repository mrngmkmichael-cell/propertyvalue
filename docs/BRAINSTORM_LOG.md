# Brainstorm log

Read by the daily improvement-ideas routine so it does not re-suggest
what is already done, in progress, or deliberately rejected. Updated by
the local Claude session as things ship. Newest first.

## Shipped (do not re-suggest)

- Admissions pages (1 Sep 2026): /schools/admissions (82 councils,
  3,251 schools with a published last-distance-offered figure), one
  hub per council listing every school tightest first with grade,
  round and how full, each linking to the school page and checker,
  and /schools/how-admissions-work, a plain-English guide to the
  calendar, criteria order, how distance is measured and why most
  schools have no catchment. All in the sitemap; linked from every
  school page and the schools guide.

- School pages (1 Sep 2026): a free "will an address get in?" checker
  (postcode measured against the school's published admission
  distance, three honest bands: Likely / Borderline / Unlikely), a map
  with the distance drawn as a circle and the checked address on it,
  a grade strip of tiles, and a per-school share card at
  /og/school/{urn}.png so links into WhatsApp and Mumsnet show the
  figure. Google and Leaflet branches both done.

- Schools guide rebuilt (1 Sep 2026): every school visible in a
  sortable table (name, phase, Ofsted, distance, admission distance
  with provenance, results), a map with Ofsted-coloured pins and
  admission-distance circles (solid = published, dashed = estimate),
  Google in production and Leaflet in dev. School profile popups load
  on demand from /schools/profile/{urn} instead of 99 inline dialogs:
  guide HTML 1.6 MB to 0.37 MB, report 2.3 MB to 0.47 MB.

- One-click sample report link in the hero (M1 1AE), zero-typing route
  into the product.
- Globe intro and Leaflet self-hosted under /static/vendor/; no scripts
  from CDNs anywhere, pinned by a test.
- Area guides link to their regional price league page.
- School pages and the schools guide cross-link to the district's
  private-schools page.
- /schools/outstanding: targets "ofsted outstanding schools near me"
  with real register counts, per-region table, top districts.

- Per-school pages de-duplicated: lead with each school's own data
  (roll, capacity, EAL, destinations, result trends). 73% shared
  phrasing down to 53%.
- Private-school area pages de-duplicated (64% down to 53%).
- 12 per-region price league pages (/market/house-prices/{region}).
- Comparison (vs) pages: first 35 pairs in the sitemap, earn-gated.
- Sitemap curated and grown on Search Console evidence only: 505
  districts as of 31 Aug 2026.
- Titles under 60 chars via seo_title(); descriptions under 155.
- Review ask at the foot of every report (in product, not email).
- /embed backlink offer on the price pages.
- Postcode autocomplete on every search box.
- 202 cold-report waits recorded as synthetic pageviews.
- Admin page: test accounts excluded everywhere, per-day traffic
  shape-checked for automation, honest unflagged average.
- Bot filter widened (HTTP libraries, headless, AI crawlers).
- Hero de-slopped: stats row and count-up numbers removed.
- New-account welcome banner; pricing CTAs land on search, not
  the price list.
- Trustpilot: verbatim quotes plus plain link ONLY (their brand rules
  prohibit scores, stars, counts outside their widgets; enforced).
- Extension 2.2.0: OnTheMarket detection, miles not km, no silent
  failure; store page linked from /browser-extension.
- District following (watch an outcode) with visit-time diffs.
- Printable viewing checklist; share-a-report with a sender note.

## In progress or queued (do not re-suggest as new)

- Report page DOM reduction (21k nodes) - separate session, approved.
- Crime months as "May 2026" not "2026-05" - separate session.
- B2B agency tier: owner emailing the comped agency power user.
- Extension Reddit post: waiting for 2.2.0 store review.
- Google Ads £30-50 experiment: awaiting owner decision.

## On hold by the owner (suggest only if new evidence)

- Press pitch outreach (drafted, held).
- Weekly GSC export loop (tooling ready, held).
- Trustpilot email invites (held).

## Rejected on principle (never suggest)

- Interface languages / translations. Built in full on 31 Aug 2026
  (nine languages, landing pages, header picker with flags, ~640
  translated strings per Chinese variant) and rolled back the next day
  at the owner's request: "the other language is not good". The lesson
  was quality, not mechanics; machine-authored translations without a
  native reviewer in the loop were not good enough to ship. Do not
  re-suggest unless the owner raises it AND brings a reviewer per
  language. The full implementation lives in git history
  (028aced..dfca9ad, reverted).

- Any third-party script on the site (privacy page promises none run;
  this killed the Trustpilot widget and any analytics snippet).
- Scheduled or promotional emails without a fresh opt-in (change-alert
  emails promise "never on a schedule").
- Trustpilot scores, stars or review counts rendered by us.
- Features without a reliable official data source (no modelling, no
  estimates): HS2 corridors, rights of way, planning applications
  (until planning.data.gov.uk covers ~50+ councils).
- Submitting all 2,943 districts to the sitemap at once.
- Em-dashes or exclamation marks in user-facing copy.
