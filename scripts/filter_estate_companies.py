"""Reduce Companies House's monthly basic company data snapshot to the
residents' and estate management companies the site holds.

    .venv/Scripts/python.exe scripts/filter_estate_companies.py E:/Claude/PropertyValue-data/BasicCompanyData-2026-09-01.zip

Writes estate_companies.csv next to the zip. Keeps active companies
whose SIC is 98000 (Residents property management), or whose name says
residents, management company, estate management, freeholders or
homeowners and whose SIC is a property-management code. Around 5.7
million rows in, around 170,000 out, in about a minute. Source and
licence: Companies House Free Company Data Product, Open Government
Licence v3.
"""
from __future__ import annotations

import csv
import io
import os
import re
import sys
import zipfile

NAME_RE = re.compile(r"(RESIDENTS|RESIDENT'S|MANAGEMENT COMPANY|ESTATE MANAGEMENT|MANAGEMENT LIMITED|MANAGEMENT LTD|RMC\b|FREEHOLDERS|HOMEOWNERS|HOME OWNERS)", re.I)
PROPERTY_SICS = ("98000", "68320", "81100", "81210", "68209")


def main() -> None:
    zip_path = sys.argv[1]
    out_path = os.path.join(os.path.dirname(zip_path), "estate_companies.csv")
    z = zipfile.ZipFile(zip_path)
    member = [n for n in z.namelist() if n.lower().endswith(".csv")][0]
    kept, total = [], 0
    with z.open(member) as fh:
        for row in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace")):
            total += 1
            row = {k.strip(): (v or "") for k, v in row.items() if k}
            if row.get("CompanyStatus", "").strip().lower() != "active":
                continue
            sics = [row.get(f"SICCode.SicText_{i}", "") for i in range(1, 5)]
            name = row.get("CompanyName", "")
            if not (any(s.startswith("98000") for s in sics)
                    or (NAME_RE.search(name) and any(s.startswith(PROPERTY_SICS) for s in sics))):
                continue
            kept.append({
                "company_number": row.get("CompanyNumber", "").strip(), "name": name.strip(),
                "incorporated": row.get("IncorporationDate", "").strip(),
                "address": ", ".join(x.strip() for x in (
                    row.get("RegAddress.CareOf", ""), row.get("RegAddress.AddressLine1", ""),
                    row.get("RegAddress.AddressLine2", ""), row.get("RegAddress.PostTown", "")) if x.strip()),
                "care_of": row.get("RegAddress.CareOf", "").strip(),
                "post_town": row.get("RegAddress.PostTown", "").strip(),
                "postcode": row.get("RegAddress.PostCode", "").strip().upper(),
                "sic1": sics[0].strip(), "category": row.get("CompanyCategory", "").strip(),
            })
    with open(out_path, "w", newline="", encoding="utf-8") as out:
        w = csv.DictWriter(out, fieldnames=list(kept[0].keys()))
        w.writeheader()
        w.writerows(kept)
    print(f"scanned {total:,}; kept {len(kept):,}; wrote {out_path}")


if __name__ == "__main__":
    main()
