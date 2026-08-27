"""Render a real property report for random postcodes in every district.

The area guides prove a district's own page works. This proves the thing
the district is for: that an address inside it produces a report. It
picks real postcodes from postcodes.io rather than inventing them, so a
404 here means the report refused an address that genuinely exists.

    .venv/Scripts/python.exe scripts/sweep_postcodes.py
    .venv/Scripts/python.exe scripts/sweep_postcodes.py --per-outcode 5
    .venv/Scripts/python.exe scripts/sweep_postcodes.py --outcodes 100
    .venv/Scripts/python.exe scripts/sweep_postcodes.py --resume

Scale, so nobody starts this unaware: 2,943 districts at 5 postcodes
each is 14,715 cold reports, and a cold report fans out to ~34 upstream
sources. Every result is written to the log as it lands and --resume
skips what is already there, so this is meant to be run in stages and
stopped whenever.

RUN THIS AGAINST THE DEV SERVER, NOT PRODUCTION. Measured on
2026-08-29: at 8 concurrent requests Render's proxy started returning
502s, and even at 4 the live homepage went from 0.3s to 5.7s for a real
visitor and recovered the moment the sweep stopped. Thousands of cold
reports is a load test however politely it is paced, and there is no
setting that makes it not one. The dev server runs the same templates
and the same gather, which is where render errors live.

The one thing a local sweep cannot see is the report's Google Maps
branch: production has GOOGLE_MAPS_API_KEY and renders it, dev has no
key and renders Leaflet instead. Cover that with a small deliberate
sample against production, not with volume.

Two things keep it from becoming a load test:

  Concurrency is low by default and there is a pause between batches.
  DEFRA, the Environment Agency and Police.uk throttle by IP and Render
  is one IP, so pushing harder degrades the live site for real visitors
  and then reports the throttling back as if it were a fault.

  The failure rate is watched. Past FAILURE_ALARM in a batch the sweep
  slows down and says so, because a sudden cliff is far more likely to
  be self-inflicted rate limiting than 40 genuinely broken districts.

The user agent contains "crawler" on purpose. A cold report answers a
browser with a 202 "building your report" page and only gives crawlers
the finished blocking render, so anything else would sweep 14,715
placeholder pages and call them healthy. It also keeps the sweep out of
the pageview counts.
"""
import argparse
import asyncio
import json
import os
import pathlib
import random
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

UA = {"User-Agent": "UKPropertyInsightSweepCrawler/1.0 (+report sweep)"}
POSTCODES_IO = "https://api.postcodes.io"

OUTCODES_FILE = pathlib.Path("app/data/outcodes.json")
DEFAULT_LOG = pathlib.Path("sweep_results.jsonl")
POSTCODE_CACHE = pathlib.Path("sweep_postcodes.json")

# Same list smoke.py uses: the ways a page returns 200 with the failure
# baked into the body.
BREAKAGE = [
    (r"\bTraceback \(most recent call last\)", "python traceback"),
    (r"jinja2\.exceptions", "jinja exception"),
    (r"\bUndefinedError\b", "undefined template variable"),
    (r">\s*None\s*<", "a None rendered as text"),
    (r"\{\{.*?\}\}", "an unrendered template expression"),
    (r"\{%.*?%\}", "an unrendered template tag"),
    (r"Internal Server Error", "server error page"),
]

# A report that rendered says these. Their absence means the page came
# back as a shell. Matched case-insensitively and against the source,
# not the rendered text: the report's kicker reads "Property report" in
# the HTML and is uppercased by CSS, so looking for "PROPERTY REPORT"
# failed every single page on the first trial run.
REQUIRED = ["property report", "</html>"]

FAILURE_ALARM = 0.25   # slow down past this share of failures in a batch
BATCH_PAUSE_S = 1.5


async def _postcodes_for(client: httpx.AsyncClient, outcode: str, want: int) -> list[str]:
    """Real postcodes inside this district, from postcodes.io.

    Nearby-point lookup rather than a random one per postcode: one call
    returns up to 100 and several of them belong to the neighbouring
    district, so they are filtered back to this one before sampling.
    """
    try:
        r = await client.get(f"{POSTCODES_IO}/outcodes/{outcode}")
        if r.status_code != 200:
            return []
        centre = r.json()["result"]
        lat, lon = centre.get("latitude"), centre.get("longitude")
        if lat is None or lon is None:
            return []
    except (httpx.HTTPError, KeyError, ValueError):
        return []

    found: list[str] = []
    for radius in (2000, 8000, 20000):
        try:
            r = await client.get(
                f"{POSTCODES_IO}/postcodes",
                params={"lon": lon, "lat": lat, "limit": 100, "radius": radius},
            )
            if r.status_code != 200:
                continue
            result = r.json().get("result") or []
        except (httpx.HTTPError, ValueError):
            continue
        prefix = outcode.upper() + " "
        found = [p["postcode"] for p in result if p.get("postcode", "").upper().startswith(prefix)]
        if len(found) >= want:
            break
    if not found:
        return []
    return random.sample(found, min(want, len(found)))


