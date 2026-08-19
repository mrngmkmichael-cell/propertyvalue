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

## Covered (82 councils, in `_AUTHORITIES`)

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
Forest, Wandsworth, Warwickshire, West Sussex, Westminster, Wigan,
Windsor and Maidenhead, Wirral, Wokingham, Worcestershire.

## Rejected - do not re-attempt without genuinely new information

### Blocked from this environment (Cloudflare / WAF / persistent 403)
Enfield, Gateshead, West Berkshire, Sunderland, Barnet, Hampshire,
Manchester.

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

## Searched this round via general web search only (no promising
## document link surfaced) - worth a deeper direct site crawl next
## time, not necessarily a dead end
Barnsley, Blackpool, Bradford, City of London, Cumberland, Derbyshire,
East Riding of Yorkshire, Halton, Herefordshire, Isle of Wight, Isles
of Scilly, Kingston upon Hull, Lancashire, Leicestershire,
Lincolnshire, Luton, North East Lincolnshire, North Lincolnshire,
North Northamptonshire, Northumberland, Nottingham, Plymouth, Rochdale,
Shropshire, Stockton-on-Tees, Torbay, Wakefield, West
Northamptonshire, Westmorland and Furness, Wiltshire, Wolverhampton,
York. (Search results for these kept surfacing generic admissions-
policy pages or other councils' documents rather than an actual
distance-figure table/spreadsheet for the named council - that doesn't
prove one doesn't exist, just that it wasn't found via search engine
in the time available. A future round should try browsing each
council's own admissions page directly for an "allocation
statistics"/"offer day"/"how places were allocated" link, and/or a
site-specific search, rather than general web search alone.)

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
round via general web search only" list a few sections up (Barnsley
through York, ~32 councils) plus Redcar and Cleveland (not searched at
all this round). Start there next time - try each council's own
admissions/"how places were allocated" page directly rather than
general web search, since that approach found real data for Bedford,
Central Bedfordshire, Warrington and Stoke-on-Trent this round (the
latter two ultimately rejected for parsing-safety reasons, not for
lack of data). If that list is ever exhausted, re-run the query above
in case the DB's set of `local_authority` values has changed (e.g. a
school import refresh), rather than assuming there's nothing left.
