"""Residents' and estate management companies from Companies House, with
the organisation whose office each is registered to.

Source: Companies House, "Free Company Data Product", the monthly basic
company data snapshot (Open Government Licence), one CSV of every live
company. Download the current month's single-file zip from
https://download.companieshouse.gov.uk/en_output.html into a folder
outside the repository, run scripts/filter_estate_companies.py to reduce
it to the companies this table holds (SIC 98000 "Residents property
management", plus property-management SICs whose names say residents,
management company, estate management, freeholders or homeowners; active
companies only), then run this with the filtered CSV:

    .venv/Scripts/python.exe scripts/import_estate_companies.py E:/Claude/PropertyValue-data/estate_companies.csv

Attribution: each company's registered office is matched to
app/data/managing_agents.json, first by postcode, then by a keyword in
the address. Everything else stays unattributed. The site's wording is
"registered to X's office", because the register records the address,
not the management contract. Re-runnable: replaces the table.
Refresh monthly with the new snapshot.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import insert  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import Base, _get_engine  # noqa: E402
from app.models import EstateCompany  # noqa: E402

AGENTS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "data", "managing_agents.json")


def _agents() -> tuple[dict[str, str], list[tuple[str, str]]]:
    data = json.load(open(AGENTS_PATH, encoding="utf-8"))
    by_postcode, keywords = {}, []
    for a in data["agents"]:
        for pc in a.get("postcodes", []):
            by_postcode[pc.upper().replace(" ", "")] = a["slug"]
        for kw in a.get("keywords", []):
            keywords.append((kw.upper(), a["slug"]))
    # Longer keywords first so "RENDALL AND RITTNER" wins over "RMG" inside other words.
    keywords.sort(key=lambda kv: -len(kv[0]))
    return by_postcode, keywords


def _date(value: str) -> dt.date | None:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "E:/Claude/PropertyValue-data/estate_companies.csv"
    by_postcode, keywords = _agents()
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    print(f"{len(rows):,} companies in {path}")
    records, attributed = [], 0
    for r in rows:
        pc = (r.get("postcode") or "").upper().replace(" ", "")
        addr = (r.get("address") or "").upper()
        slug = by_postcode.get(pc, "")
        if not slug:
            for kw, s in keywords:
                if kw in addr:
                    slug = s
                    break
        if slug:
            attributed += 1
        records.append({
            "company_number": r["company_number"][:12], "name": r["name"][:255],
            "incorporated": _date(r.get("incorporated") or ""),
            "address": (r.get("address") or "")[:300], "post_town": (r.get("post_town") or "")[:120],
            "postcode": (r.get("postcode") or "")[:12], "agent_slug": slug,
            "category": (r.get("category") or "")[:80], "sic": (r.get("sic1") or "")[:80],
        })
    print(f"  attributed {attributed:,} to a named office")

    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[EstateCompany.__table__])
    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        print("Replacing estate_companies...")
        session.query(EstateCompany).delete()
        for i in range(0, len(records), 5000):
            session.execute(insert(EstateCompany), records[i:i + 5000])
        session.commit()
    print("Done.")


if __name__ == "__main__":
    main()