# Infrastructure, not the application: Render's proxy giving up under
# load, or an upstream rate limiter. Recording these as failures blames
# the site for the sweep's own weight. A first run at concurrency 8
# logged 8 "failures" this way, every one of which returned 200 when
# retried on its own a minute later.
RETRYABLE = {429, 500, 502, 503, 504}
RETRIES = 2


async def _check(client: httpx.AsyncClient, base: str, outcode: str, postcode: str) -> dict:
    url = f"{base}/property?postcode={postcode.replace(' ', '+')}"
    started = time.monotonic()
    r = None
    for attempt in range(RETRIES + 1):
        try:
            r = await client.get(url)
        except Exception as exc:  # noqa: BLE001
            if attempt == RETRIES:
                return {"outcode": outcode, "postcode": postcode, "ok": False, "status": 0,
                        "problems": [f"request failed: {exc}"], "seconds": 0}
            await asyncio.sleep(5 * (attempt + 1))
            continue
        if r.status_code not in RETRYABLE:
            break
        if attempt < RETRIES:
            # Alone and unhurried, so the retry is not competing with
            # the rest of the batch for the same capacity.
            await asyncio.sleep(10 * (attempt + 1))

    elapsed = round(time.monotonic() - started, 1)
    problems = []
    if r.status_code != 200:
        problems.append(f"status {r.status_code}")
    else:
        body = r.text
        for pattern, name in BREAKAGE:
            if re.search(pattern, body, re.S):
                problems.append(name)
        lowered = body.lower()
        for marker in REQUIRED:
            if marker not in lowered:
                problems.append(f"missing {marker!r}")
    return {"outcode": outcode, "postcode": postcode, "ok": not problems,
            "status": r.status_code, "problems": problems, "seconds": elapsed,
            "infra": r.status_code in RETRYABLE}


def _already_done(log: pathlib.Path) -> set[str]:
    if not log.exists():
        return set()
    done = set()
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            done.add(json.loads(line)["postcode"])
        except (ValueError, KeyError):
            continue
    return done


