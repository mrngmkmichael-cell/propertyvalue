# Brainstorm log

Read by the daily improvement-ideas routine so it does not re-suggest
what is already done, in progress, or deliberately rejected. Updated by
the local Claude session as things ship. Newest first.

## Shipped (do not re-suggest)

- The six ideas of the 22 Sep 2026 brainstorm, built on 25 Sep. Michael
  said "do all", which is the approval ideas 4 and 6 needed.
  1. One flat, one link. The M1 1AE sample's "Which home is yours?" row
  offered "Apartment18 113" beside "Apartment 18, 113": one record at
  113 Newton Street runs the flat word into its number. _address_words
  (main and watchlist) splits flat, apartment, apt, unit and maisonette
  from a number run on to them, and the row's label gains the space and
  comma. Matching, saved homes and the house number filter all follow.
  2. The district's crime month is kept in the Postgres cache. Two cold
  reports on 25 Sep spent 5.3 s and 5.2 s on crime-summary-for-outcode,
  the slowest source both times: a busy district's Police.uk month is a
  megabyte, and the answer is the same for every address in the district.
  Stored without points (no caller draws them), with the coverage rule
  applied before the cache is read. crime-summary-near, 2.9 to 4.7 s, is
  the address's own and stays live; it is now the thing to look at.
  3. /alternatives re-read on 25 Sep: prices unchanged at all three.
  Crystal Roof had added a school guide, marked new (Ofsted, results,
  applications and offers, pupils, class sizes, destinations; England and
  Wales, no admission distance), and the schools cell and FAQ say so.
  4. The brainstorm's premise was half wrong: SW1A's "down 25.4%" (20.7%
  by 25 Sep) is the UK House Price Index for all of City of Westminster,
  not the 5 local sales beside it. What was true: the index moved from
  -9.0% to -24.2% in three months on 84 to 136 sales a month, and its two
  newest months carry no count because HM Land Registry revises them. A
  council-wide yearly move of 10% or more (HPI_SWING_NOTE_PCT) now says,
  from the same source, how many sales the index rests on and that its
  newest month is a first estimate: guide lead, FAQ and House prices
  section. Guides warmed before 25 Sep show it as their week runs out.
  5. The SE15 2AF double unlock is not the OX3 0SG shape: account 90
  opened number 77 and came back on 24 Sep; account 91 opened the
  postcode without a number 31 minutes later. Nothing built.
  6. The store serves 2.4.0 (Chrome's update service, 25 Sep).
  scripts/check_extension.py passed live, and EXTENSION_240_LIVE and its
  False branches, words and tests are gone.
  No dev server could be started from the session; tests 800, then smoke
  and the audit against production after deploy.

- All eight ideas of the 18 Sep 2026 morning brainstorm, built the same
  day in a separate worktree while the audit batches ran in the main
  checkout. Michael said "Do all in sequence", which is the approval
  ideas 1 and 8 needed. The day's reading: 10,210, 13,337 and 12,120
  views on 15 to 17 Sep, almost all crawl; 3 real sign-ups; the first
  subscription on an account's first day (account 87, paid within two
  minutes of signing in, opened no report, saved nothing).
  1. Crime where Police.uk cannot give a true count (3c2d40b). Greater
  Manchester Police publishes a trickle: 5 records within a mile of
  central Manchester for July 2026 against 493 in Headingley, and 0 to
  2 at six other GM boroughs, while every other force read gave 25 to
  4,728. The homepage's sample report said "6 crimes recorded". Police
  Scotland does not publish at all. crime.coverage_gap names the ten
  boroughs by council and Scotland by country, summary_near takes
  district and country as required keywords, every surface says why
  there is no count, guides and comparisons apply it at render. The
  accuracy log gained its first self-found entry ("Found by our own
  check"). check_sources.py reads central Manchester past the rule on
  every run: take the boroughs off crime.GREATER_MANCHESTER_DISTRICTS
  when it says the force publishes in full again.
  2. District comparisons (514e365): a tie says "Neither" (it named the
  right-hand district), and the five rows from one energy certificate,
  the EPC line of the short version and the one-postcode "Average sold
  price" are gone from district pages; price answers and indexing use
  the district median only.
  3. A subscriber with no home carried gets a first step on the success
  page and a one-line banner on the homepage until something is saved
  (ccf3ba8); report starts from it are marked "premium-success".
  Found on the way and fixed separately (dacf1a8): since 26 Aug the
  homepage's "Sign up free" button itself ran the ring's pulse, fading
  between 0.55 opacity and nothing every 2.4 s.
  4. A real crawler asking /running-costs for a postcode gets the page
  without the answer, and answered URLs are noindex (17ef6f5).
  5. /admin "Who is fetching pages": a coarse agent family against a
  page family, per hour, in memory for 24 hours (0fe4dc5). Read it
  before judging any crawl-shaped day.
  6. OpenStreetMap left the homepage's "official" strip for Defra, and
  the note says what OpenStreetMap supplies (4c80fae). The "13 official
  sources" count is left to the audit's source-count work (D8).
  7. Dates in words and counts with separators across about thirty
  surfaces, the share image's "40 CHECKS" from CHECK_COUNT, and an
  audit_site.py rule for both (scripts/copy_rules.py), clean on 52
  production pages (14b8050).
  8. PREMIUM_REACH says where each Premium check's source reaches,
  checked against the real gather in all four nations: Wales 10 of 15,
  Scotland 7, Northern Ireland 4. The wall names the missing checks
  outside England, /premium labels every check and answers "buying
  outside England"; storm overflows, former landfill and NHS England are
  not asked outside England and say "Not covered in Wales" where they
  said "None nearby", "No outfalls found nearby" and "No GP practice
  within 3 km" (414afb2). The bus timetables turned out to cover Great
  Britain, not England alone. Found on the way (77f8ffd): Northern
  Ireland's air quality was read from a cell near Chester, because
  postcodes.io's Irish Grid numbers were used as British ones; it is
  now converted from latitude and longitude.
  No dev server could be started from the session (it began as a
  scheduled run), so pages were rendered in-process with real data and
  screenshotted through a static server; smoke and the audit ran
  against production after each deploy.

- All seven ideas of the 17 Sep 2026 morning brainstorm, built the same
  day. Michael said "Do all in detail", which is the approval ideas 4
  and 5 needed. The day's reading: 3 real sign-ups and 3 free unlocks
  from 14 to 16 Sep, no Premium since 6 Sep, 2 paywall views in four
  days, and 16 Sep almost wholly crawl (5,101 views of 4,965 district
  comparisons).
  1. Flood outside England. The Environment Agency's maps stop at the
  border and an empty answer was read as Zone 1, so 720 area guides
  (449 Scotland, 191 Wales, 80 Northern Ireland), every report, the
  comparisons, the running-costs table, the PDF and the extension said
  "Zone 1 (low probability), Environment Agency" there, central Cardiff
  and Belfast included. flood_zones.outside_coverage(country) now
  answers first: zone_for and surface_water_risk.risk_for return None
  without asking, and every surface says "Not mapped for Wales" and
  names the body that maps it (Natural Resources Wales Flood Map for
  Planning, SEPA flood maps, DfI Flood Maps NI, URLs checked 17 Sep).
  Guides and comparisons decide it at render, so warm payloads are
  corrected without a re-warm. A change alert never reads Zone 1 to
  "Not mapped" as a change. The extension gets the label from the API,
  so no store release. A missing zone in England on the Premium
  extension card now reads "No data" rather than Zone 1. Reading the
  three nations' own maps is not built: DataMapWales did not answer on
  17 Sep, so whether it can be queried is unknown.
  2. Removal: district comparisons with Scotland or Northern Ireland on
  either side (country or region, for the 14 border districts), or
  without a price on both sides, are noindex, left out of the sitemap
  and not linked from guides. The pages still answer.
  3. An account's first saved home offers the Chrome extension, linked
  to /browser-extension; a second home gets the comparison instead, and
  a phone gets neither extension line. Judge it by signed-in views of
  /browser-extension and by "Came back".
  4. /admin "Returning accounts at the paywall": accounts that hit the
  wall on a later day than they joined, last 30 days, with saved homes
  and the pages opened in the half hour after. Four statements, only
  when /admin is opened; nothing is sent to anyone.
  5. /premium's signed-out plan buttons read "Start free, choose a plan
  later". They still go to sign-up then search, on purpose; carrying
  the plan to checkout was the alternative and was not built.
  6. Price growth names its month on comparisons and the guide's
  two-district table ("+3.3% (City of Edinburgh, June 2026)").
  Comparisons cached before today gain it as their week runs out.
  7. Three literal titles over 60 characters shortened:
  /browser-extension, /areas, /methodology. The extension's share title
  lost a stray full stop for the middot.
  No dev server could be started from the unattended session, so the
  pages were rendered through the test client and screenshotted at 1280
  and 375 px; smoke and the audit ran against production after deploy.

- Seven of the eight ideas of the 16 Sep 2026 brainstorm, built on 17
  Sep. Michael said "Do 1,2,3,5,6,7,8"; that is the approval idea 1's
  row tidy needed. Idea 4, taking the 684 /schools/guide?q= URLs out of
  the sitemap, was not chosen: do not re-raise it without a Search
  Console reading of what those URLs earn. The day's reading: 8 real
  accounts in seven days, none since 14 Sep, no Premium since 6 Sep,
  signed-in views 22, 2, 0 across 14 to 16 Sep, and 15 and 16 Sep almost
  wholly crawl (7,456 distinct paths in 8,202 views).
  1. One home is one saved row. Four of 16 accounts with saved homes
  held a home twice, because every lookup compared the raw house-number
  string, and the second-home offer put 17 CM5 9HH beside CM5 9HH.
  watchlist.same_home matches word by word ignoring case, commas and
  spacing, and a number against the same number with its street;
  same_place also joins a postcode-only row to a numbered one, and is
  used only where two rows would be put side by side. remember,
  save_item, get_item and the report's own lookup use it; the offer
  leaves out every row at this place and names a home saved twice once.
  The stored key and PremiumUnlock are unchanged. The one-off tidy of
  the two live duplicate rows (watchlist_items 29 of account 80 and 34
  of account 81, both without a note; the unlocked flat, 30, and 35
  are kept) was refused by the session's permission check and is not
  done. Until it is, those two accounts still see the home twice in My
  properties, though the report and the offer treat it as one.
  2. The anonymous HTML cache, the /property page cache and the sitemap
  keep their bodies zlib-compressed (_cache.pack_text). A guide page was
  452,640 bytes, 197,211 of them the same inlined stylesheet, so 108
  filled the 48 MB store the report gathers share, and the homepage's
  sample report measured 6.34 s cold against 0.28 s warm. About 60 KB
  compressed. Not the 3 Sep inlined-stylesheet question, which was
  transfer per request, and not a warming cadence.
  3. The signup page is counted by the page, not the request: a POST to
  /signup/seen from the page's own script, the rule the report wait has
  used since 11 Sep. 55 signup views and no account on 16 Sep moved hour
  for hour with a school and area crawl. robots.txt was deliberately not
  changed: the page is noindex, and a Disallow would stop Google reading
  that and risks "indexed, though blocked". /admin says the column
  includes crawlers before 17 Sep.
  5. A district or town search on the schools guide says a full postcode
  adds the address column, beside the table where the column would be,
  and the one box is labelled "Full postcode or town" with "Check a
  postcode or add an area". An area carried into a comparison now keeps
  its kind from its label, so a full postcode no longer loses its
  reading as soon as a second area is added, which it had since 12 Sep.
  6. Each change-alert run keeps its answer (homes checked, first looks,
  failures, homes changed, accounts told, emails sent, seconds) in the
  page cache table, 30 runs, shown on /admin under "Change alerts: what
  each run did". The workflow's curl had reported success daily while
  the figures went nowhere.
  7. A Likely, Borderline or Unlikely reading taken from a modelled
  estimate carries "est." in its own cell and says so on hover, and the
  table note says so. 79 such readings on the M14 5TG table.
  8. audit_site.py fetches /alternatives and fails once the rivals' read
  date is more than 30 days old, or missing, or given two ways.

- All eight ideas of the 14 Sep 2026 brainstorm, built the same evening.
  Michael read the eight and said "fix everything in order", which is
  the approval idea 5 needed. The day's reading: 5 real sign-ups and 4
  free unlocks from 11 to 14 Sep, no new Premium, paywall once a day,
  the /running-costs flood over since 11:00 UTC on 12 Sep.
  1. The report's running-costs link carried {{ postcode }}, which the
  report never sets, so every report since the strip shipped said "The
  full running-costs table for : every year" and opened an empty form,
  the homepage's own M1 1AE sample included. Now location.postcode.
  2. The free and Premium check lists live in main.py (FREE_CHECKS,
  PREMIUM_CHECKS). The pricing page renders them and the landing page
  counts them. The landing page had typed 23 free beside the pricing
  page's 26; a signed-out report locks 18 of 44, so 26 was right. A test
  checks both lists against which cards the report actually locks.
  DESIGN.md no longer says sign-up gives three free reports.
  3. "Add to Chrome . Free" is a middot, brownfield dwellings carry a
  thousands separator on the card and modal, and one fixed figure reads
  "1 was wrong".
  4. Sewage: outfalls still reporting now lead. M1 1AE's card led with
  Store Street CSO, last return 2022 and 0 spills, while Victoria Bridge
  Street CSO reported 34 in 2025. Every consumer reads the first entry
  (card, flag, score, solicitor questions, PDF), so the order was fixed
  once in sewage_discharge.pick_outfalls; the modal and PDF show each
  row's own year.
  5. Removal: the Resident Reviews card shows only once an area has a
  review. The table has never held a row. The modal and submit route
  stay. The /api card list the extension reads is unchanged.
  6. The homepage's anonymous HTML is kept an hour, other pages ten
  minutes. This helps only while the entry survives the shared LRU, so
  it is not the held cache partition and not a warming cadence.
  7. /admin's daily funnel has "Came back": of each day's new accounts,
  how many were seen signed in on a later day within a week, marked "so
  far" until the week is over. Judge the 12 Sep second-home offer by it.
  8. A day_label filter ("3 Jul 2026"): area guide sale dates and bus
  timetable weeks, school page bus weeks; the guide's HPI month uses
  month_label. Crime months remain the separate queued session.
  Tests 285. No dev server could be started from the unattended
  session, so smoke and the audit ran against production after deploy.

- The schools guide answers the postcode you typed (12 Sep 2026,
  Michael's ask, verified live). The table already held both halves of
  "will this address get in": the distance from the searched point to
  each school and the distance that school admitted from last time, in
  adjacent columns, with 34 rows of arithmetic left to the reader. A
  column headed with the postcode now does it, through the same
  _admission_verdict and the same three bands as the school pages, the
  report and the extension, so the guide cannot disagree with the rest
  of the site. Only for a real full postcode: place_search.resolve now
  reports whether it found a postcode, a district centroid or a geocoded
  town, and a district or town search gets no column, because measuring
  an admission distance against the middle of a town reads as an answer
  while being nothing of the kind. Live on BR6 9AX: 7 columns, 34 rows,
  23 readings, 2 Likely, 2 Borderline, 19 Unlikely, and "No figure"
  where the school has none. BR6 on its own: 6 columns, no readings.
  Also fixed on the same table, and it had never had it: the no-limit
  rule. 91 schools carry a published figure above 20 miles, up to
  868.30, and Brent's 621.37 is 1000 km exactly. School pages stopped
  presenting those as catchments earlier the same day; the guide had
  not caught up, so the Kingsbury guide printed 621.37 mi four times and
  its map drew four circles over the whole country, hiding every real
  ring. Now "No limit", sorted as the widest gate, read as Very likely,
  and no circle on either map branch. Verified on NW9 8AA: four rows
  read No limit, nothing in the table exceeds 16.97 mi, and the map
  shows real rings. At 375px the table scrolls inside its own box with
  no page overflow, though the reading sits sixth of seven columns and
  a phone reader has to scroll right to reach it. Tests 273.

- The scroll reveal made perceptible (12 Sep 2026). Michael asked
  whether the report page had any scroll animation; it has had one on 53
  blocks the whole time, confirmed running live. It was finishing before
  the eye arrived: 16px over half a second on a generic ease, triggered
  as a block first peeked over the bottom edge, which on a wide window
  put every reveal below where anyone was looking. Now 28px on a real
  ease-out over 0.7s with opacity 0.45s ahead of it, triggered at -22%
  with three quarters of a viewport of runway left. will-change is set
  while waiting and dropped on landing. The reduced-motion rule that
  shows every block immediately was already correct and is untouched.
  Do not re-suggest adding scroll animation to the report page.

- The trust figures count up on scroll (12 Sep 2026, Michael's ask).
  Once, at 60% visibility, four figures staggered 90ms apart, eased out
  over 1.1s, nothing at all under prefers-reduced-motion or without
  IntersectionObserver. The 28 Aug 2026 removal of the hero count-up
  still stands for the hero and its reason is unchanged: numbers
  spinning on load are a template tell. The real values stay in the
  markup and are overwritten only once the animation begins, so the
  first paint and every crawler see the truth.

- The second home is the offer (12 Sep 2026). Michael asked what could
  be done about subscriptions not moving. The accounts answered it: 41
  of 56 real accounts used the site on exactly one day and not one of
  them has ever subscribed; 15 came back on another day and 2 of those
  subscribed. Both paying accounts are the two with several homes on the
  go across several days, four and seven. Conversion has been a
  returning-visitor event every single time. 16 accounts have ever
  reached the paywall and 2 bought, which is not a broken paywall; only
  5 paywall views have happened in the six days since 7 Sep, which is
  the real problem. So the comparison, the page written for a reader
  weighing two houses, moved to the moment it becomes true: saving has
  been automatic since 8 Sep, so a second saved home means a second
  property genuinely opened, and the report now names the other home and
  links the comparison with both already chosen. Free accounts get the
  free light comparison and a line on what the paid one adds; Premium
  goes straight to every check; four at a time, and the block says so
  when more are saved. Costs no extra Neon round trip: the new list
  query replaced the single-row lookup the page already made. Michael
  chose this over a free preview of the full comparison and over pushing
  saving higher up the report. Do not re-raise those two as new; they
  are the untried alternatives if this does not move.
  Three defects found in the same reading, all fixed in the same deploy:
  the homepage hero strip said "40 checks" while the trust section on
  the same page said 44 three times, because the figure was a literal in
  main.py outside bump_check_count.py's reach (now CHECK_COUNT, moved by
  the script, pinned by the existing check-count test); the pricing page
  explained the difference between the pass and the subscription,
  unconditionally and in its FAQ structured data, while the pass has
  been off sale since 5 Sep with no button, aimed at exactly the reader
  least willing to take a monthly bill (gated on pass_available, and
  while it is off the question is answered with the three-month plan,
  saying plainly that it renews unless cancelled); and /running-costs,
  the busiest page on the site, told a mistyped postcode to use the full
  report search at the top of the page, which the one-box rule removed
  from that page on 11 Sep. Tests 269.

- A published distance beyond a school run is no longer shown as a
  catchment (12 Sep 2026). 91 school pages carried a council-published
  "last distance offered" over 20 miles, up to 868.30. Brent publishes
  621.37 for four schools, which is exactly 1000 km and so plainly a "no
  limit" sentinel; Gloucestershire publishes round county-wide numbers
  for 55. The page had been printing the figure as the headline, putting
  it in the title and the share title, drawing it as a circle on the map
  and rendering it to scale in the catchment picture. Above
  NO_DISTANCE_LIMIT_MILES = 20 the page now says what the figure implies
  and nothing more: distance did not limit entry, so no nearer applicant
  was refused on it. The ring is not drawn, catchment.png answers 404 and
  the plain share card is used instead, the districts section is dropped,
  the postcode verdict says the same thing, and nearby-school tables read
  "No limit". Found while pulling numbers for a Reddit post, which is
  the second time an outward-facing draft has surfaced a defect on the
  pages Google ranks best. Tests 268.
- All eight ideas of the 11 Sep 2026 brainstorm, built the same day
  (3afe11b, 94ce7f6, c5e16a7). Michael read the eight and said "Do all 8
  in sequence", which is the approval the two decision-flagged ones
  needed. The day's reading of production: 1,236 views by 10:54 UTC of
  which 490 were /running-costs alone and 144 the synthetic wait page,
  against 2 views of a finished report and zero signed-in views; no
  signup or unlock since 9 Sep at 22:24; Premium still the two accounts
  of 26 Aug and 6 Sep out of 50 real accounts.
  1. The /admin people figure no longer lets one page carry a day. The
  split counted every repeat view as a person, so a single URL hit at a
  near-constant rate through the night read as 683 people. A path now
  counts for at most as much as every other repeated page put together,
  and the page names what it held back. Chosen by running it over every
  day since 20 Aug: 20 of 23 unchanged, including both launch days when
  the homepage took 436 of 1,014 and 463 of 2,607 views with people
  signed in throughout; the 30 Aug scraper day falls from 5,307 people
  to 352 and 11 Sep from 550 to 104. A 25% share cap was tried first and
  left the scraper day reading 1,891, which is why it lost.
  2. postcodes.io lookups are cached a week, a miss an hour. Twenty four
  call sites and the only lookup on the site with no cache at all; it
  sits in front of the report route, the wait page, the poll that page
  makes, the running-costs answer and every address check. The
  running-costs answer is cached six hours per postcode and house
  number, tier 1 only: 1.7 million postcodes is the wrong key space for
  the Postgres tier the 2,943 area guides use. Live after: an identical
  repeat went from 3.22 s to 0.35 s, and the wait page from 3.96 and
  5.37 s to 0.69 and 1.16 s.
  3. A report page request no longer starts a gather; the first poll
  does. 144 wait pages against 2 finished reports, each render firing
  ~30 upstream services on an instance that runs two builds at a time.
  Two things found on the way and fixed: the progress entry that says
  "this address is already building" was created on the far side of the
  build semaphore, so while both slots were busy every poll from every
  browser spawned another queued task; and the wait pageview counted
  renders rather than waits, which is why the figure was unreadable. A
  wait now means a client that came back for the answer.
  4. A crawler waits at most 12 s for a cold report, then gets the same
  interim page a person sees. Live: 25.39 s and 21.52 s became 13.3 s
  and 13.2 s. The gather is shielded rather than cancelled so it still
  lands for the next reader, and nothing starts at all while both build
  slots are busy. The report page is noindex, follow either way, so the
  blocking render was buying link discovery, not indexing. The test
  client keeps the full blocking render.
  5. Removal: a page that asks for a postcode no longer asks twice. The
  header box is gone from /running-costs, area guides, council tax pages
  and admissions hubs, which is the homepage's own rule applied to every
  page that earned it. Measured at 375px: header field y=124 everywhere,
  the page's own at y=601 and y=513, and the /running-costs heading rose
  from y=264 to y=199. School pages keep both, and the first reason
  written down for that was wrong: their box is at y=808, inside the
  first screen, not most of a page down. It stays because it asks a
  different question, one postcode against that school's published
  distance answered in place, where the header box runs a full report.
  Two boxes are only one too many when they ask the same thing.
  6. Every council tax figure says who does not pay it. The Manchester
  page gave Band D at 2,312.04 and every band A to H, correctly sourced,
  and never mentioned that one adult alone pays a quarter less. All 350
  council pages, the running-costs page, the report modal and the PDF
  had the gap. Only statutory national rules are stated, with gov.uk or
  mygov.scot named: 25% single person, 50% or nothing for a disregarded
  household, the disabled band reduction, Council Tax Reduction. Empty
  and second-home premiums are deliberately absent because each council
  sets its own. No figure on the site is discounted, because what a
  household pays depends on who lives there and no dataset holds that.
  council_tax.for_district now carries its nation.
  7. The school shortlist is offered at the answer, not above it. Four
  schools across three accounts in ten days, with the button sitting
  above the figure before the reader had a reason to want it. The ask is
  now inside the verdict and carries the checked postcode through
  sign-up; only the saved state stays at the top. If this does not move
  in a fortnight the feature is a removal candidate, on the district
  following and weekly digest precedent.
  8. The funnel a day at a time on /admin. The two existing columns are
  cumulative, so a day that kept its traffic and stopped converting read
  exactly like a day when nothing happened. The answer to "are the quiet
  days real": the flood began at 23:00 on 10 Sep, an hour that went from
  0 to 138 on /running-costs and 0 to 41 on the wait page and has not
  stopped since. Before it, 10 Sep was an ordinary day, 39 report views
  against 32 and 38 on the two days before, with nine visits to the
  signup page and not one account, where 5 to 9 Sep ran between a third
  and a half. 11 Sep is genuinely thin on the human side: 18 homepage
  views, 2 report views and no signup page views at all. So the three
  quiet days are two different things, and the signup-page column is the
  one to watch. Tests 266, smoke 54 against production.

- /admin: people against all views, last 30 days, as one chart (10 Sep
  2026, Michael's ask). The daily people/crawl split the 14-day bars use
  is now computed for 30 days; the bars keep their 14 and every figure
  derived from them is unchanged. All views is a pale area, people a
  single accent line with end markers and end values, one axis, round
  ticks, a hover title per day, a legend naming shapes not colours, and
  the same 30 days as a table underneath. Tokens only: the dataviz
  validator wanted a lighter mark and a chroma-bearing second hue, and
  the design brief's rule that tokens are the vocabulary won, with
  shape and labels carrying identity instead. Scrolls sideways inside
  its own box on a phone. Tests 259.
- /alternatives, the comparison with Propbar, Crystal Roof and Locrating
  (9 Sep 2026, e708c5a, approved by Michael that morning). Four columns
  on one table, every cell from the other company's own public pages as
  read that day, the date on the page, "not listed" never turned into
  "no", and a paragraph per rival on what it does better. What the read
  found: Propbar £49.99 a month, £104.97 for three, £149.94 for six, "47
  risk checks" from "14 official data sources", planning nearby and an
  AI researcher; Crystal Roof free, ten categories of area statistics
  (and it owns StreetCheck); Locrating free basic, Premium £12.99 a month
  recurring, £14.99 single month, £41.99 for three, pupils' homes, feeder
  schools and priority areas. The claim "most comprehensive on the
  market" was declined the same morning: Propbar lists 47 checks to our
  44 and Sprift 300 data points, so it would be false. Re-read the three
  sites and move ALTERNATIVES_CHECKED_ON in main.py whenever anything
  changes; a stale comparison naming rivals is worse than none.
- Every check, side by side (9 Sep 2026). /watchlist/compare/full puts
  the PDF's at-a-glance rows for up to four saved homes next to each
  other, from the same gather the report runs, cached like the report,
  two gathers at a time. Premium, or every home already unlocked; a
  free account sees what it is and no gather runs. A toggle shows only
  the checks that differ, and the count of those leads the page. Entry
  points: a second button on My properties, a call to action under the
  light comparison, and the Premium plan table ("Seven headline
  figures" against "Every check on the report"). The reason to keep
  Premium for the length of a search: nine users had saved nineteen
  homes and only two had paid. Tests 258. Michael declined nothing here;
  the competitor comparison page from the same morning's assessment
  still waits on his yes.
- All eight ideas of the 9 Sep 2026 brainstorm, built the same morning
  (e997bd6, plus a37b714 and 2456c0c fixing two things the live page
  showed). Michael read the eight and said "do all in order", which is
  the approval the two decision-flagged ones needed.
  1. Built, measured, and taken back out the same afternoon. Seven of
  the eight stand; this one did not. The premise was right: the sample
  report the homepage advertises five times was cold at 09:10 UTC, 7.53
  s against 0.29 s warm, because it was warmed only at startup while its
  gather lives an hour. An hourly re-warm loop shipped, and production
  said it does not work. Warmed at 12:41 (0.82 s, then 0.29 s), it took
  13.18 s at 13:01, twenty minutes later and forty minutes inside the
  entry's own lifetime, with no deploy in between. The gather is evicted
  long before it expires: tier 1 is a bounded LRU of 1500 entries and 48
  MB shared with every page, a full gather is hundreds of kilobytes, and
  the crawler idea 2 measured walks hundreds of distinct guides and
  school pages an hour. No cadence survives that, and one short enough
  would hammer 28 upstreams for one link. The loop is gone; the startup
  warm stays but covers the minutes after a deploy, not the hour its TTL
  suggests. Two false starts on the way are worth remembering: a first
  measurement that happened to be fast was a passing visitor, and a
  second was taken 90 seconds after another session's deploy, so any
  timing test here has to check that no deploy landed inside its window.
  Do not re-suggest a warming cadence, on any interval. The open option
  is tier 2, the Postgres cache the area guides use, which survives
  eviction and restart and costs Neon transfer on every miss. That is a
  design change and it waits for Michael.
  2. /admin separates crawl from audience, because the flag was firing
  and the headline still counted the crawl. 8 Sep: 912 views, 494 on
  school and area pages, 479 of those single visits to a distinct page;
  34 school pages inside the minute 05:52 the next morning; the busiest
  human page 74 views against 923 distinct school pages at 7 views each
  at most. Read against people the signup rate is about 1.4% on 5 Sep
  and 1.0% on 8 Sep, so the fall was traffic, not conversion. Each day
  splits into views of pages read more than once and views of pages seen
  exactly once, on the cards, in the bars and in the Telegram line,
  labelled a floor rather than a headcount. The three synthetic paths
  that record events rather than pages (paywall, the 202 wait, the new
  source markers) leave the pageview figures entirely. First live read:
  684 raw today, 156 people, 528 crawl-shaped.
  3. The paywall says something new on a return visit. Twelve accounts
  reached it in seven days and one paid; the one that came back most,
  nine times, has not, one more visit than the person who did. From the
  second visit it counts the visits, counts the properties opened,
  prices two standard searches on each against the £9.99 subscription,
  and names the free report they already own with a link back. No new
  data: unlock, watchlist and prior walls were all already recorded.
  4. The em-dash is gone from user-facing copy: 37 across nine
  templates plus the PDF, the Telegram summary and two sign-in errors.
  Most were a bare dash standing for a missing figure, breaking both
  house rules at once. On /premium the PDF row showed the strongest
  paid difference as a blank; it says "Not included". A test pins
  templates and site JavaScript, so it cannot come back.
  5. Removal: the weekly digest opt-in. Zero of 48 real accounts ever
  ticked it and digest_sent_at was never written, so none was ever
  sent. It was the site's only scheduled send while the change-alert
  email promises mail only when something changed. Gone: two routes,
  the email builder, three watchlist helpers, the UI, its CSS, its
  GitHub workflow and four tests, replaced by one test pinning the
  promise rather than the feature. The two columns stay, as
  saved_districts did. Do not rebuild it.
  6. /running-costs leads with the answer. It became the busiest single
  page on the site on 8 Sep, 79 views against 5 on 6 Sep, and at 375px
  the answer to the postcode just typed sat at y=1465 behind a 200-word
  introduction, the form and a 300px map. The heading is the answer, a
  lead line gives the yearly figure and what it is made of, and on a
  phone the checker is ordered after it: first figure y=1710 to y=810.
  7. The 350 council tax pages can check an address, the one indexable
  family that had no postcode box.
  8. A report start records which page family it began on, from a fixed
  list, through a hidden field, with /admin showing the week against an
  unmarked homepage-or-direct baseline. 923 distinct school pages were
  crawled in three days for at most 7 human views each while report
  starts tracked the homepage, and referrers are deliberately not
  stored, so the question was unanswerable. First read: 375 report
  views this week, all of them unmarked, which is the baseline.
  Two things the live page caught that reading the code did not: the
  flex wrapper that ordered the answer above the checker made the body
  scroll to 492px at a 375px viewport, because a flex item and a 1fr
  track both size to their own content unless told otherwise; and the
  seven-day card would have shown people, crawl and total as three
  numbers that do not add up, the split being calendar days and the
  total a rolling week. Tests 257, smoke 53.

- The catchment map, three steps, 9 Sep 2026 (a11fcdc, 3c3e2b4 carried
  step 2, 002cfb9). The school page already drew the published distance
  as a circle with a postcode checker; Michael approved all three
  upgrades. Step 1: a tick box adds dashed rings for up to six nearby
  schools that publish a distance, with a legend linking each, off by
  default. Step 2: the postcode districts whose centre falls inside are
  labelled on the map at their centres, each opening the district's
  guide. Step 3: /school/{urn}/catchment.png draws the same facts to
  scale with Pillow (ring, school, districts, scale bar, the figure) in
  the share cards' fonts; it is the page's Open Graph image and sits on
  the page as a figure with a download link, so search engines and
  WhatsApp previews see what the JavaScript map shows. Both map branches
  every time (Google on production, Leaflet in dev). Verified: Leaflet
  by screenshot at 1280 and 375, Google by the label elements Chrome
  created on the live page (Google blocks its tiles in test browsers, so
  a grey map there is not a fault). Tests 253.
- All eight ideas of the 8 Sep 2026 brainstorm, built the same night
  (commits 3c3e2b4 and 8719923). Michael read the eight and said "DO
  all 1-8", which is the approval the three decision-flagged ones
  needed.
  1. The valuation is off the critical path of a cold report. Server-
  Timing on production named the cost: nearby-comparables took 4,910,
  4,822 and 4,939 ms on three cold reports, the slowest source every
  time and 1.3 to 1.5 s clear of the next, while 250 of 315 report
  starts in three days went through the wait page. It now runs beside
  the page, cached under its own key, warmed in the background, and
  filled in by /api/property/valuation from the same macros the page
  used (app/templates/_valuation.html). wait_for_amenities became
  wait_for_slow and covers both slow sources, so the PDF still waits
  for everything. Measured after: 5.0, 5.1 and 5.6 s against 6.0, 5.8
  and 6.6 s, so about a second, not the quarter estimated. The gather
  is parallel, so removing the slowest source only buys the gap to the
  next one, and crime, noise and flood zone now set a ~3.4 s floor.
  Those three are the next thing to look at, not this one again.
  2. The 276 outcode private-school pages canonical to their council
  page and left the sitemap. They were 4,860 impressions in three
  months, 46% of the site's, for 4 clicks: seven Birmingham outcodes
  bidding against each other and against /schools/independent/
  birmingham for one town-shaped query. Still live, still linked from
  the guides, school pages and schools guide.
  3. Removal: the two calculators are noindexed and out of the sitemap.
  1,562 impressions at an average position around 90 and no click in
  three months. Both still work and now link the running-costs pages.
  4. School pages lead with the figure. The published distance moved
  from document y=732 to y=461 at 375px, above the fold, with the
  share row below it; the page rounds to two decimals like its own
  title (the third decimal was never the council's precision, since
  1,072 of 3,627 figures carry six decimals from a metres conversion).
  5. Opening a report keeps the property in My properties, said out
  loud with the way out beside it, never overwriting an existing note.
  5 of 46 accounts had ever saved one; both paying accounts had come
  back on another day.
  6. Area guides open with a lead paragraph: figures already further
  down the page, each naming its source, no verdict. Computed outside
  the cached payload so the warm guides picked it up without a re-warm.
  7. /admin lists what is 404ing (path and count, in memory), because
  Search Console reports 1,161 missing pages and will not say which.
  The withdrawn estate directory URLs 301 to the explainer instead of
  dying; that is not the directory coming back, and smoke pins the 301
  so a 200 there would be caught.
  8. /premium shows four real pages of the 25-page PDF above the price,
  for M1 1AE, the sample the homepage already links. Building it found
  two defects in the document itself: the cover printed "M11AE" for
  M1 1AE at -1.6pt of tracking, the same bug fixed on the web report on
  7 Sep, and it printed the postcode twice when there was no house
  number. Brownfield dwelling counts gained their thousands separator.
  Tests 252, smoke 53. Two things noted and not acted on: the PDF says
  "51 checks" where the site says 44, because it counts its own
  checklist rows, and MHCLG spells one council "Bristol UA" in the
  lead, which is the source's own name for it.

- Search Console, second round, 8 Sep 2026 evening (320fefa, c91326c).
  The 404 drill-down was four shapes: 809 outcode-only report URLs the
  area guides themselves linked to (now /#postcode=<outcode>, and the
  route sends a known outcode to its guide with a 301); about 40
  /schools/<website> from 14,180 school websites stored without a
  scheme (an external_url filter on every such link, and a /schools/
  {host} 301 only for a recorded school website); 9 from the withdrawn
  /set-language switcher (301 to the wrapped page); 6 compare pages
  that answer 200. The 149 5xx URLs match the 4 Sep Neon cut-off and
  all answer 200. Michael set Render's Health Check Path to /healthz.
  School pages: 832 profiles carry "varies" as the year, so a quarter
  of the family said "in varies" in title, description, share card,
  FAQ and badge; one helper now gives a year or nothing and a distance
  to two decimals, titles lead with the distance. The admissions hub
  links the twenty school pages at positions 5 to 13 (the near-miss
  list lives in NEAR_MISS_SCHOOL_URNS; refresh it from the weekly
  export). Weekly Monday 9:00 calendar alarm created for the export.
- Search Console follow-up, 8 Sep 2026 (three deploys, d368169,
  caea5b8, 11059e4). The second GSC export showed 557 districts earning
  impressions; the 179 not yet promoted joined GSC_EARNED_OUTCODES, so
  the sitemap grew from 5,455 to 6,165 URLs. School pages, the one
  family that ranks (position 12.7), gained the bus service within
  600 m from the BODS timetables and an embeddable badge at
  /school/{urn}/badge.svg with a copyable snippet, so a school site or
  parents' group can link back. Area guides moved to payload v20 with
  the 7 Sep data: the centre neighbourhood since 2011, the busiest stop
  within 800 m, GP practices with patients per qualified GP and the
  nearest A&E's four-hour figure, and the council's Band D history with
  any EFS or section 114. A council tax page per billing authority
  (/running-costs/council-tax/<slug>, 350 pages) with every band, the
  rank in its nation, six years of rises, the finances record, the area
  guides inside the council and FAQ structured data, linked from the
  listing, the guides and the report modal. Tests 233; smoke 49.
- Idea 9 of nine, price per square metre (7 Sep 2026). The valuation
  already fetched the EPC floor area for up to twenty recent nearby
  sales; the Premium valuation modal now shows pounds per square metre
  at the median and middle half of those sales (each scaled for the
  area's growth like the estimate), this home's own last sale per square
  metre against today's local median, and what its floor area would
  fetch at that rate; the card carries the local figure; the PDF a
  checklist row. HM Land Registry prices over EPC Register areas,
  matched by house number. All nine of the 7 Sep 2026 ideas are now
  shipped and verified live; 44 checks quoted. Tests 230.
- Idea 8 of nine, the grammar school layer (7 Sep 2026, commit
  3ce21db), built instead of the mock exams Michael floated. The DfE
  register marks 163 state secondary schools as selective (GIAS
  AdmissionsPolicy, independents excluded); /schools/grammar lists them
  by council (Kent 32, Lincolnshire 15, Buckinghamshire 13) with Ofsted,
  the council's published last distance offered where we hold one (25
  schools), an account of how selection works and what the address
  decides, and the free familiarisation papers from the three bodies
  that set the tests (GL Assessment, CSSE, Kent County Council). The
  report's State Schools modal shows the grammar schools within about
  ten miles with distance and the published figure; grammar school pages
  link the papers. No dates are stored: registration windows and test
  dates change yearly, so the page sends people to the school's own
  admissions page for them. Tests 228.
- Idea 5 of nine, Since 2011 (7 Sep 2026, commit a33913a). Six ONS
  Census 2011 Key Statistics tables (tenure, age, qualifications, country
  of birth, health, ethnic group; column meanings checked against the
  England totals in each file) re-keyed to 2021 LSOA codes with the ONS
  best-fit lookup (34,753 areas into 34,633; merges summed, the other
  half of a split says it has no comparable figure), in table
  census_2011 via scripts/import_census_2011.py. A free card in Area &
  Community leads with the biggest mover in percentage points; the modal
  sets nine shares for 2011 and 2021 beside England's change over the
  decade (private renting 16.8% to 20.5%), computed from the same tables
  so the comparison is like for like. 44 checks quoted. Tests 225.
- Ideas 3, 4, 6 and 7 of nine (7 Sep 2026, commits 325afc1 to eea247d,
  178bec6, 041c531, 6959f5f). Bus Service (Premium): the DfT Bus Open
  Data Service publishes every operator's timetable as GTFS with no key;
  scripts/import_bus_frequency.py reads the 1.3 GB national feed and
  keeps, for 302,987 stops, the boardable departures on a reference
  Tuesday (daytime, evening) and Sunday, first and last bus and the
  routes; the card leads with the best stop within 500 m in buses an
  hour. Two corrections the raw feed needed: buses and coaches only
  (route types 3 and 200; the feed carries tube, tram, DLR, ferries), and
  the same journey published up to three times (operator plus authority
  datasets, or twice in one), so a departure counts once per stop, route
  and minute (eight passes over the file to fit in memory). Verified:
  Redbridge Central Library takes 1,078 daytime departures from 18 routes,
  90 an hour, and that is real; the 150 and 364 were the copies.
  Health Services (Premium): NHS England Digital list sizes (6,129
  practices, 1 Aug 2026) and fully qualified GP full-time equivalents
  (July 2026), geocoded by postcode, give patients per GP at the three
  nearest practices against the England median of 2,186; NHS England's
  monthly A&E file (121 Type 1 providers, July 2026) with its ICB mapping
  gives the four-hour performance of the trusts in the same integrated
  care board against 61.5% nationally. The council's finances on the
  free Council Tax card: MHCLG's Band D live table (all precepts, every
  billing authority since 1993-94), the exceptional financial support
  list for every year from 2020-21 (51 councils; counties such as East
  Sussex and Norfolk matched by name so their districts see it) and the
  councils' section 114 notices since 2018, flagged when current. Flood
  Re on the free Flood Risk card: homes built in 2009 or later cannot lean
  on the scheme, read from the EPC build date and dwelling type against
  the zone and surface water risk, with the pre-offer step spelled out.
  Postcode lookups now carry the county. 43 checks quoted everywhere. Tests
  223.
- Idea 2 of nine, Development Nearby (7 Sep 2026). Brownfield land
  register sites within 800 m of the address, from MHCLG's planning data
  platform (planning.data.gov.uk, dataset brownfield-land, 37,670 sites),
  which retires the 2025 objection in designations.py that only
  third-party mirrors existed. A Premium card in Planning & Heritage:
  distance, site, hectares, homes where the register states them,
  permission status, ownership, year listed, the council's count on the
  platform and the newest entry nearby; "Register not on the national
  platform" when a council has nothing there (339 authorities mapped by
  GSS code, scripts/import_planning_organisations.py); England only
  elsewhere. Flagged (Check this) when a site has permission, ten or
  more homes or half a hectare; the flag waits behind the lock. PDF:
  Part 6 line and a checklist row. The site now says 41 checks
  everywhere (scripts/bump_check_count.py moves the figure; the
  free/Premium split on the landing and pricing pages is by hand). Real
  check: Bromley holds 88 sites on the platform, Manchester 616; BR6 9AX
  has 13 in a 2 km box. Tests 208.
- Idea 1 of nine, the cost of reaching EPC Band C (7 Sep 2026, commit
  9aea3c7). The new EPC data service (the old epc.opendatacommunities.org
  redirected to it on 30 May 2026) returns a domestic certificate with
  its recommendation report inline: suggested_improvements, each with an
  indicative cost range, a typical yearly saving and the rating after
  that step, cumulative, in the assessor's order. The report sums the
  shortest prefix that reaches 69 (Band C) and shows it on the energy
  card and as a table in the modal, with the crossing step tinted;
  already-C homes get the cost of the remaining suggestions, new builds
  with no measures say so. Measure names come from the codes-info
  endpoint (scripts/import_epc_codes.py, 69 codes in
  app/data/epc_improvement_codes.json). The premium PDF carries the same
  line in Part 4 and a checklist row. Real check: 6 Avalon Road BR6 9AX,
  Band D 57, reaches C after four measures for £12,100 to £26,350. Tests
  204.
- The morning brainstorm, built the same afternoon (7 Sep 2026). Seven of
  the eight ideas shipped in one deploy. The report h1 rendered "M1 1AE"
  as "M11AE" (the -0.02em inherited from the global heading rule closed
  the space, and two adjacent 1s finished it); the postcode now takes the
  mono face and normal tracking, which is what the design brief says data
  gets. The locked PDF button said "PDF report . Premium"; that stray
  full stop is the middot used everywhere else. A retired postcode is no
  longer called a spelling mistake: LS6 2AA and B29 6AA are real
  postcodes Royal Mail withdrew in 2018 and 2010, postcodes.io puts the
  retirement date in the body of its own 404, so the page names the month
  and year and offers the district guide. Still a 404; a postcode that
  never existed keeps the spelling message. M1 1AE is warmed on deploy
  again (one postcode, not the four dropped on 4 Sep): the homepage links
  it five times and it was measured cold at 6.15 s and 5.80 s against
  0.87 s warm, then 1.11 s on the first hit after the deploy. District
  following is gone: saved_districts held zero rows across 43 real
  accounts and six weeks, so two routes, a diff, two UI surfaces, nine
  CSS rules, four tests and a per-user query on every cached area guide
  came out; the model and the empty table stay, because dropping a
  production table cannot be undone. /admin gained two tables that say
  whether one free report per account is holding (addresses unlocked by
  more than one account on a day, free reports by mailbox provider),
  prompted by three accounts on qq.com, hotmail.com and gmail.com
  unlocking OX3 0SG number 7 within 45 minutes on 6 Sep; the page had no
  test at all and now has two. The area-guide prewarm moved to 11:00 to
  15:00 UTC: hourly views peak at 06:00 (163), 22:00 (157), 20:00 (142)
  and 23:00 (127) and fall to 30, 45, 52, 31 and 24 across the UK working
  day, and the job used to run at 07:20 and 19:20 on one worker. Tests
  198.

- Email confirmation, after the report rather than before it (7 Sep
  2026). Michael asked to prevent random addresses taking the free
  report. The evidence argued against a gate: 11 of 12 launch-night
  sign-ups opened a report within minutes, the one duplicate was a real
  buyer comparing two houses via a Gmail plus-alias, and the one typo
  fixed itself in 55 seconds. So the free report never waits. What waits
  is mail: change alerts, the weekly digest and admission updates go
  only to confirmed addresses; a banner asks signed-in accounts to
  confirm, with a resend limited to one every ten minutes; Google
  arrivals count as confirmed. Sign-up now catches mistyped domains
  (gmail.con and twenty others, offering the likely address) and treats
  one Gmail mailbox as one account. The whole thing is dark until
  ALERTS_FROM_EMAIL names a domain verified in Resend, because the
  shared resend.dev sender cannot reach a customer; Michael's step is the
  DNS records. Tests 196. Later that night, live: Resend verified the
  domain at 01:30 (DNS is at GoDaddy, Microsoft 365 receives the
  domain's mail), the first confirmation completed end to end, and
  Michael set the rule: the basic report stays free, the one free full
  report is the reward for confirming, Google arrivals need nothing,
  and the 44 accounts from before that day were marked confirmed once.
  Two secret-gated internal routes show the mail switch and resend a
  link. Tests 197.

- Conversions made visible (6 Sep 2026). Michael asked who the "Unknown"
  subscription statuses on /admin were (his three Stripe test purchases
  from 19 and 21 Aug, which never received a webhook status) and then for
  two things: a section listing every Premium account with the date it
  joined and the date it converted, and a Telegram message for every new
  subscription. `users.premium_since` records the first moment an account
  became Premium by any route (subscription active, pass bought, comp)
  and never moves; db.init_db adds the column on Postgres at startup so
  the deploy needs no hand migration. The Stripe webhook now detects the
  not-Premium to Premium transition, dates it, and sends one Telegram
  line (who, what plan and its monthly value, joined when, days to
  convert, the first property they looked at, link to /admin#premium);
  renewals arrive as the same event with the account already Premium and
  send nothing. The two real subscribers were backdated from Stripe's own
  subscription-created timestamps. Evidence that mattered: the newest
  subscriber came from the Xiaohongshu post, hit the paywall six times
  over 18 hours, and paid for the monthly plan the next afternoon.

- The premium PDF, rebuilt (6 Sep 2026). Michael found the old seven-page
  export "not premium enough" and asked for a document with all forty
  checks and the running costs, something a buyer feels is worth paying
  for. It is now 25 pages in the site's own type (Instrument Sans and
  JetBrains Mono, instanced to static weights for reportlab): a cover
  with the postcode, address, score, verdict and six figures a buyer asks
  first; a contents page; Part 1 with every check on a page (44 rows, each
  with its result, a Fine/Check/Act/Noted reading and its source); then
  value and market, running costs (council tax at every band, the home's
  own EPC energy estimate, stamp duty on the valuation for movers,
  first-time buyers and additional properties, tenure, estate charge,
  renting instead), the property, risk and safety, planning and heritage,
  schools with the published admission distances measured against the
  address, getting around, area and community, the questions to ask, and
  a sources-and-method appendix. Every figure names its source and every
  gap is written in words. Built from the same gather as the live report
  plus the running-costs answer, so the three cannot disagree. The engine
  (xhtml2pdf) fought back for hours; its rules are in memory
  (project_pdf_engine_quirks). Also that day: a reader's figure report
  that Worcester Sixth Form is not a secondary school was right, and 82
  sixth form colleges left the school counts; Welsh reports stopped
  printing "None" for the region and say the school data covers England.

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
- District following (watch an outcode) with visit-time diffs. Removed
  7 Sep 2026, unused by every account. Do not rebuild it.
- Printable viewing checklist; share-a-report with a sender note.
- Free full report offered, not spent (7 Sep 2026): a signed-in free
  account used to spend its one free full report on the first postcode
  it opened, stray or not. Now the report asks ("Use your free full
  report on X?", a pop-up plus the same offer inline above the cards)
  and the spend happens only on a yes, via POST /property/unlock. The
  browser extension no longer claims the report on a listing view; it
  honours an unlock already made on the site.
- "In the News?" card (7 Sep 2026): Michael asked whether a home's past
  (a murder, a suicide, a haunting) could be shown. No source holds it
  (Police.uk blurs to a street segment; no address-level register from
  coroners or the ONS; a news search cannot tell one house on a street
  from another), so the card is the honest version: says what is
  missing, links a street-level news search for the reader to judge,
  and gives the step that binds a seller (the question in writing;
  Sykes v Taylor-Rose 2004). Free, in Risk & Safety, not one of the 40
  counted checks. Do not turn it into a data feature: matching news to
  a house number would mislabel homes and is a defamation risk.

## In progress or queued (do not re-suggest as new)

- The nine content ideas of 7 Sep 2026 all shipped the same day; see
  Shipped for each.

- Report page DOM reduction (21k nodes) - separate session, approved.
- Crime months as "May 2026" not "2026-05" - separate session.
- B2B agency tier: owner emailing the comped agency power user.
- Extension Reddit post: waiting for 2.2.0 store review.
- Google Ads £30-50 experiment: awaiting owner decision.

## Looked at and rejected on the evidence (do not re-raise)

- Letting the first free full report through without email confirmation
  (raised 7 Sep 2026, not built). The finding behind it is real and
  stands: all twelve of the most recent unlocks happened between 1 and 33
  seconds after the account was created, so the confirmation gate will
  land on the exact moment people convert, and it is one DNS record away
  from switching on. But FREE_PREMIUM_UNLOCKS is 1, so "gate from the
  second report onward" means the gate never fires at all, which deletes
  Michael's rule of 7 Sep rather than tuning it. The mitigation already
  exists: the confirmation link carries the property they were on, so the
  round trip returns them there. The measurement is recorded in a comment
  above the banner in base.html. Revisit only if the free allowance
  changes, or with data on how many confirmations actually complete.

## On hold by the owner (suggest only if new evidence)

- The "who manages your estate" directory (league table of agents
  offices, a page per agent, the name search), withdrawn 7 Sep 2026 at
  Michael's word: "the data here seems inaccurate, please remove it for
  now". ESTATE_DIRECTORY_ENABLED = False in main.py: the three routes
  answer 404, nothing links to them, they are out of the sitemap and
  llms.txt; the estate_companies table, the importer and the templates
  are kept. The /estate-charges explainer stays live. Do not switch it
  back on until the registered-office attribution has been checked
  against a sample of companies he approves.

- SchoolAppealHQ, a school admission appeals and EHCP venture (Michael's
  business model v1, August 2026). Assessed 7 Sep 2026: the admissions
  half could sit on our school pages, the EHCP half could not. Michael:
  "hold fire on this first, keep improving on our website first". Do not
  re-raise; the free admissions layer on school pages is also on hold.

- Press pitch outreach (three stories drafted in docs/press/press-kit.md).
  Held again by Michael on 5 Sep 2026 ("hold off"): do not send, do not
  re-raise.
- The £19 one-off pass. Built and leading the pricing page when on sale,
  but STRIPE_PRICE_ID_PASS stays unset by Michael's decision on 5 Sep 2026
  ("hold off"): do not ask again; subscriptions are the only product.
- Weekly GSC export loop (tooling ready, held).
- Trustpilot email invites (held).

## Rejected on principle (never suggest)

- School admission mock exams (11-plus practice papers written by us).
  Raised by Michael 7 Sep 2026, declined: content creation with an
  examiner's error risk, a crowded specialist market, and no fit with a
  due-diligence brand. The grammar school layer links the consortia's
  official familiarisation papers instead.

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
  emails promise "never on a schedule"). Since 9 Sep 2026 the site has
  no scheduled send at all: the weekly digest, the only one, came out
  unused, and a test pins that no route mails a list on a timer.
- Trustpilot scores, stars or review counts rendered by us.
- Features without a reliable official data source (no modelling, no
  estimates): HS2 corridors, rights of way, planning applications
  (until planning.data.gov.uk covers ~50+ councils).
- Submitting all 2,943 districts to the sitemap at once.
- Em-dashes or exclamation marks in user-facing copy.
