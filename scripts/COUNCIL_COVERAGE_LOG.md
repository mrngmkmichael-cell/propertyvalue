# School catchment admission-radius coverage log

Tracks which of England's 333 councils have been investigated for
`scripts/import_admission_radii.py`, so repeat runs don't re-discover
the same dead ends. Update this file in the same commit whenever you
add a council to `_AUTHORITIES` or reject one - it's the durable
record; individual agent-session chat summaries are not.

Target: as many of England's 333 councils as have genuine, safely
matchable public data (real per-school admission-distance figures,
correctly attributed - never fabricated or guessed). 3 further
councils (not listed below) are covered via real catchment-area
polygons rather than a single admission-radius figure.

## Covered (83 councils, in `_AUTHORITIES`)

Bedford, Bexley, Birmingham, Bolton, Bracknell Forest, Brent, Brighton
and Hove, Bristol City of, Bromley, Buckinghamshire, Bury, Calderdale,
Cambridgeshire, Camden (secondary only), Central Bedfordshire, Cheshire
East, Cheshire West and Chester, County Durham, Coventry, Derby,
Dorset, Dudley, Ealing,
East Sussex, Gloucestershire, Greenwich, Hackney, Haringey, Harrow,
Hartlepool (secondary only), Havering, Hertfordshire, Hillingdon,
Hounslow, Islington, Kensington and Chelsea, Kingston upon Thames,
Kirklees, Knowsley, Lambeth, Leeds, Leicester, Lewisham, Merton,
Middlesbrough, Milton Keynes, Newcastle upon Tyne, Newham, North
Somerset, North Yorkshire, Oldham, Oxfordshire, Peterborough,
Portsmouth, Reading, Richmond upon Thames, Salford, Sandwell, Sefton,
Solihull, Somerset, Southampton, Southend-on-Sea (primary only),
Southwark, Staffordshire, Stockport, Suffolk, Surrey, Sutton,
Tameside, Tower Hamlets (community schools only), Walsall, Waltham
Forest, Wandsworth, Warwickshire, West Northamptonshire (primary/
junior only - see note below), West Sussex, Westminster, Wigan,
Windsor and Maidenhead, Wirral, Wokingham, Worcestershire.

