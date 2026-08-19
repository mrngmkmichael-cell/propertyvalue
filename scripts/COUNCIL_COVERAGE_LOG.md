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

## Covered (81 councils, in `_AUTHORITIES`)

Bedford, Bexley, Birmingham, Bolton, Bracknell Forest, Brent, Brighton
and Hove, Bristol City of, Bromley, Buckinghamshire, Bury, Calderdale,
Cambridgeshire, Camden (secondary only), Cheshire East, Cheshire West
and Chester, County Durham, Coventry, Derby, Dorset, Dudley, Ealing,
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
and everything in "Rejected" above. As of this round that left only
these English councils genuinely uninvestigated:

Barnsley, Blackpool, Bradford, Central Bedfordshire, City of London,
Cumberland, Darlington, Derbyshire, East Riding of Yorkshire, Halton,
Herefordshire (County of), Isle of Wight, Isles Of Scilly, Kingston
upon Hull (City of), Lancashire, Leicestershire, Lincolnshire, Luton,
North East Lincolnshire, North Lincolnshire, North Northamptonshire,
Northumberland, Nottingham, Plymouth, Redcar and Cleveland, Rochdale,
Rutland, Shropshire, Slough, South Tyneside, Stockton-on-Tees,
Stoke-on-Trent, Torbay, Wakefield, Warrington, West Northamptonshire,
Westmorland and Furness, Wiltshire, Wolverhampton, York.

(Devon was in that list and has now been moved to Rejected above,
Bedford has been moved to Covered above.)

## Ground not yet investigated

See the explicit list immediately above - work through those. If that
list is ever exhausted, re-run the query above in case the DB's set of
`local_authority` values has changed (e.g. a school import refresh),
rather than assuming there's nothing left.
