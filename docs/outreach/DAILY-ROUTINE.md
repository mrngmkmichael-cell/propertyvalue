# The 10am outreach routine

What the scheduled task "daily-outreach-drafts" does every morning, and
what it must never do. Written 30 September 2026, after the first batch
went out.

## The job, in order

1. **Read the ledger.** `docs/outreach/prospects.json` holds every firm
   ever drafted to: firm, contact, category, email, the hook line, the
   page the address came from, and the dates it was drafted, sent and
   replied to. Nobody in that file is ever contacted again by this
   routine.

2. **Find five new firms.** In this order of preference, because this is
   the order they are worth:
   - relocation consultants who list school search, from the ARP member
     directory at `arp-relocation.com/members/<slug>` (each member page
     publishes a name, a phone number and usually an email),
   - buying agents and property finders, from their own contact pages,
   - mortgage brokers,
   - conveyancing solicitors last, and only where they publish an
     address rather than a form.
   Vary the region each day: London one day, the north west another,
   Scotland, the south west, the Midlands. A firm already in the ledger,
   or at the same domain as one, does not count towards the five.

3. **Verify every address on the firm's own page.** Read it, do not
   infer it. `info@` plus the domain is a guess, and a guess that
   bounces costs the sender's reputation. If a firm publishes only a web
   form, add it to the ledger with `"email": ""` and
   `"status": "form only"` so it is never re-researched, and move on.
   If the firm is one person trading under their own name rather than a
   limited company or LLP, treat it as form-only too: UK law (PECR)
   treats a sole trader as an individual, so cold email needs consent.

4. **Write one true hook line per firm**, taken from that firm's own
   words on their own site: the counties they cover, a service they
   list, a guide they have published. Never flattery, never invented.

5. **Append to the ledger and build.**
   `.venv/Scripts/python.exe scripts/outreach_build_emails.py` rewrites
   `docs/outreach/emails/` from the ledger, with today's real figures
   read from the app and the database.

6. **Put them in Outlook.**
   `powershell -File scripts/outreach_to_outlook.ps1` saves a draft for
   each firm that has no `drafted` date, stamps the ledger and stops.
   The drafts land in the Drafts folder of
   support@ukpropertyinsight.co.uk.

7. **Check for replies.** Read the support@ inbox for messages from any
   address in the ledger since yesterday. List them for Michael and set
   `replied` in the ledger. Do not answer them: a reply asking for a
   report is his to answer, the same day.

8. **Report.** Five lines: who was added and why they fit, who replied,
   anything that bounced, and the running totals from the ledger
   (drafted, sent, replied). Write the same into
   `docs/outreach/log/<date>.md`, commit the ledger, the emails and the
   log, and push.

## The two promises, enforced in code

**One firm, one email.** `scripts/outreach_build_emails.py` refuses to
build if the ledger holds the same address twice, or two addresses at
the same domain without `"allow_same_domain": true` on the entry, and
`scripts/outreach_to_outlook.ps1` drafts only for entries with no
`drafted` date. `tests/test_outreach_ledger.py` fails the suite if
either rule is broken, so a repeat cannot reach a mailbox through a
mistake in a morning's research. A firm that turns out to be form-only
or a sole trader still goes in the ledger, with an empty email, so it is
never researched a second time either.

**No two days read alike.** Each firm draws its subject line, its
opening angle and its price sentence from rotations, chosen by its own
address and the day it was added: fourteen subject lines across the four
trades, four openings, three ways of saying the price. The same firm
always rebuilds to the same email, and a batch of five never opens the
same way five times. When adding a firm, write its hook line fresh from
that firm's own site; the rotation handles the rest.

## Never

- **Never send.** The routine writes drafts. Michael reads and clicks
  Send. No exceptions, and no "send the ones he approved yesterday".
- **Never invent an address**, or build one from a pattern.
- **Never write to a firm already in the ledger**, or to a second person
  at a firm already contacted, without Michael asking for it.
- **Never use a mail-merge tool, a tracking pixel or an attachment.**
- **Never drop the opt-out line** at the end of each email. UK law
  (PECR) allows business-to-business email without prior consent only
  when the sender is identified and every message offers a way out.
- **Never quote a figure that is not read from the site or the database
  that morning.** The build script reads them; do not hand-write them.

## Why the dedupe lives in the ledger

On 30 September 2026 the Outlook script decided what was already drafted
by reading the Drafts folder through COM. Mid-sync the folder came back
empty, four firms were drafted a second time, and when the batch was sent
seven firms received the same email two or three times. The ledger on
disk cannot go stale halfway through a sync, so it is now the only
source of truth, and the folder is never consulted.

## The pricing paragraph

£9.99 a month or £24.99 a quarter, first full report free with an
account, described as introductory prices while feedback is being
collected, with a note that they will be reviewed. It says introductory,
not "discount", because there is no former higher price to discount from
and this site does not make claims it cannot show.
