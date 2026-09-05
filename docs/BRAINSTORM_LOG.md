# Brainstorm log

Read by the daily improvement-ideas routine so it does not re-suggest
what is already done, in progress, or deliberately rejected. Updated by
the local Claude session as things ship. Newest first.

## Shipped (do not re-suggest)

- The afternoon of 5 Sep 2026, on "analyse the whole website and improve
  it" with Michael away for three hours. Measured first: the funnel is
  15 to 50 homepage views a day, 0 to 26 report starts, no account in
  five days, and most visitors land on an area guide or a school page,
  not the homepage. So the forms moved to where people arrive: an
  address check opens every area guide, a compact checker sits under
  every school page headline, council hubs offer one too, and the
  running-costs answer can be shared. Earlier the same day, at
  Michael's direction: /running-costs answers a postcode on the page
  (one table per year, one for the one-off costs, one worth knowing;
  house number for the home's own EPC; map beside the box, Google in
  production and Leaflet in dev); a school search box with a typing
  placeholder on the admissions index and the schools guide, backed by
  /api/school-search over every open school. Then two crawls of every
  page family on production (about 1,200 pages): no broken page except
  /compare/M43/vs/SK16, advertised by the sitemap and answering 404
  because neighbourhood is not symmetric (fixed: a pair exists when
  either side counts the other); guide titles were 63 to 66 characters
  with the brand suffix (dropped on single-district pages) and
  descriptions 168 to 170 (trimmed). The cold schools-guide build was
  profiled statement by statement: seven database round trips plus two
  postcodes.io calls. Now districts resolve from the outcode table on
  disk, the six one-row-per-school tables come back in one joined query,
  the guide path skips the 37-column detail row it never shows, and
  schools(latitude, longitude) is indexed (created on Neon by hand, and
  in the model for fresh databases). Twelve cold districts on
  production: mean 1.75 s before, 1.45 s after. The school page had the
  same shape, eight one-row lookups in a row; now one statement, and
  twelve cold school pages went from a mean of 1.44 s to 1.01 s. The
  market report is in tier 2, so the first visitor after a deploy no
  longer waits 4 to 5 s for it. The estate directory gained FAQ markup for "who manages
  my estate", and the report and school pages link to the running-costs
  table for their own postcode. Titles across the site run long by
  design (school names, council names); left alone.

