# The emails, ready to send

One file per firm: open it, copy the subject and the body, send. The body is
already personalised, so there is nothing to fill in. Built by
scripts/outreach_build_emails.py from docs/outreach/prospects.json, which is the
ledger of every firm ever drafted to and the page each address was read from.

Figures quoted in the bodies today: 3,627 schools across 88 councils,
44 checks, 29 of them free.

## The list

| # | Firm | Type | Email | Added | File |
|---|---|---|---|---|---|
| 1 | Hampton Relocation | relocation consultant | james.nevile@hamptonrelocation.co.uk | 2026-09-30 | [01-hampton-relocation.md](01-hampton-relocation.md) |
| 2 | Central Relocation Services | relocation consultant | info@centralrelocation.co.uk | 2026-09-30 | [02-central-relocation-services.md](02-central-relocation-services.md) |
| 3 | Home to Home London | relocation consultant | contact@hometohomelondon.com | 2026-09-30 | [03-home-to-home-london.md](03-home-to-home-london.md) |
| 4 | Louise Crichton Property Search | relocation consultant | louise.crichton@lcps.co.uk | 2026-09-30 | [04-louise-crichton-property-search.md](04-louise-crichton-property-search.md) |
| 5 | South West Relocation | relocation consultant | andrew@southwestrelocation.co.uk | 2026-09-30 | [05-south-west-relocation.md](05-south-west-relocation.md) |
| 6 | Citrus Relocation Services | relocation consultant | globalmobility@citrusrelocation.com | 2026-09-30 | [06-citrus-relocation-services.md](06-citrus-relocation-services.md) |
| 7 | Premier Property Search | relocation consultant | sellers@premier-propertysearch.co.uk | 2026-09-30 | [07-premier-property-search.md](07-premier-property-search.md) |
| 8 | Scott's Relocation | relocation consultant | james@scottsrelocation.co.uk | 2026-09-30 | [08-scott-s-relocation.md](08-scott-s-relocation.md) |
| 9 | LSS Relocation | relocation consultant | accounts@lssrelocation.com | 2026-09-30 | [09-lss-relocation.md](09-lss-relocation.md) |
| 10 | Relocate UK | relocation consultant | info@relocate.uk.com | 2026-09-30 | [10-relocate-uk.md](10-relocate-uk.md) |
| 11 | Garrington Property Finders | buying agent | info@garrington.co.uk | 2026-09-30 | [11-garrington-property-finders.md](11-garrington-property-finders.md) |
| 12 | Stacks Property Search | buying agent | info@stacks.co.uk | 2026-09-30 | [12-stacks-property-search.md](12-stacks-property-search.md) |
| 13 | Stacks Property Search, London | buying agent | london@stacks.co.uk | 2026-09-30 | [13-stacks-property-search-london.md](13-stacks-property-search-london.md) |
| 14 | Buying Agent Partnership | buying agent | hello@buyingagent.com | 2026-09-30 | [14-buying-agent-partnership.md](14-buying-agent-partnership.md) |
| 15 | Ridgestone Property | buying agent | hello@ridgestoneproperty.com | 2026-09-30 | [15-ridgestone-property.md](15-ridgestone-property.md) |
| 16 | Strang & Co | buying agent | hello@strangandco.com | 2026-09-30 | [16-strang-co.md](16-strang-co.md) |
| 17 | Cotswold Property Finder | buying agent | info@cotswoldpropertyfinder.co.uk | 2026-09-30 | [17-cotswold-property-finder.md](17-cotswold-property-finder.md) |
| 18 | Craig Fuller Property Search | buying agent | craig@craigfullerproperty.co.uk | 2026-09-30 | [18-craig-fuller-property-search.md](18-craig-fuller-property-search.md) |
| 19 | Property Hounds, Wiltshire and Gloucestershire | buying agent | jo@property-hounds.co.uk | 2026-09-30 | [19-property-hounds-wiltshire-and-gloucestershire.md](19-property-hounds-wiltshire-and-gloucestershire.md) |
| 20 | Property Hounds, Bath and Bristol | buying agent | Richard@property-hounds.co.uk | 2026-09-30 | [20-property-hounds-bath-and-bristol.md](20-property-hounds-bath-and-bristol.md) |
| 21 | Property Hounds, Cheltenham and North Cotswolds | buying agent | Peter@property-hounds.co.uk | 2026-09-30 | [21-property-hounds-cheltenham-and-north-cotswolds.md](21-property-hounds-cheltenham-and-north-cotswolds.md) |
| 22 | Recoco Property Search | buying agent | njb@recoco.co.uk | 2026-09-30 | [22-recoco-property-search.md](22-recoco-property-search.md) |
| 23 | Bradbourne Property Finders | buying agent | enquiries@bradbourneproperty.co.uk | 2026-09-30 | [23-bradbourne-property-finders.md](23-bradbourne-property-finders.md) |
| 24 | eddge | mortgage broker | hello@eddge.co.uk | 2026-09-30 | [24-eddge.md](24-eddge.md) |
| 25 | Jackson Potter | mortgage broker | fulwell@jacksonpotter.co.uk | 2026-09-30 | [25-jackson-potter.md](25-jackson-potter.md) |
| 26 | The Mortgage Broker | mortgage broker | enquiries@themortgagebroker.co.uk | 2026-09-30 | [26-the-mortgage-broker.md](26-the-mortgage-broker.md) |
| 27 | Mortgage Required | mortgage broker | team@mortgagerequired.com | 2026-09-30 | [27-mortgage-required.md](27-mortgage-required.md) |
| 28 | Gregory Abrams Davidson Solicitors | conveyancing solicitor | info@gadlegal.co.uk | 2026-09-30 | [28-gregory-abrams-davidson-solicitors.md](28-gregory-abrams-davidson-solicitors.md) |
| 29 | Comptons Solicitors | conveyancing solicitor | advice@comptons.co.uk | 2026-09-30 | [29-comptons-solicitors.md](29-comptons-solicitors.md) |
| 30 | Gorvins Residential | conveyancing solicitor | Enquiries@gorvinsresi.com | 2026-09-30 | [30-gorvins-residential.md](30-gorvins-residential.md) |

## How they reach Outlook

`powershell -File scripts/outreach_to_outlook.ps1` saves each one as a draft in the
Drafts folder of support@ukpropertyinsight.co.uk. It sends nothing, and it skips any
draft already there, so it can be run again safely.

## The rules that matter

- UK law allows email to a company or LLP without prior consent, as long as you
  identify yourself and offer a way to opt out. Every draft ends with that line.
  A one-person firm trading under their own name counts as an individual, so use
  their enquiry form instead.
- Ten a day at most, one at a time, from your own mailbox. No mail-merge, no
  tracking, no attachment.
- One follow-up, five working days later, then leave them alone.
- Answer anyone who asks for a report the same day. That is the whole offer.

## The single follow-up

> Hello [name],
>
> Following up once on the note below, in case it was the wrong week. The offer
> stands: send me an address and I will email the report back the same day, free.
> If it is not for you, no reply needed and I will not write again.
>
> Michael