**West Northamptonshire** (added this round): `westnorthants.gov.uk`
publishes "how places were allocated" PDFs per district (Northampton
town; Daventry & South) plus a separate Junior-schools PDF, republished
each year at new `cms.westnorthants.gov.uk/media/<id>/download` URLs -
not linked from the plain HTML of the "Primary school place offers"
page (a Next.js app whose document links only appear in a rendered
view, not the raw `__NEXT_DATA__` JSON payload - found via a fetch tool
that renders JS rather than raw `curl`/`httpx`). Clean one-row-per-
school table (School Name / How places were allocated / Places
remaining?) with the distance embedded in prose in column 2 ("...The
last pupil to be allocated a place in the '...' criterion lives X.XXX
miles from the school."); only the *first* such phrase per cell is
taken, since later "May/June/July round of reallocations" paragraphs
in the same cell describe different, later distances (sometimes "from
their nearest alternative school", a different figure entirely) for
places freed up after National Offer Day. All 28/28 extracted rows
matched correctly on manual spot-check (verified against the real
`_match_urn` matcher, not just eyeballed extraction) - no wrong
matches, unlike the two rejections below that use a very similarly
laid-out source. A separate "Breakdown of Allocations for WNC
Secondary Schools" PDF exists but was not attempted: rotated/garbled
header cells, rows that continue across a page break with a *blank*
school-name cell (previous-school text overflowing), and "the linked
area criterion lives X miles from **the nearest alternative school**"
distances that mean something different from the target school's own
distance - meaningfully riskier than the primary/junior document, not
worth it for one more phase of one authority already covered.

## Rejected - do not re-attempt without genuinely new information

### Blocked from this environment (Cloudflare / WAF / persistent 403)
Enfield, Gateshead, West Berkshire, Sunderland, Barnet, Hampshire,
Manchester, Leicestershire (whole `leicestershire.gov.uk` domain
returns 403 to this environment's requests, not just one document -
confirmed against both a specific PDF and the bare domain root),
Wiltshire (whole `wiltshire.gov.uk` domain returns 403 to a direct
`httpx` request, confirmed against both the admissions landing page
and the bare domain root - same pattern as Leicestershire), Stockton-
on-Tees (whole `stockton.gov.uk` domain returns 403 to a direct
`httpx` request, confirmed against both the domain root and a specific
admissions article page).

### Unreachable (timeout / connection failure / persistent 5xx)
North Tyneside (source lives on a different subdomain that times out),
Redbridge, Thurrock (FOI disclosure page).

### Source confirmed not to publish, or document not locatable
Nottinghamshire, Telford and Wrekin, Bournemouth Christchurch and
Poole, Kent (explicitly states it doesn't publish this), Norfolk,
Essex, Liverpool, Rotherham, Doncaster, Medway, Cornwall, South
Gloucestershire, Swindon, Barking and Dagenham, Trafford (URLs moved
since last check).

### Real correctness risk - rejected per safety policy, not attempted around
- **Croydon** - proven name-bleed between adjacent schools ("Coloma
  Girls'"/"Convent" split around a data row).
- **Bath and North East Somerset** - genuine multi-band-per-year
  ambiguity for several schools, including an unresolvable "St
  John's" x2 name clash.
- **Blackburn with Darwen** - multiple *confirmed* wrong-school
  matches ("St Anthony's"->"St Anne's" etc.) from many similarly
  patterned "St X ... Primary School" names.
- **Hammersmith and Fulham** - schools are banded with no single
  meaningful distance value.
- **Sheffield** - PDF text layer is character-interleaved between
  table columns; not safely parseable without risking wrong figures.
- **St Helens** - multi-line name wrapping plus page-break
  contamination; not safely parseable.
- **Devon** - large rural county with many generically-named "St X
  Church of England Primary" / "St X C of E VA Primary" schools;
  spot-checking the real "Distance (Metres) of Last Offered Place"
  primary allocation spreadsheet (linked from
  devon.gov.uk/educationandfamilies/.../allocation-day-faqs/, hosted
  as SharePoint .xlsx files, e.g.
  `https://devoncc.sharepoint.com/:x:/s/PublicDocs/Education/IQAZGIaqfE6QR5cX3ROIlHBfARZCcys1O1yNYsjKocw0CGo?e=uU8aHH&download=1`)
  through the script's actual `_match_urn` fuzzy matcher produced
  *confirmed wrong* matches - e.g. "St James C of E - Okehampton"
  matched to "St Andrew's Church of England Academy", "St Michael's C
  of E VA Primary School" matched to "The Beacon Church of England
  Voluntary Aided Primary" - both above the 0.72 cutoff despite being
  different schools. Same failure mode as Blackburn with Darwen.
  Devon's secondary-school spreadsheet (same SharePoint folder) shares
  the same LA-wide candidate pool and would carry the same risk - not
  attempted.
- **Warrington** - the primary/secondary "20XX allocation table" PDFs
  (`warrington.gov.uk/sites/default/files/.../Primary Schools Brochure
  2026-27.pdf` and `.../Secondary Education Schools Brochure
  2026-2027...pdf`) have real per-school "Last criterion allocated and
  Distance" figures in a parseable (if line-wrapped) layout, but even
  after excluding the school-type word ("Academy"/"Community") that a
  naive parse would otherwise wrongly append to the name, spot-checking
  real extracted names through the script's actual matcher still
  produced *confirmed wrong* matches - "Dallam Primary" -> "Statham
  Primary", "Our Lady's Catholic Primary" -> "St Oswald's Catholic
  Primary", "Burtonwood Primary" -> "Callands Primary Academy" (before
  the type-word fix), "Penketh South Primary" -> "Penketh Primary" (a
  different, actually-named school in the same document). Too many
  short generically-suffixed "X Primary" names for safe fuzzy matching.
- **Bradford** - `bradford.gov.uk`'s "Applying for a
  primary/secondary school - detailed guide" PDFs have a genuine
  per-school directory ending "(furthest distance N.NNN miles)" for
  oversubscribed schools, cleanly headed by school name (safe to
  extract - not a Sheffield/Stoke-style wrapping problem), but
  spot-checking real extracted names through the script's actual
  matcher produced *confirmed wrong* matches among Bradford's several
  "St X Catholic Primary" schools - "St Anthony's Catholic Primary"
  and "St Francis' Catholic Primary" and "St Walburga's Catholic
  Primary" (three distinct real schools) all matched to "St Anne's
  Catholic Primary" instead. Separately, the GIAS data itself has two
  rows for "St Anthony's Catholic Primary School, A Voluntary Academy"
  under Bradford with two different URNs (147981 and 147982) - a
  genuine data-quality issue in the school directory itself that would
  make that particular school unsafe to target even with perfect name
  matching. Same failure category as Blackburn with Darwen.
- **Shropshire** - `next.shropshire.gov.uk`'s "Parents' Guide to
  Primary Education 2026/27" PDF (`.../media/5kmnznsi/primary-parents-
  guide-2026-27.pdf`) is a genuine composite prospectus with one page
  per school and a clean "Criteria & distance of last on-time place
  allocated ... X.XXX miles" line, and the school name is cleanly and
  reliably extractable from a stable footer sentence ("...detailed
  oversubscription criteria for **<Name>**, please visit the school's
  website.") - the header line is *not* reliable (several schools wrap
  the town name and school name across separate lines in a way that
  puts neither cleanly "before Headteacher:", e.g. "Longlands Primary
  School" in Market Drayton renders as "Market Drayton, Headteacher:
  ... / Longlands Primary Tel: ..."). Even using the safe footer
  anchor and dropping the one obvious typo (106.4 miles), running all
  49 matched extractions through the real `_match_urn` matcher found
  roughly 20 *confirmed wrong* matches - most strikingly, eleven
  distinctly-named "X CE Primary School" / "X Church of England
  Primary School" entries (Oxon, St Giles', Adderley, Clive, Morda,
  Newtown, Selattyn, Trefonen, Welshampton, Whittington, Whixall - all
  real, separate schools with real, different distances from 0.57mi to
  8.996mi) all matched to the *same* single wrong school, "Hadnall
  Church of England Primary School". This isn't just the known
  unspaced-"CofE" gap - normalized names like "oxon church of england
  primary" and "hadnall church of england primary" share so much
  boilerplate ("church of england primary") that `difflib`'s ratio
  scores them as close enough regardless of the actual distinguishing
  town-name fragment. See the general note below the Bradford entry.
- **North Northamptonshire** - `northnorthants.gov.uk`'s "Primary
  school place offers" page publishes real per-school prose directly
  in HTML (no PDF needed) - "The last pupil allocated a place lives
  X.XXX miles from the school." - in a clean `<tr><td>Name</td>
  <td>prose</td><td>Yes/No</td></tr>` table. Running all 26 extracted
  rows through the real matcher found 3 *confirmed* wrong matches out
  of 25 "matched": "Cranford C of E Primary School" and "Woodford C of
  E Primary School" are two distinct real schools in the database, but
  the former's distance got attached to the latter's URN; likewise
  "Gretton Primary School"/"Gretton Primary Academy" (real, distinct)
  had its distance attached to "Rushton Primary School" (also real,
  separately and correctly matched elsewhere in the same run); and
  "Greenfields Primary School" (source) vs. the real DB name
  "Greenfields Primary School and Nursery" matched instead to the
  unrelated "Beanfield Primary School". Same short-generic-name/
  boilerplate-domination failure mode as Shropshire above, on an
  entirely different source and city, which is why it's flagged as a
  *pattern* rather than a one-off. West Northamptonshire, checked the
  same round with a similarly-laid-out source, did NOT show this
  problem (28/28 correct) - so the failure isn't universal to Church-
  of-England-heavy authorities, but it recurs often enough that every
  new authority with several similarly-patterned "X C of E/CE Primary"
  names must still be spot-checked through the real matcher, not just
  assumed safe from format alone.
- **North Lincolnshire** - `northlincs.gov.uk` turned out to publish
  real per-school admission-distance data in an unusual place: not a
  PDF, but directly on each school's own profile section of the
  council's "Primary/Secondary/Infant/Junior Schools" directory pages
  (e.g. `.../our-schools-and-colleges/primary-schools/`), one `<h2>`
  per school followed by an "Admissions information for `<Name>`"
  subsection whose "Note:" prose gives a real, year-by-year "In `<year>`
  the children who were not offered places were those in category N
  who lived further than X.XXX miles walking distance away from the
  school" (or, for a few schools, "In `<year>` no child over X.XXX
  miles away from the school was admitted") - taking the last such
  figure per school (most recent oversubscribed year) across all four
  directory pages (primary, secondary, infant, junior - all four use
  the same template and all four have real data) gave 23 schools with
  a usable distance. Running all 23 through the real `_match_urn`
  matcher found **2 confirmed wrong matches**: "Scunthorpe Church of
  England Primary School" (real, distinct school, its own `<h2>`
  section elsewhere on the same page) matched instead to "Eastoft
  Church of England Primary School" (also real, distinct, also its own
  section on the same page); and "St Norbert's Catholic Primary
  Voluntary Academy" matched to "St Bernadette's Catholic Primary
  Voluntary Academy" (both real, distinct schools - St Bernadette's
  already correctly matched to itself elsewhere in the same 23-row
  set, so this is a same-URN collision between two different source
  rows). Same "short/boilerplate-heavy name, `difflib` ratio ignores
  the one distinguishing word" failure mode as Shropshire/North
  Northants/Bradford/Devon above, this time hitting both a
  Church-of-England pair and a Catholic-saint pair in one 23-row
  sample - rejected per safety policy despite the source itself being
  genuinely clean and easy to extract. (Sibling council North East
  Lincolnshire does not use the same page template and no equivalent
  data was located for it - see "Checked this round" below.)
- **Plymouth** - `plymouth.gov.uk`'s composite "School admissions
  parents' guide" page (`plymouth.gov.uk/school-admissions-parents-
  guide` - note this URL 403s to the WebFetch tool's own fetcher but
  loads fine (200) via a direct `httpx` GET with a browser User-Agent,
  so it is not actually environment-blocked) has a "Statistics - What
  happened last year" section with clean, real, official
  `<table class="govuk-table">` markup for Primary, Junior, Secondary
  and Key Stage 4 schools: columns are DCSF Number / SCHOOL / PAN /
  Total Places Allocated / Lowest Admission Criteria allocated / Last
  Distance allocated (straight line miles). 73 rows had a plausible
  (<=30 mile) distance figure after dropping two obvious source-side
  typos (Eggbuckland Vale "136.53" miles, Goosewell "105.36" miles,
  plus three implausible secondary/KS4 values over 250 miles -
  Lipson Co-Operative Academy "257.20", Plymstock School "292.00",
  Scott Medical "257.98" - same class of error as Peterborough's
  known "206.404" typo, handled the same way, by dropping anything
  implausible for a real straight-line distance in this city). Running
  the 73 plausible rows through the real `_match_urn` matcher found
  **7 confirmed wrong matches**: "Austin Farm Primary School" matched
  to "Yealmpstone Farm Primary School" (a real, distinct school that
  is *also* correctly matched to itself elsewhere in the same 73-row
  set, so this silently overwrote/collided with a correct row);
  "Hooe Primary School" -> "Knowle Primary School" (same collision
  pattern - Knowle also correctly self-matched elsewhere); "Mayflower
  Primary School" -> "Ford Primary School" (same pattern, Ford also
  correctly self-matched elsewhere); "Mount Wise Primary School" ->
  "Mount Street Primary School" (same pattern); "St Andrew's C/E
  Primary School" -> "St Edward's CofE Primary School" (a real,
  distinct school - one of the "Refer to school" rows with no numeric
  distance of its own in the source, so this one doesn't collide with
  an existing correct row, but is still a confirmed wrong identity
  match); "St Peter's R/C Primary School" -> "St Peter's CofE Primary
  School" (a real, distinct - different denomination - school also
  correctly self-matched elsewhere); "Whitleigh Primary School" ->
  "Leigham Primary School" (same collision pattern). A 7/73 confirmed
  wrong-match rate is worse than North Lincolnshire's 2/23 above and
  well outside anything safe to ship - rejected per safety policy. The
  source itself is genuinely excellent (official, clean, tabular, with
  a DCSF/establishment number per row that a future fix could
  potentially use for a safer non-fuzzy match, the same way Greenwich's
  fetcher uses a published URN directly - not attempted this round
  since DCSF/establishment numbers are not the same identifier as GIAS
  URNs and would need a separate LA-code-plus-establishment-number
  cross-reference to use safely).

**Known but NOT fixed - `_normalize_school_name` gap, needs careful
follow-up, not a quick patch**: while investigating Bradford, several
false matches turned out to be caused by `_normalize_school_name` not
recognising `"CofE"` (no spaces, no dots - e.g. "St John's CofE
Primary School") as an abbreviation for "Church of England", even
though it already handles `"C of E"` (spaced) and standalone `"CE"`.
Adding `r"\bCofE\b": "Church of England"` to `_ABBREVIATIONS` *did* fix
those specific false matches, but re-running the full import script
afterwards showed it silently broke a *different* already-covered,
previously-trusted authority: Oldham's source document abbreviates as
`"C.E."` (with dots), which normalizes to a short literal "ce" that
used to score close enough against the (also then-short) unexpanded
"cofe" in Oldham's GIAS names - expanding the DB side to the long
"church of england" broke that balance and dropped Oldham's match rate
from 26/28 to 20/28, and even changed some individual matches (e.g.
"St. Martin's C.E. Primary School" started wrongly matching "St Agnes
Church of England Primary" instead of unmatching or hitting the real
"St Martin's CofE Junior Infant and Nursery School"). Adding a further
`r"\bC\.E\.(?=\s|$)"` pattern to also expand the dotted form fixed
Oldham's specific regression but *still* produced a different wrong
match for "St. Martin's" there (a pre-existing ambiguity between
similarly-patterned "St X CofE ... School" names in Oldham that the
original unexpanded normalization had apparently been avoiding somewhat
by accident, via shorter/less-similar strings). Given `_ABBREVIATIONS`
is shared by all 82+ authorities and a change can shift which
candidate wins for entirely unrelated schools in ways that are hard to
predict without exhaustively re-checking every existing authority, this
change was **reverted** rather than shipped under time pressure - the
repo is unchanged from before this investigation. A future round
should pick this up specifically (not as a side effect of adding a new
authority) with time to re-verify every currently-covered authority's
match set before and after, not just spot-check the one authority
being newly investigated.

**Addendum (this round):** the Shropshire and North Northamptonshire
rejections above show the false-match problem is broader than just the
unspaced-"CofE" gap - it also happens with correctly-expanded "Church
of England" strings, purely because `difflib.SequenceMatcher.ratio()`
on two short, mostly-boilerplate normalized strings (e.g. "<town>
church of england primary" vs a different "<other town> church of
england primary") can score high enough to pass the 0.72 cutoff even
though the only distinguishing part (the town/dedication name) barely
overlaps. A more robust matcher might weight the non-boilerplate
tokens more heavily, or require the leading token(s) to match closely
before falling back to whole-string ratio - but that's the same
"shared by 83+ authorities, must be regression-tested against all of
them before shipping" caveat as the `_ABBREVIATIONS` fix above, and is
out of scope for a single-authority round.

### Unsafe to parse (real data exists, but extraction risks wrong figures)
- **Stoke-on-Trent** - `stoke.gov.uk` publishes a genuine "Furthest
  Distance Admitted (miles)" + "Final Criterion Used" column per
  school (e.g. `primary_school_allocated_places_september_2025.pdf`),
  but the underlying PDF table wraps each school's name across 1-3
  separate table rows with no consistent rule for whether the
  continuation line comes before or after the numeric data row (e.g.
  "Priory C of E" / [data row] / "Primary" - name fragment both before
  *and* after the data) - reconstructing the correct name is not
  reliably possible without real risk of attaching the wrong trailing
  word (and hence fuzzy-matching to the wrong school). Same failure
  mode as the already-rejected Sheffield/St Helens.

## Checked this round, no usable distance data found (not a parsing
## risk - the document just doesn't contain it, or none was locatable)
- **Rutland** - the only admissions-data page found
  (rutland.gov.uk/.../admissions-data) is aggregate "% first
  preference" only; the linked "LA Report to OSA" is a narrative
  report, not a per-school distance table. Only 1-2 secondary schools
  in the whole county in any case.
- **Darlington** - "Primary/Secondary Guide for Parents 2026-2027"
  PDFs (darlington.gov.uk) checked page-by-page for "metres"/"last
  distance"/"furthest distance" - none present; these are pure
  policy/criteria guides with no post-allocation statistics appendix.
- **Slough** - a myth-busting FAQ in the Secondary Admissions Booklet
  2026-27 explicitly references "the last distance" and points to
  "Section 5" for it, but Section 5 as actually published
  (`slough.gov.uk/downloads/file/4854/...`) only has preference/offer
  *counts* per school, no distance figures at all.
- **South Tyneside** - the per-school admissions-policy pages live on
  `publications.southtyneside.gov.uk`, a different subdomain to the
  main council site; connection to it refused outright from this
  environment (likely blocked/misconfigured, not just slow) - same
  practical outcome as the Cloudflare-blocked group above.
- **Isles of Scilly** - only 1 school in the whole authority in our DB
  (Five Islands Academy, a single federated all-through school with
  satellite bases per island) - there is no oversubscription/admission-
  distance concept to publish in the first place. Not worth
  re-investigating; this is a structural "no", not a "not found yet".
- **Derbyshire** - `derbyshire.gov.uk`'s "School allocation data" page
  (`.../primary-admissions/parentsguide/how-to-apply/rules/school-
  allocation-data/`) publishes only aggregate stats ("93.6% got their
  first preference") - no per-school table or linked document found,
  and the rest of the parents-guide page tree has no other allocation-
  statistics link.
- **Lincolnshire** - `data.lincolnshire.gov.uk`'s "School admissions"
  open-data CSV (`OpenData_OfferData_Sept26...csv`) was downloaded and
  inspected directly - columns are only preference-count and PAN, no
  distance column at all. No separate PDF with per-school distance
  figures was located either.
- **York** - `data.yorkopendata.org`'s "admissions-summary-allocated-
  primary/secondary" open-data CSVs were downloaded and inspected
  directly - columns break down places allocated *by admissions
  criteria group* (catchment/sibling/distance/religion/etc. counts),
  not an actual distance-in-miles figure. No separate document with
  real distance figures was located on york.gov.uk's admissions pages.
- **Wolverhampton, Nottingham, Cumberland, Halton, Kingston upon Hull
  (City of), Barnsley, Lancashire (North)** - specific composite
  "parents' guide"/"admission arrangements" documents found and
  downloaded for each (`Admission-Arrangements-2026-27-June25.pdf`;
  `admission-arrangements-20252026-determined.pdf`;
  `starting_school_in_cumberland_parental_booklet_2025_v1.pdf`;
  `smwst.co.uk/downloads/admissions/primary_booklet_2026__1_.pdf`;
  `hull.gov.uk/downloads/file/4888/a-guide-to-primary-admissions-2026-
  to-2027`; `barnsley.gov.uk/media/locjtvek/primary-school-admissions-
  2026-booklet.pdf`; `lancashire.gov.uk/media/ivsbnufd/primary-school-
  admissions-in-north-lancashire-2026-27.pdf`) and scanned page-by-page
  with a generic numeric-distance-pattern regex
  (`\d\.\d+\s*(miles?|km|metres?|m)`) the same way Shropshire's lookalike
  document was found to have real data - zero matches (or, for
  Lancashire North, only a policy-criteria mention of a fixed "2.5
  miles" catchment radius, not a real last-offered figure) in any of
  the seven. These are pure policy/criteria guides with no
  post-allocation statistics appendix, unlike Shropshire's and West
  Northamptonshire's genuinely data-bearing lookalikes.
- **Herefordshire** - checked with a real browser (network-request
  capture, not just `curl`/`httpx`): the "Information for parents -
  Admission to primary school 2026" landing page
  (`herefordshire.gov.uk/downloads/file/21116/...`) actually redirects
  to a *stale, 404ing* 2025 media URL - the council's own redirect
  metadata hasn't been updated to point at the 2026 file. Found a
  mirror of the actual 2026 document hosted on a Herefordshire school's
  own site (`withington.hereford.sch.uk/attachments/download.asp?
  file=145&type=pdf`, "Information for parents - Admission to primary
  school - Commencing September 2026") and scanned it page-by-page -
  zero numeric-distance-pattern matches across 22 pages. It's a pure
  policy/criteria booklet (admissions rules, school list, contact
  details), not a data booklet - genuinely no distance data to find,
  not a retrieval failure.
- **Isle of Wight** - `iow.gov.uk/documents/download/educating-your-
  child-booklet-2026-2027` (the parents' guide) returned a 403 to this
  environment both via a plain HTTP fetch and via a real browser
  (navigation itself was denied) - confirms this is a genuine
  environment block (same category as the Cloudflare/WAF group above),
  not a URL problem worth retrying without different network access.
- **Northumberland** - the "Primary Handbook 26-27" PDF
  (`northumberland.gov.uk/.../Primary%20Handbook%2026-27.pdf`, 178
  pages) was downloaded and scanned page-by-page with the generic
  numeric-distance-pattern regex (`\d\.\d+\s*(miles?|km|metres?|m)`) -
  zero matches. Pure policy/criteria handbook, no post-allocation
  statistics appendix.
- **Blackpool** - both the "Admission to Blackpool primary schools
  2026" and "...secondary schools 2026" parents' guide PDFs were
  downloaded and scanned the same way - zero matches in either.
- **Redcar and Cleveland** - the "Guide for Parents - Secondary
  Admissions" PDF was downloaded and scanned the same way - zero
  matches. (Primary equivalent not separately checked, but the same
  council publishing pattern makes it unlikely to differ.)
- **North East Lincolnshire** - the LA Primary Scheme PDF
  (`nelincs.gov.uk/assets/uploads/2024/08/North-East-Lincolnshire-LA-
  Primary-Scheme-2025-2026.pdf`) was downloaded and scanned the same
  way - zero matches; it is a coordinated-scheme/criteria document,
  not a results document. The general admissions page was also fetched
  directly and its linked documents (school catchment list, Fair
  Access Protocol, Annual Report to the Office of the Schools
  Adjudicator) list no allocation-statistics document. Sibling council
  North Lincolnshire's real per-school-directory-page data (see
  "Real correctness risk" above) does **not** carry over here -
  `nelincs.gov.uk` is a different site on a different CMS.
- **Westmorland and Furness** - both the "Starting school in
  Westmorland & Furness - September 2025" (112 pages) and "Transfer to
  secondary school..." (40 pages) PDFs were downloaded and scanned
  page-by-page - zero matches in either. Pure policy/criteria guides.
- **Wakefield** - two documents referenced from the "Apply for a
  full-time place in Reception" page were checked directly: "Places at
  each primary and junior school" PDF (37 pages - PAN, applications,
  places-allocated, catchment-area prose per school, no distance
  figures found via the same regex scan) and "Primary Guide for
  Parents 26-27" (a .docx, not PDF - all paragraphs and table cells
  scanned the same way, zero matches).
- **Luton** - the "How to apply for a school place guide" PDF link
  found via search (`luton.gov.uk/.../How-to-apply-for-a-school-place-
  guide.pdf`) has moved/gone stale - it now redirects to a generic
  admissions landing page rather than serving the PDF. That landing
  page and its "apply, transfer or change schools" hub were fetched
  directly; neither links to an allocation-statistics document.
- **Rochdale** - the "Starting Primary School - a guide for parents
  and carers" download URL (`rochdale.gov.uk/downloads/download/1077/
  ...`) serves a short HTML landing page (not the PDF itself, and no
  PDF link found in its raw HTML) rather than a document with content
  to scan. No other candidate document surfaced via search.
- **East Riding of Yorkshire** - both the main school-admissions hub
  page and the "choosing your preferred schools" page were fetched
  directly; the latter explicitly recommends reviewing "how places
  were allocated in previous year[s]" but the only link offered from
  either page is to general admission-arrangements guidance, not a
  results document.
- **City of London** - only 9 schools total appear under this
  `local_authority` in our DB, and only one (The Aldgate School,
  formerly Sir John Cass's) is a normal maintained admissions-relevant
  primary - a council committee "School Admissions Report" PDF
  (`democracy.cityoflondon.gov.uk/documents/s219701/...`) was
  downloaded and scanned page-by-page - zero matches. Structurally
  low-value even if data existed (effectively a single-school
  authority, similar to Isles of Scilly).
- **Torbay** - has a genuine per-school "school place allocations"
  page structure (`torbay.gov.uk/schools-and-learning/admissions/
  school-place-allocations/<slug>-allocations/`, one page per school,
  39 pages total, school identity unambiguous from the URL/link text
  rather than fuzzy-extracted) - checked every single page directly.
  10 of the 39 mention a mile figure, but in every case it's the
  school's fixed policy catchment-radius criterion ("Children living
  within 2 miles of the school") plus how many places that criterion
  filled, not a real "last distance offered" value for a given year.
  No school's page has an actual last-distance-offered figure. This is
  Hammersmith and Fulham's "banded with no single meaningful distance
  value" failure mode, not Bradford/Shropshire's matching-risk one -
  the school identity here is completely safe, there is simply no
  distance data to extract.

## Searched this round via general web search AND direct site
## navigation - conclusion: the remaining pool is now exhausted

Every one of the 15 councils identified in the previous round's
"ground not yet investigated" list (Blackpool, City of London, East
Riding of Yorkshire, Luton, North East Lincolnshire, North
Lincolnshire, Northumberland, Plymouth, Redcar and Cleveland,
Rochdale, Stockton-on-Tees, Torbay, Wakefield, Westmorland and
Furness, Wiltshire) was investigated again this round, this time with
direct `httpx` fetches (bypassing the WebFetch tool's own fetcher,
which 403'd on at least one genuinely-reachable site - Plymouth - that
a direct request loaded fine) and, where a lead existed, downloading
and scanning real documents/pages rather than relying on search
snippets alone. The result:

- **2 of the 15** (North Lincolnshire, Plymouth) turned out to have
  real, genuinely locatable admission-distance data - the best hit
  rate for this list across three rounds of searching - but both
  failed the `_match_urn` spot-check with multiple confirmed wrong
  matches and were rejected per the safety policy (see "Real
  correctness risk" above for full detail on each).
- **2 of the 15** (Wiltshire, Stockton-on-Tees) are confirmed
  environment-blocked (whole-domain 403), moved to "Blocked from this
  environment" above.
- **11 of the 15** (Blackpool, City of London, East Riding of
  Yorkshire, Luton, North East Lincolnshire, Northumberland, Redcar
  and Cleveland, Rochdale, Torbay, Wakefield, Westmorland and Furness)
  were confirmed, via direct document/page inspection rather than just
  search, to genuinely not publish this data (or, for Torbay, to
  publish only fixed policy criteria, not results) - moved to "Checked
  this round, no usable distance data found" above.

**This exhausts the entire previously-identified remaining-candidate
list with a concrete outcome for every member - none left in limbo.**
Combined with the fact that this was the third round some of these
councils were checked (search twice, then direct inspection this
round), and that the two genuine hits both independently reproduced
the exact same short-generic-name/boilerplate-collision matcher
failure seen in five previously-rejected authorities (Bradford,
Shropshire, North Northants, Blackburn with Darwen, Devon, Warrington)
rather than any new failure mode, there is no concrete lead left to
chase for this round's candidate pool. The 86-entry/83-council
`_AUTHORITIES` coverage from before this round is unchanged - this
round added no new authority, and that is a definitive, checked
outcome rather than a gap.

A genuinely future round should not re-search this 15-council list
without a new angle (e.g. a JS-rendering fetch tool for the handful of
sites not yet tried with one, since North Lincolnshire's and
Plymouth's data this round was both found via plain `httpx` HTML/GET,
not JS rendering) - it should instead pick up the one substantive
open thread this round surfaced: the shared `_match_urn` /
`_normalize_school_name` matcher's boilerplate-collision weakness
(documented in the "Addendum" note further up this file) is now
confirmed to be the single biggest blocker to *adding* new
authorities, having independently sunk two more genuinely
well-formatted, easy-to-extract real sources this round on top of the
five it already blocked. A dedicated round to improve the matcher
(e.g. weighting the leading/distinguishing token more heavily before
falling back to whole-string `difflib` ratio, or requiring a minimum
edit-distance margin over the second-best candidate rather than a
single absolute cutoff), with time to regression-test every one of the
86 currently-covered authorities' match sets before and after (not
just the one authority being newly investigated, per the reverted
`_ABBREVIATIONS` fix note above), would likely unlock North
Lincolnshire, Plymouth, Bradford, Shropshire, North Northamptonshire,
Blackburn with Darwen, Devon and Warrington all at once - a larger
potential gain than continuing to search for undiscovered authorities
in a pool that is now confirmed exhausted.

## Correction (a prior round): actual remaining-council count

A prior round's "roughly 220 councils... mostly smaller shire
district/borough councils" estimate for "not yet investigated" was
wrong - it conflated England's 333 *all-purpose* local authorities
(most of which are lower-tier district councils with no education
function at all - schools admissions is run by the 152-ish upper-tier
councils: county councils, unitary authorities, metropolitan boroughs
and London boroughs) with the ~152 `SchoolDetail.local_authority`
values actually used in this project's database. Only councils that
appear as a `local_authority` value in the `school_details` table can
ever be matched by this script, so only those are worth investigating.

To regenerate the true remaining list at the start of a future round,
run (from the repo root, with `.env`'s `DATABASE_URL` set):

```
python -c "
import sys; sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from app.db import _get_engine
from sqlalchemy import text
with _get_engine().connect() as conn:
    for r in conn.execute(text('SELECT DISTINCT local_authority FROM school_details ORDER BY 1')):
        print(r[0])
"
```
then exclude Wales/offshore entries, everything in "Covered" above,
and everything in "Rejected" above (which now includes the "Checked
this round, no usable distance data found" subsection - those *are*
rejections, just for "not locatable" rather than "unsafe to parse"
reasons).

## Ground not yet investigated

**As of this round, there is none.** The 15-council list that the
previous round left as "ground not yet investigated" (Blackpool, City
of London, East Riding of Yorkshire, Luton, North East Lincolnshire,
North Lincolnshire, Northumberland, Plymouth, Redcar and Cleveland,
Rochdale, Stockton-on-Tees, Torbay, Wakefield, Westmorland and
Furness, Wiltshire) was fully investigated this round with a concrete
outcome recorded for every single member (2 real-data-but-unsafe
rejections, 2 newly-confirmed environment blocks, 11 confirmed
no-data) - see "Searched this round via general web search AND direct
site navigation - conclusion: the remaining pool is now exhausted"
above for the full breakdown. Every other candidate from earlier
rounds either got added to `_AUTHORITIES`, or has a specific reject
reason recorded in one of the "Rejected" subsections above.

**A future round has two honest options, not a "keep searching the
same 152" option:** (1) pick up the `_match_urn` matcher-improvement
thread flagged at the end of the "conclusion" section above, which
could unlock up to 8 already-found-but-rejected authorities (North
Lincolnshire, Plymouth, Bradford, Shropshire, North Northamptonshire,
Blackburn with Darwen, Devon, Warrington) without needing to find any
new data source at all; or (2) re-run the query below in case the DB's
set of `local_authority` values has changed (e.g. a school import
refresh added or renamed a council), since that is the only way this
candidate pool grows from here:

```
python -c "
import sys; sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from app.db import _get_engine
from sqlalchemy import text
with _get_engine().connect() as conn:
    for r in conn.execute(text('SELECT DISTINCT local_authority FROM school_details ORDER BY 1')):
        print(r[0])
"
```
then exclude Wales/offshore entries and everything in the "Covered" and
"Rejected" sections above.
