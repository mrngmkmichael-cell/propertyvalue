"""Snapshot every page's English HTML, or compare against a snapshot.

Marking 1,800 strings up for translation is a huge mechanical edit to
templates, and the one property that proves it did no harm is this: with
the language set to English, every page must render byte-for-byte what
it rendered before. Anything else is a bug the eye would never catch
across 46 templates.

    python scripts/i18n_snapshot.py save    # before the edit
    python scripts/i18n_snapshot.py check   # after it
"""
import hashlib
import json
import re
import pathlib
import sys

import httpx

BASE = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8010"
OUT = pathlib.Path("scratch_i18n_snapshot.json")
# A crawler UA so cold reports render fully rather than returning the
# 202 "building" placeholder, which would snapshot nothing useful.
UA = {"User-Agent": "Googlebot/2.1 (+i18n-snapshot)"}

PAGES = [
    "/", "/areas", "/area/M1", "/area/SW1A", "/area/EH1", "/area/W4/private-schools",
    "/schools/guide?q=M1", "/schools/shortlist", "/schools/outstanding",
    "/school/100050/parliament-hill-school",
    "/buying-guide", "/browser-extension", "/premium", "/accuracy", "/data",
    "/methodology", "/support", "/terms", "/privacy", "/market-report",
    "/login", "/signup", "/forgot-password", "/embed",
    "/property?postcode=M1+1AE", "/property?postcode=EH1+1YZ",
    "/property/comparables?postcode=M1+1AE", "/property/checklist?postcode=M1+1AE",
    "/watchlist", "/watchlist/compare", "/compare", "/compare/M20/vs/M21",
    "/compare?postcode=M1+1AE&postcode=LS1+4DY",
    "/tools/stamp-duty-calculator", "/tools/mortgage-calculator",
    "/market/district-prices", "/market/house-prices/london",
    "/market/house-prices/north-west", "/no-such-page-404",
]


def normalise(html: str) -> str:
    """Collapse runs of whitespace.

    The marker rewrites a text run that was indented across three lines
    into one tidy line, because the catalogue key has to be one line to
    be reviewable. HTML collapses any run of whitespace to a single
    space when it renders, so those two are the same page. Comparing
    normalised text therefore still catches everything that matters,
    a changed word, a dropped sentence, a double-escaped entity, while
    ignoring the one difference that is known and harmless.
    """
    return re.sub(r"\s+", " ", html).strip()


def fetch_all():
    client = httpx.Client(timeout=90.0, headers=UA, follow_redirects=True)
    out = {}
    for path in PAGES:
        try:
            r = client.get(BASE + path)
            out[path] = {"status": r.status_code,
                         "sha": hashlib.sha256(normalise(r.text).encode("utf-8")).hexdigest(),
                         "len": len(r.text), "body": r.text}
        except Exception as exc:  # noqa: BLE001
            out[path] = {"status": "ERROR", "sha": str(exc), "len": 0, "body": ""}
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    now = fetch_all()
    if mode == "save":
        OUT.write_text(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "body"}
                                   for k, v in now.items()}, indent=1), encoding="utf-8")
        # Bodies go in a separate file so a diff can show what changed.
        pathlib.Path("scratch_i18n_bodies.json").write_text(
            json.dumps({k: v["body"] for k, v in now.items()}), encoding="utf-8")
        print(f"saved {len(now)} pages")
        return

    before = json.loads(OUT.read_text(encoding="utf-8"))
    bodies = json.loads(pathlib.Path("scratch_i18n_bodies.json").read_text(encoding="utf-8"))
    same = differ = 0
    for path, was in before.items():
        is_ = now.get(path, {})
        if was["sha"] == is_.get("sha"):
            same += 1
            continue
        differ += 1
        print(f"\nDIFFERS  {path}  ({was['len']} -> {is_.get('len')} bytes)")
        a, b = normalise(bodies.get(path, "")), normalise(is_.get("body", ""))
        for i, (ca, cb) in enumerate(zip(a, b)):
            if ca != cb:
                print(f"  first difference at byte {i}:")
                print("    was: ..." + a[max(0, i-70):i+70].replace("\n", "\n"))
                print("    now: ..." + b[max(0, i-70):i+70].replace("\n", "\n"))
                break
        else:
            print(f"  identical for {min(len(a), len(b))} bytes, then one ends")
    print(f"\n{same} identical, {differ} changed")
    sys.exit(1 if differ else 0)


main()
