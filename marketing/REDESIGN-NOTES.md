# Redesign, 22 August 2026

Live at ukpropertyinsight.co.uk. Everything below is deployed and verified.

## If you hate it

```
git reset --hard pre-redesign-2026-08-22
git push --force-with-lease origin main
```

That tag is the exact working state from before this work, pushed to
GitHub, along with a `backup/pre-redesign-2026-08-22` branch. Nothing is
lost either way.

To revert only part of it, the redesign is a single commit — `15ba917`.

## What the problem actually was

Your reviewer said the site looked AI-generated. Measuring it showed
why, and almost none of it was a matter of taste:

| | Before | After |
|---|---|---|
| Font sizes defined in CSS | 30 | 7 |
| Font sizes rendering on the homepage | 21 | 7 |
| Text elements at weight 700 or 800 | 103 of 178 | 0 at 800, 38 at 600 |
| Weights in use | 400 / 600 / 700 / 800 | 400 / 500 / 600 |
| Typefaces | Inter | Instrument Sans + JetBrains Mono |
| Page background | `#f5f6f8` (Tailwind default) | `#faf9f6` (warm paper) |

Thirteen of those thirty sizes were crammed between 10.9px and 15.2px.
Two of them differed by a tenth of a pixel. Sizes chosen one component
at a time never form a scale, and that flatness is what reads as
undesigned.

And Inter is the default typeface of nearly every AI site builder — so
part of "looks AI-made" was, literally, the font.

## What changed

**Palette.** Warm paper neutrals instead of the cool grey-blue Tailwind
default. Cards stay white, panels are bone, text is a warm near-black.
Status colours muted into the same family. This should read as a
document, not a dashboard — which suits a product built on official
records.

**Type scale.** Seven steps with real jumps between them, replacing
thirty ad-hoc values. Every component now picks one.

**Weight.** 800 is gone entirely; bold is 600. More than half the page
used to be heavy bold, which is why it shouted. Hierarchy now comes from
size and colour, which is how the reference site does it.

**Typefaces.** Instrument Sans for text, JetBrains Mono for the small
uppercase labels and figures. The mono is subset to only the glyphs used
— 30.7 KiB down to 11.5. Together the two faces are 40.9 KiB, which is
less than Inter alone used to cost.

**Detail.** Tabular numerals everywhere (this site is mostly numbers, so
digits now line up in columns instead of jittering), mono uppercase
kickers with opened letter-spacing, a hairline border on the one card
that relied on a shadow alone, and 6rem of air between sections.

## Verified before deploying

- All 21 routes render, no server errors, on local and live
- 182 text elements on the homepage pass WCAG AA contrast, zero
  failures, lowest ratio 4.61 — the accessibility work from the previous
  commit survives intact
- /property passes at both 1440px and 375px: zero contrast failures,
  zero overflow
- Google sign-in, postcode search, property reports, hero map, meta
  descriptions all still working live
- Homepage still contacts zero third-party origins

## What I did NOT do, deliberately

**The copy.** This is the other half of "looks AI-written", and it is
the half I shouldn't do without you. Measured on the homepage:

- 18 em dashes in 1,502 words — one every 83 words
- 13 negation-contrast constructions. "A verdict, not a data dump."
  "queried live, not scraped." "Zero scraping, zero guesswork."
  "A real due-diligence report, not a sales teaser." That "X, not Y"
  shape is the single most recognisable LLM writing tic, and it appears
  five times.
- Every section runs the same KICKER → heading → dek scaffold

The fix is not to have me rewrite it differently — that just produces
different generated copy. What would change it most is three scrappy
sentences from you, in first person, near the top: who built this and
why. You are a non-developer who built a due-diligence tool while buying
somewhere, and who is unusually stubborn about labelling estimates as
estimates. None of that is anywhere on the site, and it is the only part
a competitor cannot copy.

Write those three sentences badly and I'll do the structural work around
them.

**Also still outstanding** (unrelated to the redesign):

- `Cache-Control` header missing on static assets
- `noindex` on /login, /signup, /compare
- /property takes ~14s to load — worth profiling, it's your commercial page
