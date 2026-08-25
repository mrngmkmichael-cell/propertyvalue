# UKPropertyInsight design brief

The four blocks any design prompt needs: **aesthetic, reference, intent,
guardrails.** Everything below is a decision already made and shipped, so
new work matches the site instead of re-inventing it. Change this file
when the direction changes; do not change it to match a one-off.

---

## 1. Aesthetic

**Family: warm editorial document.** Not a SaaS dashboard, not a fintech
app. The product is a due-diligence report built from public records, so
it should read like something a professional prepared, printed on good
paper. Calm, dense with fact, confident enough to be plain.

Adjectives that are on-brief: considered, warm, factual, quiet, precise,
archival.
Off-brief: playful, techy, futuristic, glossy, urgent, salesy.

**Tokens (app/static/css/style.css `:root`) — do not freelance colours.**

| Role | Value | Note |
|---|---|---|
| Ground | `--bg: #faf9f6` | Warm paper. Never a cool grey (#f5f6f8 is the Tailwind default and reads as generated). |
| Surface | `--surface: #ffffff`, `--surface-2: #f1efe9` | |
| Ink | `--ink: #1c1714`, `--ink-soft: #57504a`, `--ink-faint: #736a61` | Three steps, all ≥4.5:1 on the darkest surface. |
| Border | `--border: #e5e1d8` | **One** border colour, 1px, everywhere. This is a large part of why the site feels calm. |
| Accent | `--accent: #2b4c8c` | Deep navy. Never an electric blue; it fights a warm palette. |
| CTA | `--cta-orange: #a8541a` | Warm rust, belongs to the palette rather than shouting over it. |
| Status | `--good: #2f6b4f`, `--warn: #8a5a12`, `--bad: #a83a32` | Muted into the same warm family. Semantic only, never decorative. |

**Type.** Instrument Sans for everything read as language; JetBrains Mono
for labels, figures, postcodes and anything that should feel like data.
The mono is the site's signature — kickers, stat labels, section pills.
Seven-step scale (`--text-xs` … `--text-3xl`), weights normal/medium/
semi/bold. Self-hosted, no external font requests.

---

## 2. Reference

Primary: **zaro.ai** — the original direction. What was taken: one hairline
border colour, generous whitespace, calm sectioning, restraint in motion.
What was not: its palette or its subject matter.

Add your own references to `design/taste/` and name them (see the README
there). A named style can enter a prompt; an unnamed screenshot cannot.

---

## 3. Intent

**Who:** a UK buyer, usually a first-timer, part-way through the biggest
purchase of their life, often anxious and short on time. Sometimes their
partner, reading a link they were sent.

**What they should do:** type a postcode. Then, if convinced, sign up for
three free reports.

**What they should feel:** that someone competent already did the
homework, and is not selling them a house.

**Voice.** Plain English, complete sentences, no marketing verbs. Name the
source of a figure. When data is missing, say so and say why — never a
silent blank. Never an em-dash (an AI tell; use a comma, a colon or a full
stop). Never exclamation marks.

---

## 4. Guardrails

**Always**
- Every figure names its official source.
- Missing data states the gap in words.
- One accent doing the work; everything around it quiet.
- Real content in mockups, never lorem ipsum or invented numbers.
- Test at 375px before shipping; `overflow-x: clip` on html/body is a
  standing guard, and no form control goes below 16px on mobile (iOS
  zooms the page otherwise).
- `prefers-reduced-motion` respected on every animation.

**Never**
- Blue-purple gradients. Any gradient, in practice.
- Inter, or any of the default system-stack look.
- Cool grey neutrals.
- Generic blobs, stock illustration, emoji as section markers.
- Everything centred.
- More than one border colour, or a second accent hue.
- A number on screen that is not real and sourced.
- Rounded-everything: `--radius-sm/md/lg/pill` exist and are chosen per
  component, not applied uniformly.

---

## Working method

1. **Never single-shot a visual decision.** Build 3–5 genuinely different
   directions, screenshot each, compare side by side, then pick. One
   version is a lottery ticket; five is a choice.
2. **Build the knobs, then turn them.** "Make it more premium" is not a
   brief. If a look needs tuning, expose the variable and adjust it.
3. **Verify in a browser, not in the terminal.** Screenshot the result at
   desktop and 375px. The dev server's reloader wedges silently — restart
   it rather than trusting a reload (see the project memory notes).
