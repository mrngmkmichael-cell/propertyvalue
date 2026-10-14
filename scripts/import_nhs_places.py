"""Pharmacies, dentists and hospitals in England, from the official
registers, into app/data/nhs_places.json for the report's "Nearby
essentials" card.

Why (14 Oct 2026): the card read them from OpenStreetMap until its
Overpass service began refusing this site. These are the registers the
NHS and its regulator keep:

  - Pharmacies: NHS England's Organisation Data Service, every active
    organisation with the primary role PHARMACY (RO182), through the
    public ORD API (directory.spineservices.nhs.uk).
  - Dentists and hospitals: the Care Quality Commission's care directory,
    every registered location whose service types include "Dentist" or
    "Hospital". "Hospital" is the CQC's own service type: NHS and private
    hospitals, not clinics or mental health hospitals, which it lists
    under other types. Picking hospitals out of NHS trust sites by name
    would have been a guess.
  - postcodes.io (ONS Postcode Directory) for each postcode's position.

Both registers cover England. Which nations each list actually holds is
written beside it, so the card can say a list does not cover an address
instead of counting none there.

Refresh: monthly, with the other imports. The CQC publishes a new care
directory each week; pharmacies open and close every month.

    .venv/Scripts/python.exe scripts/import_nhs_places.py
"""
import csv
import io
import json
import pathlib
import re
import sys
import time
from collections import Counter

import httpx

OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "nhs_places.json"
ORD = "https://directory.spineservices.nhs.uk/ORD/2-0-0/organisations"
CQC_PAGE = "https://www.cqc.org.uk/about-us/transparency/using-cqc-data"
UA = {"User-Agent": "UKPropertyInsight data import (support@ukpropertyinsight.co.uk)"}
KEEP_UPPER = {"UK", "NHS", "LLP", "PLC", "CIC", "GP", "MSK", "ENT"}
# A nation counts as covered by a list when it holds at least this many
# places there; a handful over a border is not coverage.
MIN_FOR_COVERAGE = 50


def tidy(name: str) -> str:
    """'ROWLANDS PHARMACY' -> 'Rowlands Pharmacy'. The ODS writes names in
    capitals; the CQC's are already written as the practices write them."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name.isupper():
        return name
    words = []
    for word in name.split(" "):
        bare = re.sub(r"[^A-Z]", "", word)
        words.append(word if bare in KEEP_UPPER else word[:1] + word[1:].lower())
    return " ".join(words)


def pharmacies(client: httpx.Client) -> list[tuple[str, str]]:
    out, offset, total = [], 0, None
    while total is None or offset < total:
        # Offset 0 is refused ("must be greater than 1"); the first page
        # goes without one, and later offsets count from zero.
        params = {"PrimaryRoleId": "RO182", "Status": "Active", "Limit": 1000}
        if offset:
            params["Offset"] = offset
        r = client.get(ORD, params=params)
        r.raise_for_status()
        total = int(r.headers.get("X-Total-Count", "0"))
        page = r.json().get("Organisations", [])
        if not page:
            break
        out += [(tidy(o["Name"]), o.get("PostCode") or "") for o in page]
        offset += len(page)
        time.sleep(0.3)
    assert total and len(out) >= total * 0.98, (len(out), total)
    return out


def cqc(client: httpx.Client) -> tuple[str, list[tuple[str, str, str]]]:
    page = client.get(CQC_PAGE).text
    m = re.search(r'href="([^"]+_CQC_directory\.csv)"', page)
    assert m, "the CQC care directory link was not found"
    url = m.group(1) if m.group(1).startswith("http") else "https://www.cqc.org.uk" + m.group(1)
    raw = client.get(url, timeout=300).content.decode("utf-8-sig", errors="replace")
    lines = raw.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Name,"))
    out = []
    for row in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        types = {t.strip() for t in (row.get("Service types") or "").split("|")}
        for kind, service in (("dentist", "Dentist"), ("hospital", "Hospital")):
            if service in types:
                # A provider's registered head office carries its service
                # types without seeing a patient there ("Trust Headquarters",
                # "One Health Group - Head Office"): six such hospitals on
                # 14 Oct 2026. Not a place anyone is treated, so left out.
                if kind == "hospital" and re.search(r"head ?office|headquarters", row["Name"], re.I):
                    continue
                out.append((kind, tidy(row["Name"]), row.get("Postcode") or ""))
    return url, out


def geocode(client: httpx.Client, postcodes: set[str]) -> dict[str, tuple[float, float, str]]:
    out = {}
    todo = sorted(p for p in postcodes if p)
    for i in range(0, len(todo), 100):
        batch = todo[i:i + 100]
        r = client.post("https://api.postcodes.io/postcodes", json={"postcodes": batch}, timeout=60)
        r.raise_for_status()
        for item in r.json().get("result", []):
            res = item.get("result")
            if res and res.get("latitude") is not None:
                out[item["query"]] = (round(res["latitude"], 6), round(res["longitude"], 6), res.get("country") or "")
        time.sleep(0.2)
    return out


def main() -> None:
    with httpx.Client(timeout=120, headers=UA) as client:
        ph = pharmacies(client)
        cqc_url, care = cqc(client)
        where = geocode(client, {pc for _, pc in ph} | {pc for _, _, pc in care})
    rows, missing = [], Counter()
    for kind, name, postcode in [("pharmacy", n, pc) for n, pc in ph] + care:
        if postcode not in where:
            missing[kind] += 1
            continue
        lat, lon, country = where[postcode]
        rows.append([kind, name, lat, lon, country])
    counts = Counter((r[0], r[4]) for r in rows)
    coverage = {kind: sorted(c for (k, c), n in counts.items() if k == kind and n >= MIN_FOR_COVERAGE)
                for kind in ("pharmacy", "dentist", "hospital")}
    totals = Counter(r[0] for r in rows)
    assert totals["pharmacy"] > 10000 and totals["dentist"] > 10000 and totals["hospital"] > 500, totals
    OUT.write_text(json.dumps({
        "sources": {"pharmacy": "NHS England Organisation Data Service, role RO182 (PHARMACY)",
                    "dentist": f"Care Quality Commission care directory ({cqc_url.rsplit('/', 1)[-1]})",
                    "hospital": f"Care Quality Commission care directory ({cqc_url.rsplit('/', 1)[-1]})"},
        "coverage": coverage,
        "places": rows,
    }, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT}: {dict(totals)}; not placed {dict(missing)}; coverage {coverage}")


if __name__ == "__main__":
    sys.exit(main())