- The night of 4 to 5 Sep 2026, on Michael's "business partner, decide
  on your own" instruction. Backup first (E:\Claude\PropertyValue-backups6-09-04:
  every table as CSV, schema, git bundle, .env). Then: the landing page
  names three pillars (prices, schools, running costs) with live counts,
  headline "Prices, schools, and the running costs no listing shows you";
  /running-costs (council tax Band D for 300+ English councils ranked,
  EPC energy, tenure); /estate-charges (fleecehold explained from the CMA,
  HOA, Hansard, Commons Library and the 2024 Act, twelve questions for a
  conveyancer, FAQ markup); and "Who manages your estate?": 168,580
  active residents' and estate management companies from the Companies
  House snapshot, 12,002 registered to twenty named agents' offices
  (attributed by registered office, each checked), a league table, a
  page per agent, and a name search. Wording is "registered to X's
  office", never "managed by". No charge figures: no official source.
  The £19 pass leads the pricing page, but is not on sale because
  STRIPE_PRICE_ID_PASS is unset on Render (Michael's to create).
  The report itself now says "what it costs to live here" under the
  score: council tax at Band D, the EPC's yearly energy estimate, tenure.
  Press kit gained story three. Estate charge *records* (community data,
  moderation, PDF extraction) deliberately not built: no traffic to feed
  the funnel yet, and it changes what the site is; Michael's call.

- The wait made worth watching (4 Sep 2026): the 202 "building your
  report" page now shows the district's facts from its area guide in
  tier 2 (median sale price and count, price trend, share of schools
  Good or better, crime, flood zone, the schools with a published
  distance), each naming its source, instantly, while the address's own
  checks arrive. Cause: on 2 Sep 26 people started a report and 12 saw
  one finish; a cold build takes 13 s. Under the score, signed-out
  readers get one return hook that is not "unlock premium": save the
  property free and be told when it changes. Also the sitemap widened to
  all 3,627 school pages, and the guide's rating cell hotfix.

- Ofsted read in full (4 Sep 2026): the import used one column, the
  overall grade from the last graded inspection, blank for 54% of primary
  and secondary schools. Now the ungraded-inspection outcome ("school
  remains Good" becomes a Good grade dated to that visit; "improved
  significantly" and the rest become a one-line note where the blank
  was) and the November-2025 report card (nine areas on a five-point
  scale, shown on the school page, badge "Report card") are read too.
  Schools with nothing to show: 54% to 16%; 1,986 report cards; 8,243
  ungraded outcomes. scripts/import_ofsted_outcomes.py refreshes in
  place monthly (update the URL in import_schools.py first). Prompted by
  Michael asking why Harris Primary Academy Orpington showed "Not rated".

- Daily ten, 4 Sep 2026 (one deploy): area guides carry an admitted-from
  column and a table of the district's schools with a published distance,
  each linked (appears as each guide's weekly cache refreshes); the 404
  page offers the search and the data pages; premium and the admissions
  guide carry FAQ markup with their honest answers; the homepage FAQ
  answers "will my child get into the school" and shows the live count
  of published distances; a share row (WhatsApp, email, copy) on the two
  stories, every council hub and every school page; Cache-Control on
  anonymous cache hits; the importer resubmits to IndexNow itself; and
  .github/workflows/uptime.yml probes /area/M1 and / every ten minutes
  and alerts Telegram (needs the two TELEGRAM secrets in GitHub).

- Three more councils (4 Sep 2026): Devon (52 schools, its two
  allocation-breakdown spreadsheets, metres of last offered place),
  Nottinghamshire (75, the allocation summaries at the back of its seven
  district PDFs, keeping only schools that filled and dropping two
  hundred-mile rows that were not distance decisions; the secondary
  table is not extractable as text) and Croydon (21, its oversubscribed-
  primaries spreadsheet). 88 councils, 3,626 schools. Looked at and
  left: Norfolk (no figures published), Derbyshire, Lincolnshire,
  Leicestershire, Cornwall, Wiltshire, Liverpool, Wakefield (nothing
  found), Bradford (guide link rotted, arrangements only), Barnet (guides
  page carries no documents), Enfield (403 to scripts), Medway (figures
  sit in a JS-rendered per-school directory), Lancashire (booklets
  rotate). Kent and Hampshire remain the two largest gaps.

- What a tight gate costs (3 Sep 2026): /schools/catchment-house-prices
  pairs every published admission distance with the Land Registry median
  of the districts within reach (bisect on latitude-sorted centres, so
  3,478 schools price in under a second, cached a day). Headline: within
  reach of schools admitting from under half a mile the middle district is
  £350,000 against £278,000 nationally; 30 tight gates sit below the
  national middle. Linked from the first story and the admissions index,
  in the sitemap and smoke.

- Six smaller fixes the same evening: the homepage demo strip sat between
  the eyebrow and the headline on phones, pushing headline and search
  below the first screen (flex order); the pageview INSERT now runs after
  the response is sent (Starlette BackgroundTask), so a cached page no
  longer pays a Neon round trip before its first byte; report builds call
  malloc_trim and run two at a time, the code half of the 450 MB memory
  fix (the env half, MALLOC_ARENA_MAX=2, is Michael's); the sitemap is
  cached an hour and dated by deploy instead of "today" on every request;
  distances under a quarter of a mile show metres; council hubs invite
  the signed-out to sign up for republish alerts; the signup page said
  "three reports" when the free allowance is one.

- Ten exposure moves (3 Sep 2026), after the first Search Console
  look (8 clicks, 2,830 impressions, position 46, surfaced for "private
  school [town]"): /schools/independent and one page per council (151,
  DfE register) shaped like that query; BreadcrumbList on every
  admissions page; Dataset markup with a CSV distribution
  (/schools/admission-distances.csv) on the story page and each hub for
  Google Dataset Search; share cards for hubs and the story; /llms.txt
  for AI search; /internal/indexnow-resubmit for pages whose titles
  changed; council hub titles that say "catchments"; area guides and the
  footer linking into the admissions pages; each school page linking its
  nearest six with a figure (a mesh, not 3,478 leaves); the admissions
  index carrying each council's middle distance and tightest school.
  Not done, on purpose: externalising the 238 KB inlined stylesheet. It
  was measured and chosen (see inline_css); ~25 KB gzipped per page is
  not the bottleneck.

- The data story and the press kit (2 Sep 2026): /schools/tightest-catchments
  ranks every published admission distance we hold (3,478 schools, 85
  councils): the 50 tightest gates, the widest, and the councils compared
  by their middle school's distance, every figure linked to its school or
  council page, with a journalist's note on sourcing. docs/press/press-kit.md
  (regenerated by scripts/press_kit.py) carries the national release, three
  pitch templates and one paragraph per council. Purpose: links, which a
  six-week-old domain has none of. Two zero-distance rows were bad data and
  are gone; the importer now refuses them.

- School page titles answer the query parents type (2 Sep 2026): "X
  catchment area: admitted from 1.2 miles in 2025/26", with the council as
  the part dropped when the title runs long. Search Console's first 8
  clicks came from "private school [town]" and "catchment"-shaped queries;
  the number in the title is the answer no other result has.

- The pageview counter fixed twice (2 Sep 2026): a page served from the
  anonymous HTML cache raised inside the counter (no session layer on a
  cache hit), so from 1 Sep every cached view went uncounted; the 142 and
  388 "pageviews" on 1 and 2 Sep are undercounts. And our own checks now
  send X-Internal-Check: 1, which the counter ignores, so smoke runs and
  deploy polls never read as visitors.

- Council coverage widened (2 Sep 2026): Essex (188 primary schools,
  the council's FOI spreadsheet ECC19026611), Sheffield (34 schools,
  the council's two oversubscribed-schools PDFs) and Manchester (7
  secondaries from "The demand for secondary school places"; the
  council's firewall refuses scripts, so the fetcher falls back to the
  figures read in a browser, dated). 85 councils now. The importer
  gained --only "Essex,Sheffield" to replace one council's rows without
  re-fetching the other 82, and a phase hint so a council's short
  "Ecclesfield" matches the primary in the reception table and the
  secondary in the Year 7 one. Looked at and left: Lincolnshire (two
  grammar schools only), Lancashire (booklet links rotate and carry no
  per-school distance that a script can find), Kent and Hampshire (no
  parsable document found).

- The intersection made visible (1 Sep 2026): every school page prices
  the districts inside its admission distance ("What it costs to live
  within reach of X", from the area guides' medians, with an FAQ);
  the report's Schools card leads with "Likely for N schools" and the
  catchment card with the three-band counts; the compare page has a
  "Schools likely to admit" row; school pages carry the next statutory
  deadline for their phase. Positioning sentence on the admissions
  index.

- Extension 2.3.0 (1 Sep 2026): the Schools tab shows, for each nearby
  school, whether this listing is Likely / Borderline / Unlikely to
  get a place, against how far the school admitted from last time
  (published figure linked to the school page; estimates marked).
  Homepage and extension page now say the positioning out loud:
  school sites show you the school, this shows you whether the house
  gets a child in. Zip rebuilt; owner uploads to the Chrome store.

- School shortlist + admission-update alerts (1 Sep 2026): the
  shortlist shows each saved school's grade and current published
  distance with its round; a "Save to my shortlist" button on every
  school page; an opt-in email sent only when a council republishes a
  saved school's distance, via /internal/send-admission-updates run
  after an admissions import (first sighting records, never emails).
  Honours the "never on a schedule" promise: event-driven only.

- Anonymous HTML cache (1 Sep 2026): the finished HTML of the slow
  pages (homepage, area guides, schools guide, admissions hubs, school
  pages without ?check, market pages) is kept ten minutes and served
  to signed-out visitors and crawlers. Dev measurements: schools guide
  3.5 s to 1 ms, area guide 1.2 s to 1 ms, council hub 2.0 s to 1 ms.
  Never for a session or referral cookie, never for unknown query
  strings, never when the response sets a cookie.

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

- Press pitch outreach (three stories drafted in docs/press/press-kit.md).
  Held again by Michael on 5 Sep 2026 ("hold off"): do not send, do not
  re-raise.
- The £19 one-off pass. Built and leading the pricing page when on sale,
  but STRIPE_PRICE_ID_PASS stays unset by Michael's decision on 5 Sep 2026
  ("hold off"): do not ask again; subscriptions are the only product.
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
