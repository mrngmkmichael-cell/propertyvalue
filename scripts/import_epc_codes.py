"""Fetch the EPC improvement-measure names from the Energy Performance of
Buildings data service and save them as app/data/epc_improvement_codes.json.

Source: MHCLG, "Get energy performance of buildings data", the codes-info
endpoint (GET /api/codes/info?code=improvement_summary&key=N), Open
Government Licence. A domestic certificate lists its recommended measures
as numbered codes (suggested_improvements[].improvement_details.
improvement_number); this file turns 7 into "50 mm internal or external
wall insulation". The list is the RdSAP methodology standard and changes
rarely; re-run after a schema change (the certificate JSON reports its
schema_version). Needs EPC_API_TOKEN in .env. Takes about a minute.
"""
import json
import os
import pathlib
import sys

import httpx
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
API_BASE = "https://api.get-energy-performance-data.communities.gov.uk"
OUT = ROOT / "app" / "data" / "epc_improvement_codes.json"
MAX_KEY = 150
STOP_AFTER_MISSES = 25


def main() -> int:
    token = os.environ.get("EPC_API_TOKEN")
    if not token:
        print("EPC_API_TOKEN is not set", file=sys.stderr)
        return 1
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    codes: dict[str, dict] = {}
    misses = 0
    with httpx.Client(timeout=20, headers=headers) as client:
        for key in range(1, MAX_KEY + 1):
            entry = {}
            for code in ("improvement_summary", "improvement_description"):
                r = client.get(f"{API_BASE}/api/codes/info", params={"code": code, "key": key})
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                values = r.json()["data"][0]["values"]
                # The wording is the same across schema versions; take the newest.
                values.sort(key=lambda v: v.get("schemaVersion", ""), reverse=True)
                entry[code.split("_")[1]] = values[0]["value"]
            if entry:
                codes[str(key)] = entry
                misses = 0
            else:
                misses += 1
                if misses >= STOP_AFTER_MISSES:
                    break
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(codes, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(codes)} improvement codes written to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
