# UKPropertyInsight

UK property due-diligence site. FastAPI + Jinja2 + SQLAlchemy on Postgres
(Neon), deployed on Render, which redeploys on push to `main`.

## Read first, every session

- **[DESIGN.md](DESIGN.md)** — the design brief: palette, type, voice,
  guardrails. Any visual work starts here. Never freelance a colour or a
  font; the tokens are the vocabulary.
- **[design/taste/](design/taste/)** — reference families, named. Point
  at one by name when building a new look.

## Non-negotiables

- **Real data or no feature.** Every figure names an official source.
  Where no reliable national dataset exists, the feature is skipped, not
  modelled. Missing data says so in words rather than showing a blank.
- **No em-dashes in user-facing copy.** Use a comma, a colon or a full
  stop.
- **Verify before claiming done.** Scratchpad tests with the existing
  `check()` pattern, `smoke.py` against the dev server, and a browser
  screenshot for anything visual, at desktop and 375px.
- **Ship small and verify live.** Commit, push, then confirm on
  ukpropertyinsight.co.uk before saying it works.

## Traps that have bitten before

- The report page has **two map implementations**: production has
  `GOOGLE_MAPS_API_KEY` and renders the Google branch; dev has no key and
  renders Leaflet. Change both, or production shows nothing.
- Anonymous report pages are **HTML-cached for 10 minutes in-process**, so
  a template edit can look like it did nothing. Restart the dev server or
  use a fresh postcode.
- `uvicorn --reload` **wedges silently**. Restart via the preview tools
  rather than trusting a reload.
- Bash heredocs break on this repo's CRLF line endings when the content
  has apostrophes. Write a Python script into the scratchpad instead.
- Any script or poll that hits production sends the header
  `X-Internal-Check: 1`, or it counts as visitors on /admin. smoke.py and
  audit_site.py already do. The Browser pane is excluded by its user agent.

## Commands

Dev server: use the preview tools (`.claude/launch.json`, port 8010), never
a bare shell command.
Tests: `.venv/Scripts/python.exe -m pytest -q` (105 tests, run before
every push), then `.venv/Scripts/python.exe smoke.py` against a running
server. pytest fakes the ~30 upstream services; smoke.py walks the real
pages and catches what a fake cannot. Pass a base URL to point it at
production.
Data imports: `scripts/import_*.py`, each re-runnable and commented with
its source and refresh cadence.
