# AI search readiness (GEO), 16 September 2026

Run with the claude-seo plugin's GEO skill (v2.3.1, MIT) against
ukpropertyinsight.co.uk, then verified by reading the live pages directly.
Every page was fetched with `X-Internal-Check: 1`, so none of it counted as
visitors on /admin.

**Question this answers:** when someone asks ChatGPT, Perplexity or Google's
AI Overviews about UK school catchments or house prices, can they read us and
cite us?

## Score: about 75 out of 100

| Area | Weight | Result |
|---|---|---|
| Technical accessibility | 20 | 19. Nothing blocks an AI crawler, and nothing needs JavaScript |
| Citability | 25 | 21. Specific figures, each naming its source |
| Structure | 20 | 17. Clean headings, 7 to 8 tables a page, real questions in the FAQs |
| Multi-modal | 15 | 9. Maps and tables, no video |
| Authority and freshness | 20 | 9. No dates anywhere, no author, no Wikipedia entry |

## What is already right

**Every AI crawler is allowed.** robots.txt is `User-agent: *` with `Allow: /`,
so OAI-SearchBot (which decides ChatGPT Search citability), Claude-SearchBot,
PerplexityBot and Googlebot all have full access. Only /watchlist and
/internal/ are closed, which is correct. Training crawlers (GPTBot,
Google-Extended, ClaudeBot, CCBot) are also allowed; that is a licensing
choice, not a search one, and it currently favours us.

**Nothing depends on JavaScript.** AI crawlers do not run it. Every page
fetched as raw HTML already contains its headings, its tables and its figures:
270 KB on a school page, 258 KB on an area guide, with 8 and 7 tables
respectively. The report's group tiles are built in the browser, but the
server still sends every card, which is why that change was safe.

**The structured data is unusually complete.** School pages carry School,
GeoCoordinates, PostalAddress, BreadcrumbList, FAQPage and Organization. Area
guides carry Place and FAQPage. This is better than most competitors.

**llms.txt exists and is good.** 3,587 bytes, listing the data pages with a
sentence each, plus rules for reuse and a contact address. Google ignores it
by its own published guidance, but other systems may not. No work needed.

**The FAQs ask real questions.** "What is the catchment area for X?", "How
close do I need to live to get a place?" Those match how people type into an
AI assistant, and they are inside FAQPage schema.

## The one real gap: nothing says when a page was last checked

Zero date signals across all four page types tested. No `dateModified`, no
`datePublished`, no visible "Updated on" line.

| Page | Dates in schema | Visible date |
|---|---|---|
| Homepage | 0 | none |
| School page | 0 | none |
| Area guide | 0 | none |
| Admissions hub | 0 | none |

Why it matters, on the plugin's own cited evidence (SE Ranking, 1.3 million
citations): content under three months old is about three times more likely to
be cited in an AI answer, and pages left untouched for six months or more lose
citation eligibility. Our data is refreshed constantly, and an AI system has
no way to tell. We are being judged as stale while being current.

This is the highest-value fix on the list, and it is cheap: the dates already
exist in our own data (the admissions import's academic year and source
authority, the area guide payload's build date, the HPI period).

## The rest, in order

1. **Add dates.** `dateModified` in the schema and one visible line per page,
   from the data we already hold. Highest impact, lowest effort.
2. **Name who is behind it.** No author or organisation credentials appear on
   any page. AI systems weigh this as an authority signal, and we have a real
   answer: every figure traces to a named government source, which the
   methodology page already documents.
3. **The admissions hub is thinner than the school pages.** /schools/admissions
   has 3 headings, 1 table, no FAQPage schema and no question-form headings,
   while a single school page has 14 headings and 8 tables. It is a hub that
   search sends people to, so it deserves the same treatment.
4. **Brand mentions beat backlinks for AI citation**, by roughly three to one
   in the plugin's cited Ahrefs study of 75,000 brands, with YouTube the
   strongest single signal. We have none of that. This is a marketing
   question, not a code one, and the video work is on hold by choice.
5. **No video anywhere.** Multi-modal pages see materially higher selection
   rates. Also on hold by choice.

## What was not done

No changes were made to the site. This is a read-only assessment.
