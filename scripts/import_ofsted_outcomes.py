"""Refresh every school's Ofsted outcome in place, reading the monthly
file in full: graded grades, ungraded-inspection outcomes and the
November-2025 report cards. See app/services/ofsted_outcomes.py for why
one column was never enough.

Source: Ofsted, "Management information - state-funded schools - latest
inspections", republished monthly at a new URL. Find the current file at
https://www.gov.uk/government/statistical-data-sets/monthly-management-information-ofsteds-school-inspections-outcomes
and update OFSTED_URL (kept in scripts/import_schools.py) before a run.

Updates only: no row is deleted, so the site keeps serving throughout.
Adds the columns it needs with ADD COLUMN IF NOT EXISTS, so it is safe
to run on a database that predates them. Re-run monthly, after
import_schools.py if that has been run too.

    .venv/Scripts/python.exe scripts/import_ofsted_outcomes.py
"""
from __future__ import annotations

import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from sqlalchemy import select, text, update  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from app.db import _get_engine  # noqa: E402
from app.models import School, SchoolDetail  # noqa: E402
from app.services.ofsted_outcomes import CARD_AREAS, derive  # noqa: E402
from import_schools import OFSTED_URL, _decode  # noqa: E402

NEW_COLUMNS = [
    "ALTER TABLE schools ADD COLUMN IF NOT EXISTS ofsted_note VARCHAR(160) NOT NULL DEFAULT ''",
    "ALTER TABLE school_details ADD COLUMN IF NOT EXISTS ofsted_card_date DATE",
] + [
    f"ALTER TABLE school_details ADD COLUMN IF NOT EXISTS ofsted_card_{key} VARCHAR(24) NOT NULL DEFAULT ''"
    for key, _c, _l in CARD_AREAS
]


def main() -> None:
    engine = _get_engine()
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            for stmt in NEW_COLUMNS:
                conn.execute(text(stmt))
            print(f"ensured {len(NEW_COLUMNS)} columns")

    print(f"Downloading {OFSTED_URL}")
    resp = httpx.get(OFSTED_URL, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(_decode(resp.content))))
    print(f"  {len(rows)} rows in the Ofsted file")

    SessionLocal = sessionmaker(bind=engine)
    with SessionLocal() as session:
        known = set(session.execute(select(School.urn)).scalars().all())
        detailed = set(session.execute(select(SchoolDetail.urn)).scalars().all())
    school_updates, detail_updates = [], []
    counts = {"graded": 0, "ungraded": 0, "card": 0, "none": 0}
    for row in rows:
        urn_raw = (row.get("URN") or "").strip()
        if not urn_raw.isdigit() or int(urn_raw) not in known:
            continue
        urn = int(urn_raw)
        d = derive(row)
        if d["card"]:
            counts["card"] += 1
        elif d["note"]:
            counts["ungraded"] += 1
        elif d["rating"]:
            counts["graded"] += 1
        else:
            counts["none"] += 1
        school_updates.append({
            "urn": urn, "ofsted_rating": d["rating"], "ofsted_rating_label": d["rating_label"],
            "ofsted_inspection_date": d["inspection_date"], "ofsted_note": d["note"][:160],
        })
        if urn in detailed:
            upd = {"urn": urn, "ofsted_card_date": d["card_date"]}
            for key, _c, _l in CARD_AREAS:
                upd[f"ofsted_card_{key}"] = d["card"].get(key, "")
            detail_updates.append(upd)

    print(f"  matched {len(school_updates)} schools: {counts}")
    with SessionLocal() as session:
        for i in range(0, len(school_updates), 2000):
            session.execute(update(School), school_updates[i:i + 2000])
        for i in range(0, len(detail_updates), 2000):
            session.execute(update(SchoolDetail), detail_updates[i:i + 2000])
        session.commit()
    print("Done.")


if __name__ == "__main__":
    main()
