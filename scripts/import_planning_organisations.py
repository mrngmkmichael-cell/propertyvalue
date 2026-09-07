"""Save the planning data platform's organisation list as
app/data/planning_organisations.json, keyed by GSS code.

Source: MHCLG, planning.data.gov.uk, the organisation dataset
(https://files.planning.data.gov.uk/organisation-collection/dataset/organisation.csv),
Open Government Licence. Every local authority and national park authority
carries its ONS statistical geography code, which is what postcodes.io
gives a property (codes.admin_district), and its platform entity number,
which is how the brownfield-land dataset says which council published a
site. 339 live authorities on 7 Sep 2026. Councils change rarely
(reorganisations, usually 1 April); re-run after one, or yearly.
"""
import csv
import io
import json
import pathlib
import sys

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
URL = "https://files.planning.data.gov.uk/organisation-collection/dataset/organisation.csv"
OUT = ROOT / "app" / "data" / "planning_organisations.json"
KEEP = {"local-authority", "national-park-authority"}


def main() -> int:
    text = httpx.get(URL, timeout=60, follow_redirects=True).raise_for_status().text
    rows = csv.DictReader(io.StringIO(text))
    out = {}
    for r in rows:
        if r["dataset"] not in KEEP or r.get("end-date") or not r.get("statistical-geography"):
            continue
        out[r["statistical-geography"]] = {
            "entity": int(r["entity"]),
            "name": r["name"],
            "type": r.get("local-authority-type") or r["dataset"],
        }
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(out)} authorities written to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
