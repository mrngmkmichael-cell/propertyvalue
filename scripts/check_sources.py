"""Which of the report's data sources are actually answering right now.

Runs the real property gather against real upstreams for a handful of
addresses and prints, per source, whether it returned data, returned
nothing, or failed. smoke.py deliberately does not do this: it checks
that pages render, and treats a source being down as normal.

Pacing is the whole trick. DEFRA, the Environment Agency and Police.uk
throttle by IP. Sweeping several addresses back to back once produced a
run where Noise looked broken everywhere, which it was not: the sweep
had throttled itself and then read its own rate limiting as a fault.
So addresses are walked one at a time with a real gap between them, and
a source is only called broken when it fails for every address tried.

    .venv/Scripts/python.exe scripts/check_sources.py
    .venv/Scripts/python.exe scripts/check_sources.py --quick

Exit code is 1 if any source failed everywhere it was tried.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.main import _full_property_gather  # noqa: E402
from app.services.postcodes import lookup_postcode  # noqa: E402

# Spread across all four nations on purpose. Several sources are
# England-only infrastructure with no equivalent elsewhere, and a sweep
# of English postcodes alone would never show that.
ADDRESSES = [
    ("M1 1AE", "England"),
    ("SW1A 1AA", "England"),
    ("LS1 4DY", "England"),
    ("EH1 1YZ", "Scotland"),
    ("CF10 1EP", "Wales"),
    ("BT1 5GS", "Northern Ireland"),
]
QUICK = ["M1 1AE", "EH1 1YZ"]

# Sources with no coverage outside England. Absent data for these is the
# correct answer there, not a fault, and the report says so on the page.
ENGLAND_ONLY = {
    "flood_zone", "flood", "surface_water", "radon", "coal_mining",
    "historic_landfill", "sewage", "designations", "heritage",
    "air_quality", "catchment",
}

# The gap between addresses. Long enough that the rate limiters do not
# see a burst.
PACE_S = 20


# The gather's own sources, being every name it can set a <name>_error
# flag for. Listed rather than discovered: the flag is only set when a
# source FAILS, so a healthy run leaves none of them on the context and
# discovery finds nothing at all. Kept honest by _check_list_is_current
# below, which fails loudly if main.py grows or loses one.
SOURCES = [
    "age_profile", "air_quality", "amenities", "background", "broadband",
    "catchment", "clay_risk", "coal_mining", "crime", "deprivation",
    "designations", "epc", "flood", "flood_zone", "food_hygiene",
    "google_ratings", "heritage", "historic_landfill", "household_income",
    "housing", "mobile", "noise", "occupation", "orientation", "price_trend",
    "qualification", "radon", "rental", "schools", "sewage", "surface_water",
    "tx", "valuation", "wellbeing",
]

# Where a source's result actually lands on the context, when the key is
# not simply the source name.
RESULT_KEY = {
    "tx": "transactions",
    "epc": "certificates",
    "schools": "school_landscape",
    "sewage": "sewage_outfalls",
    "amenities": "amenities",
}


def _check_list_is_current() -> list[str]:
    """SOURCES has to track main.py by hand, so say so when it stops."""
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parent.parent / "app" / "main.py"
    body = src.read_text(encoding="utf-8")
    start = body.index("async def _full_property_gather")
    rest = body[start:]
    end = re.search(r"\n(@app\.|def |async def )", rest[10:])
    gather = rest[: end.start() + 10] if end else rest
    found = set(re.findall(r'context\["([a-z_]+)_error"\]', gather))
    return sorted(found.symmetric_difference(SOURCES))


def _sources(context: dict) -> dict:
    """Each source as name -> "ok" | "empty" | "failed"."""
    out = {}
    for name in SOURCES:
        if context.get(f"{name}_error"):
            out[name] = "failed"
            continue
        value = context.get(RESULT_KEY.get(name, name))
        out[name] = "ok" if value not in (None, [], {}, 0, "", False) else "empty"
    return out


async def main() -> int:
    drifted = _check_list_is_current()
    if drifted:
        print("SOURCES no longer matches _full_property_gather. Differs on:")
        for name in drifted:
            print(f"  - {name}")
        return 1

    quick = "--quick" in sys.argv
    addresses = [(p, c) for p, c in ADDRESSES if not quick or p in QUICK]

    results: dict[str, dict[str, str]] = {}
    for index, (postcode, country) in enumerate(addresses):
        if index:
            print(f"  (pausing {PACE_S}s so the rate limiters do not see a burst)")
            time.sleep(PACE_S)
        print(f"\n=== {postcode} ({country})")
        started = time.monotonic()
        location = await lookup_postcode(postcode)
        if location is None:
            print("    postcode lookup failed, skipping")
            continue
        context = await _full_property_gather(location, "", False, wait_for_amenities=True)
        elapsed = time.monotonic() - started
        found = _sources(context)
        results[postcode] = found
        counts = {state: sum(1 for v in found.values() if v == state) for state in ("ok", "empty", "failed")}
        print(f"    {len(found)} sources in {elapsed:.1f}s: "
              f"{counts['ok']} ok, {counts['empty']} empty, {counts['failed']} failed")
        broken = sorted(n for n, v in found.items() if v == "failed")
        if broken:
            print(f"    failed here: {', '.join(broken)}")

    if not results:
        print("\nnothing was measured")
        return 1

    every = sorted({name for found in results.values() for name in found})
    print(f"\n{'=' * 74}\n{len(every)} sources across {len(results)} addresses\n{'=' * 74}")
    header = "source".ljust(24) + "".join(p.split()[0].ljust(9) for p in results)
    print(header)

    always_failed = []
    for name in every:
        row = name.ljust(24)
        states = []
        for postcode in results:
            # "n/a" means the gather never reported on this source for
            # this address, which is how a source that does not apply to
            # the nation shows up.
            state = results[postcode].get(name, "n/a")
            states.append(state)
            row += {"ok": "ok", "empty": "empty", "failed": "FAILED"}.get(state, "n/a").ljust(9)
        print(row)
        # England-only sources returning nothing elsewhere is the correct
        # answer, so they only count as broken on the English addresses.
        relevant = [
            s for (p, c), s in zip(addresses, states)
            if not (name in ENGLAND_ONLY and c != "England")
        ]
        if relevant and all(s == "failed" for s in relevant):
            always_failed.append(name)

    print()
    if always_failed:
        print(f"{len(always_failed)} source(s) failed everywhere they were tried:")
        for name in always_failed:
            print(f"  - {name}")
        return 1
    print("no source failed everywhere it was tried")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
