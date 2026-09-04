"""End-to-end smoke test against a running server.

What this is for, and what it is not:

  pytest exercises the app with the ~30 upstream services faked, which
  is right for logic but means a template can render perfectly in the
  suite and still be broken against real data. This walks a running
  server and checks that the pages a visitor actually reaches come back
  whole.

  It is deliberately NOT a health check of the upstream data sources.
  Those throttle by IP, Render is one IP, and a fast sweep manufactures
  failures that look exactly like real ones. A source being down is
  expected and is not a smoke failure; a page that 500s, loses its
  content, or leaks a template error is.

    .venv/Scripts/python.exe smoke.py                     # dev, port 8010
    .venv/Scripts/python.exe smoke.py https://ukpropertyinsight.co.uk

Exit code is 0 when every check passed, 1 otherwise, so it can gate a
push.
"""
import re
import sys
import time

import httpx

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010").rstrip("/")

# Identifies these requests in the analytics as a bot rather than a
# visitor. Dev and production share one database, so an unmarked sweep
# writes itself into the real pageview counts.
UA = {"User-Agent": "UKPropertyInsightSmoke/1.0 (+bot)", "X-Internal-Check": "1"}

# Text that means the renderer fell over, whatever the status code says.
# A Jinja page can return 200 with an exception baked into the body.
BREAKAGE = [
    (r"\bTraceback \(most recent call last\)", "python traceback"),
    (r"jinja2\.exceptions", "jinja exception"),
    (r"\bUndefinedError\b", "undefined template variable"),
    (r">\s*None\s*<", "a None rendered as text"),
    (r"\{\{.*?\}\}", "an unrendered template expression"),
    (r"\{%.*?%\}", "an unrendered template tag"),
    (r"Internal Server Error", "server error page"),
]

# (path, accepted statuses, [strings the page must contain])
# The markers are chosen to be the thing the page exists to say, so a
# page that renders its furniture but loses its content still fails.
CHECKS = [
    ("/", {200}, ["checks"]),
    ("/areas", {200}, ["Area guides"]),
    ("/area/M14", {200}, ["Living in M14", "House prices", "Follow M14"]),
    ("/area/EH1", {200}, ["Living in EH1"]),
    ("/area/M14/private-schools", {200}, ["Fee-paying schools in M14"]),
    ("/compare/M20/vs/M21", {200}, ["M20", "M21"]),
    ("/schools/guide?q=M14", {200}, ["M14"]),
    ("/schools/admissions", {200}, ["council by council"]),
    ("/schools/admissions/hertfordshire", {200}, ["Hertfordshire", "Admitted from"]),
    ("/schools/how-admissions-work", {200}, ["31 October", "15 January"]),
    ("/schools/tightest-catchments", {200}, ["tightest gates in England", "councils compared"]),
    ("/schools/catchment-house-prices", {200}, ["within reach"]),
    ("/running-costs", {200}, ["Band D", "Estate charges"]),
    ("/estate-charges", {200}, ["fleecehold", "Twelve questions"]),
    ("/schools/independent", {200}, ["Private schools, council by council"]),
    ("/schools/independent/surrey", {200}, ["Private schools in Surrey"]),
    ("/schools/admission-distances.csv", {200}, ["urn,school,phase,council"]),
    ("/llms.txt", {200}, ["# UKPropertyInsight"]),
    ("/property?postcode=M1+1AE", {200, 202}, ["M1 1AE"]),
    ("/property?postcode=EH1+1YZ", {200, 202}, ["EH1 1YZ"]),
    ("/property/comparables?postcode=M1+1AE", {200}, ["Comparables"]),
    ("/property/checklist?postcode=M1+1AE", {200}, ["Viewing checklist", "At every viewing"]),
    ("/tools/stamp-duty-calculator", {200}, ["Stamp duty"]),
    ("/tools/mortgage-calculator", {200}, ["Mortgage"]),
    ("/market-report", {200}, []),
    ("/market/district-prices", {200}, []),
    ("/methodology", {200}, ["methodology"]),
    ("/accuracy", {200}, []),
    ("/premium", {200}, []),
    ("/buying-guide", {200}, []),
    ("/support", {200}, []),
    ("/terms", {200}, []),
    ("/privacy", {200}, []),
    ("/login", {200}, []),
    ("/signup", {200}, []),
    # Not the production hostname: on the dev server these are localhost
    # URLs, and a check that only passes against one environment is a
    # check that gets ignored on the other.
    ("/sitemap.xml", {200}, ["<urlset", "<loc>"]),
    ("/robots.txt", {200}, ["Sitemap:"]),
    # Signed out, so these must send the visitor to sign in rather than
    # rendering an empty page or erroring.
    ("/watchlist", {303}, []),
    # A postcode that does not exist must be told so, not crash.
    ("/property?postcode=ZZ99+9ZZ", {404}, []),
    ("/no-such-page-at-all", {404}, []),
]

# Land Registry, Ofsted and the rest are slow on a cold cache, and a
# burst is what gets an IP throttled.
PAUSE_S = 0.4


def main() -> int:
    print(f"smoke: {BASE}\n")
    failures = []
    client = httpx.Client(timeout=90.0, headers=UA, follow_redirects=False)

    for path, want_status, markers in CHECKS:
        started = time.monotonic()
        try:
            r = client.get(BASE + path)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{path}: request failed: {exc}")
            print(f"  FAIL  {path}  ({exc})")
            continue
        elapsed = time.monotonic() - started
        body = r.text
        problems = []

        if r.status_code not in want_status:
            wanted = " or ".join(str(x) for x in sorted(want_status))
            problems.append(f"status {r.status_code}, wanted {wanted}")

        # Only inspect the body of a page meant to have one.
        if r.status_code == 200 and 200 in want_status:
            for marker in markers:
                if marker not in body:
                    problems.append(f"missing {marker!r}")
            for pattern, name in BREAKAGE:
                if re.search(pattern, body, re.S):
                    problems.append(name)
            if len(body) < 500 and not path.endswith(".txt"):
                problems.append(f"body only {len(body)} bytes")

        if problems:
            failures.append(f"{path}: {'; '.join(problems)}")
            print(f"  FAIL  {path:<46} {elapsed:5.1f}s  {'; '.join(problems)}")
        else:
            print(f"  ok    {path:<46} {elapsed:5.1f}s")
        time.sleep(PAUSE_S)

    print()
    if failures:
        print(f"{len(failures)} of {len(CHECKS)} failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"all {len(CHECKS)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
