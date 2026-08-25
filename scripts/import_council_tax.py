"""Import Band D council tax per English billing authority.

Source: MHCLG, "Council Tax levels set by local authorities in England"
(Table 10, Data_Billing sheet, line 17: the average Band D two-adult
charge for the authority's area including all precepts). Published
annually each March; re-run this script against the new year's ODS URL
and commit the refreshed JSON.

Usage: python scripts/import_council_tax.py
"""
import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

ODS_URL = "https://assets.publishing.service.gov.uk/media/6a02eeeccd2e0e8b5b20b449/Table_10_2026-27.ods"
YEAR_LABEL = "2026-27"
OUT = Path(__file__).resolve().parent.parent / "app" / "data" / "council_tax.json"

NS = {"table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
      "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0"}
T = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"


def cells(row, cap=60):
    out = []
    for c in row.findall("table:table-cell", NS):
        rep = int(c.get(T + "number-columns-repeated", "1"))
        txt = " ".join("".join(p.itertext()) for p in c.findall("text:p", NS))
        out.extend([txt] * min(rep, cap))
        if len(out) > cap:
            break
    return out[:cap]


def main() -> None:
    print(f"Downloading {ODS_URL}")
    ods = httpx.get(ODS_URL, timeout=120, follow_redirects=True).content
    root = ET.fromstring(zipfile.ZipFile(BytesIO(ods)).read("content.xml"))
    tbl = next(t for t in root.findall(".//table:table", NS)
               if t.get(T + "name") == "Data_Billing")
    rows = [cells(r) for r in tbl.findall("table:table-row", NS)]
    header = rows[4]
    col = next(i for i, h in enumerate(header)
               if h.startswith("17. Average (Band D 2 adult equivalent) council tax for area"))
    print(f"Band D area-charge column: {col}")

    data = {}
    for r in rows[5:]:
        if len(r) <= col:
            continue
        ons, name, value = r[1].strip(), r[2].strip(), r[col].strip().replace(",", "")
        if not ons.startswith("E0") or not value:
            continue
        try:
            data[ons] = {"authority": name, "band_d": round(float(value), 2)}
        except ValueError:
            continue

    assert len(data) > 250, f"only {len(data)} authorities parsed - source layout changed?"
    sample = data.get("E07000223")
    assert sample and 1000 < sample["band_d"] < 4000, f"Adur sanity check failed: {sample}"

    OUT.write_text(json.dumps({"year": YEAR_LABEL, "source": ODS_URL,
                               "authorities": data}, indent=1), encoding="utf-8")
    print(f"Wrote {len(data)} authorities to {OUT}")


if __name__ == "__main__":
    sys.exit(main())
