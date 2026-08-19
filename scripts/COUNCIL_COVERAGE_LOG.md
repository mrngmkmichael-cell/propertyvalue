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
confirmed against both a specific PDF and the bare domain root).

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
- **Herefordshire** - a real-sounding document exists
  ("Information for parents - Admission to primary school 2026",
  `herefordshire.gov.uk/downloads/file/21116/...`) but the URL is a
  JS-driven download-trigger landing page, not the PDF itself or a
  redirect to it - the actual file host/URL wasn't locatable within
  this round's time (unlike West Northamptonshire's equivalent
  Next.js-embedded-JSON case, this page's `__NEXT_DATA__`-equivalent
  didn't yield a direct link either). Worth a dedicated retry with a
  full browser-rendering tool next time, not a dead end.
- **Isle of Wight** - `iow.gov.uk/documents/download/educating-your-
  child-booklet-2026-2027` (the parents' guide) returned a 403 to this
  environment on the specific document path.

## Searched this round via general web search AND direct site
## navigation (admissions hub pages fetched and their links listed) -
## no promising document link surfaced either way
Blackpool, City of London, East Riding of Yorkshire, Luton, North East
Lincolnshire, North Lincolnshire, Northumberland, Plymouth, Redcar and
Cleveland, Rochdale, Stockton-on-Tees, Torbay, Wakefield, Westmorland
and Furness, Wiltshire. (This is the second round these have been
checked, now including direct navigation of each council's own
admissions landing page as well as general web search, per last
round's suggestion - and it still didn't surface a distance document
for this group. That still doesn't *prove* one doesn't exist:
**Shropshire and North Northamptonshire were in this exact "search
found nothing" bucket after last round's pass and this round's pass
alike, right up until a JS-rendering-aware fetch and a document-title
guess surfaced real data for both** - so "not found by search/
direct-nav twice" is meaningfully stronger evidence of a genuine gap
than one pass, but a future round that wants to push further should
try: (a) a fetch tool that renders JavaScript for any site built on a
modern JS framework - some of these councils' sites, like West
Northamptonshire's and Herefordshire's, only expose real document
links in the rendered DOM, not the raw HTML/JSON a plain `curl`/
`httpx` request sees; (b) guessing that a neighbouring/sibling council
(e.g. one that split from the same former county, or shares a
supplier/CMS) publishes the same page template - this round found West
Northamptonshire's real data specifically *because* North
Northamptonshire's sibling page worked; (c) composite "Parents' Guide"
prospectus PDFs specifically, scanned page-by-page for a numeric
distance pattern - these often bury real per-school distance data
inside a much longer policy document under a title that gives no hint
of it, as Shropshire's did.)

## Correction (this round): actual remaining-council count

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

After this round, the true remaining set is exactly the "Searched this
round via general web search AND direct site navigation" list a few
sections up (Blackpool, City of London, East Riding of Yorkshire,
Luton, North East Lincolnshire, North Lincolnshire, Northumberland,
Plymouth, Redcar and Cleveland, Rochdale, Stockton-on-Tees, Torbay,
Wakefield, Westmorland and Furness, Wiltshire - 15 councils) plus two
councils with a genuine, real-sounding-but-not-yet-retrieved document
(Herefordshire - JS-driven download page, actual file URL not found
this round; Isle of Wight - specific document 403'd) worth a dedicated
retry with better tooling. Every other candidate this round either got
added (West Northamptonshire), got a specific reject reason recorded
above (Shropshire, North Northamptonshire, Isles of Scilly, Derbyshire,
Lincolnshire, York, Wolverhampton, Nottingham, Cumberland, Halton,
Kingston upon Hull, Barnsley, Lancashire, Leicestershire - the last now
confirmed environment-blocked rather than merely "not found"), or was
already covered/rejected in a prior round.

Two whole rounds of general web search plus one-to-two rounds of
direct-navigation-of-admissions-pages (including scanning several
composite "parents' guide" PDFs page-by-page for numeric distance
patterns, the technique that found Shropshire's real document - it
came up empty again for Wolverhampton, Nottingham, Cumberland, Halton,
Hull, Barnsley and Lancashire North this round) have now failed to
surface a *distance-bearing* document for the 15-council list, which is
stronger (though still not
conclusive) evidence it's a genuine gap rather than a search-effort
gap - see the note above this list for concrete ideas a future round
could try that this round didn't (JS-rendering fetch tools, sibling-
council template guessing). If that list is ever exhausted, re-run the
query below in case the DB's set of `local_authority` values has
changed (e.g. a school import refresh), rather than assuming there's
nothing left:

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
