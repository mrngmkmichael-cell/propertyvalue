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

## Covered (80 councils, in `_AUTHORITIES`)

Bexley, Birmingham, Bolton, Bracknell Forest, Brent, Brighton and Hove,
Bristol City of, Bromley, Buckinghamshire, Bury, Calderdale,
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

## Ground not yet investigated

Everything else - roughly 220 councils, mostly smaller shire
district/borough councils that haven't been individually checked yet.
Prioritise these over re-investigating anything above unless you have
a concrete reason to believe the earlier blocker has changed (e.g. a
Cloudflare-blocked site is reachable again, a moved URL has a new
location).