async def main() -> int:
    ap = argparse.ArgumentParser()
    # Defaults to dev on purpose. See the note at the top: pointing this
    # at production measurably slows the live site for real visitors.
    ap.add_argument("base", nargs="?", default="http://127.0.0.1:8010")
    ap.add_argument("--per-outcode", type=int, default=5)
    ap.add_argument("--outcodes", type=int, default=0, help="limit districts, 0 = all")
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    args = ap.parse_args()

    base = args.base.rstrip("/")
    log = pathlib.Path(args.log)
    districts = [o["outcode"] for o in json.loads(OUTCODES_FILE.read_text(encoding="utf-8"))]
    random.shuffle(districts)   # broad coverage early, so a partial run still means something
    if args.outcodes:
        districts = districts[: args.outcodes]

    done = _already_done(log) if args.resume else set()
    if done:
        print(f"resuming: {len(done)} postcodes already checked")

    # Resolving postcodes is a separate pass so a resumed run does not
    # ask postcodes.io for them all over again.
    cache = json.loads(POSTCODE_CACHE.read_text(encoding="utf-8")) if POSTCODE_CACHE.exists() else {}
    missing = [o for o in districts if len(cache.get(o, [])) < args.per_outcode]
    if missing:
        print(f"resolving postcodes for {len(missing)} districts...")
        async with httpx.AsyncClient(timeout=30, headers=UA) as client:
            for i in range(0, len(missing), 8):
                chunk = missing[i: i + 8]
                got = await asyncio.gather(*(_postcodes_for(client, o, args.per_outcode) for o in chunk))
                for outcode, postcodes in zip(chunk, got):
                    cache[outcode] = postcodes
                if i and i % 200 == 0:
                    POSTCODE_CACHE.write_text(json.dumps(cache), encoding="utf-8")
                    print(f"  {i}/{len(missing)} districts resolved")
        POSTCODE_CACHE.write_text(json.dumps(cache), encoding="utf-8")

    # Breadth first: every district's 1st postcode, then every
    # district's 2nd, and so on. Depth first (all five of one district
    # before moving on) means 22 hours of running before the last
    # districts are touched at all, and five postcodes inside one
    # district largely re-test the same district-level data. Ordered
    # this way, each pass is complete national coverage on its own and
    # stopping early still leaves a result that means something.
    jobs = []
    for nth in range(args.per_outcode):
        for outcode in districts:
            postcodes = cache.get(outcode, [])
            if nth < len(postcodes) and postcodes[nth] not in done:
                jobs.append((outcode, postcodes[nth]))
    no_postcodes = [o for o in districts if not cache.get(o)]
    if no_postcodes:
        print(f"{len(no_postcodes)} districts yielded no postcode from postcodes.io "
              f"(e.g. {', '.join(no_postcodes[:6])})")

    per_pass = sum(1 for o in districts if cache.get(o))
    print(f"\n{len(jobs)} reports to check against {base}, {args.concurrency} at a time")
    print(f"breadth first: {per_pass} districts per pass, up to {args.per_outcode} passes")
    print(f"writing to {log}\n")

    failures, checked, slow = 0, 0, 0
    started = time.monotonic()
    concurrency = args.concurrency
    async with httpx.AsyncClient(timeout=120, headers=UA, follow_redirects=True) as client:
        with log.open("a", encoding="utf-8") as fh:
            # An explicit cursor, not range(0, len(jobs), concurrency):
            # the step is fixed when range() is built, so backing the
            # concurrency off mid-run left gaps and the first trial
            # silently checked 13 of its 30 postcodes.
            cursor = 0
            while cursor < len(jobs):
                batch = jobs[cursor: cursor + concurrency]
                cursor += len(batch)
                results = await asyncio.gather(*(_check(client, base, o, p) for o, p in batch))
                batch_failures = 0
                for res in results:
                    fh.write(json.dumps(res) + "\n")
                    checked += 1
                    if not res["ok"]:
                        failures += 1
                        batch_failures += 1
                        print(f"  FAIL  {res['postcode']:<10} ({res['outcode']}) "
                              f"{'; '.join(res['problems'])}")
                    elif res["seconds"] >= 20:
                        slow += 1
                        print(f"  slow  {res['postcode']:<10} ({res['outcode']}) {res['seconds']}s")
                fh.flush()

                # Any 5xx that survived its retries means the site is
                # struggling under this sweep, so ease off at once
                # rather than waiting for the batch threshold.
                infra = sum(1 for res in results if res.get("infra"))
                if infra and concurrency > 1:
                    concurrency = max(1, concurrency - 1)
                    print(f"  ** {infra} request(s) still 5xx after retries: this is load, "
                          f"not the site. Easing to {concurrency} at a time and pausing 60s")
                    await asyncio.sleep(60)

                # A cliff is far more likely to be our own rate limiting
                # than a sudden crop of broken districts, so back off
                # rather than logging hundreds of manufactured failures.
                elif batch_failures / max(1, len(batch)) > FAILURE_ALARM and concurrency > 1:
                    concurrency = max(1, concurrency - 1)
                    print(f"  ** {batch_failures}/{len(batch)} failed in one batch: "
                          f"easing off to {concurrency} at a time and pausing 30s")
                    await asyncio.sleep(30)

                if checked % 50 == 0:
                    rate = checked / max(0.001, time.monotonic() - started)
                    left = (len(jobs) - checked) / max(0.001, rate)
                    print(f"  ... {checked}/{len(jobs)}  {failures} failed  "
                          f"{rate * 60:.0f}/min  ~{left / 3600:.1f}h left")
                await asyncio.sleep(BATCH_PAUSE_S)

    elapsed = time.monotonic() - started
    print(f"\n{'=' * 66}")
    print(f"{checked} reports in {elapsed / 60:.1f} min: "
          f"{checked - failures} ok, {failures} failed, {slow} slow (>=20s)")
    if failures:
        print(f"\nfailures are in {log}; group them with:")
        print(f"  .venv/Scripts/python.exe scripts/sweep_postcodes.py --summarise {log}")
    return 1 if failures else 0


def summarise(path: str) -> int:
    """Group a log by failure reason, so 400 lines become a few causes."""
    import collections

    rows = [json.loads(line) for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    bad = [r for r in rows if not r["ok"]]
    print(f"{len(rows)} checked, {len(bad)} failed ({len(bad) / max(1, len(rows)) * 100:.1f}%)")
    if not bad:
        print("no failures")
        return 0
    by_reason = collections.Counter(p for r in bad for p in r["problems"])
    print("\nby reason:")
    for reason, count in by_reason.most_common():
        examples = [r["postcode"] for r in bad if reason in r["problems"]][:5]
        print(f"  {count:>5}  {reason}")
        print(f"         e.g. {', '.join(examples)}")
    return 1


if __name__ == "__main__":
    if "--summarise" in sys.argv:
        sys.exit(summarise(sys.argv[sys.argv.index("--summarise") + 1]))
    sys.exit(asyncio.run(main()))
