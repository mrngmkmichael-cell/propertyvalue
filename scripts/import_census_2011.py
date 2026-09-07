"""Import the 2011 Census key statistics that pair with the 2021 tables
the report already holds, so a small area can say how it changed.

Sources: ONS Census 2011 Key Statistics, Nomis bulk downloads at output
area and above (the LSOA rows are used), Open Government Licence:
- KS402EW Tenure            https://www.nomisweb.co.uk/output/census/2011/ks402ew_2011_oa.zip
- KS102EW Age structure     https://www.nomisweb.co.uk/output/census/2011/ks102ew_2011_oa.zip
- KS501EW Qualifications    https://www.nomisweb.co.uk/output/census/2011/ks501ew_2011_oa.zip
- KS204EW Country of birth  https://www.nomisweb.co.uk/output/census/2011/ks204ew_2011_oa.zip
- KS301EW Health            https://www.nomisweb.co.uk/output/census/2011/ks301ew_2011_oa.zip
- KS201EW Ethnic group      https://www.nomisweb.co.uk/output/census/2011/ks201ew_2011_oa.zip
and the ONS best-fit lookup from 2011 LSOAs to 2021 LSOAs (Open Geography
Portal, item b684a0dbf786473f9563ec0616da2f8b), so each row is keyed by
the 2021 code the rest of the site uses. Where 2011 areas merged, their
counts are summed; where one split, the best-fit child carries it and the
other child has no 2011 figure (the page says so).

The column meanings were checked against the England totals in each
file (for example KS201EW0002 is White British: 42,279,236, 79.8%).
Static data: the next refresh is the 2031 census.

    python scripts/import_census_2011.py [dir with the zips]

Downloads what is missing into a temp dir; about 230 MB.
"""
import csv
import datetime
import io
import json
import os
import sys
import tempfile
import zipfile

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from sqlalchemy import func, select  # noqa: E402

from app import db  # noqa: E402
from app.models import AgeProfile, Census2011, CountryOfBirth, Ethnicity, GeneralHealth, Qualification, Tenure  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTEXT_PATH = os.path.join(ROOT, "app", "data", "census_change_context.json")
NOMIS = "https://www.nomisweb.co.uk/output/census/2011/{table}_2011_oa.zip"
LOOKUP = "https://open-geography-portalx-ons.hub.arcgis.com/api/download/v1/items/b684a0dbf786473f9563ec0616da2f8b/csv?layers=0"
TABLES = ("ks402ew", "ks102ew", "ks501ew", "ks204ew", "ks301ew", "ks201ew")

# column name -> (table, [column codes to sum])
FIELDS = {
    "households": ("ks402ew", ["KS402EW0001"]),
    "owned": ("ks402ew", ["KS402EW0002", "KS402EW0003"]),
    "private_rented": ("ks402ew", ["KS402EW0007", "KS402EW0008"]),
    "social_rented": ("ks402ew", ["KS402EW0005", "KS402EW0006"]),
    "residents": ("ks102ew", ["KS102EW0001"]),
    "under_15": ("ks102ew", ["KS102EW0002", "KS102EW0003", "KS102EW0004", "KS102EW0005"]),
    "over_65": ("ks102ew", ["KS102EW0014", "KS102EW0015", "KS102EW0016", "KS102EW0017"]),
    "adults": ("ks501ew", ["KS501EW0001"]),
    "level_4_plus": ("ks501ew", ["KS501EW0007"]),
    "cob_total": ("ks204ew", ["KS204EW0001"]),
    "born_uk": ("ks204ew", ["KS204EW0002", "KS204EW0003", "KS204EW0004", "KS204EW0005", "KS204EW0006"]),
    "health_total": ("ks301ew", ["KS301EW0001"]),
    "health_good": ("ks301ew", ["KS301EW0008", "KS301EW0009"]),
    "eth_total": ("ks201ew", ["KS201EW0001"]),
    "white": ("ks201ew", ["KS201EW0002", "KS201EW0003", "KS201EW0004", "KS201EW0005"]),
}


def _fetch(url: str, path: str) -> None:
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return
    print(f"downloading {url}")
    with httpx.stream("GET", url, timeout=600, follow_redirects=True) as r, open(path, "wb") as f:
        r.raise_for_status()
        for chunk in r.iter_bytes(1 << 20):
            f.write(chunk)


def _table_rows(path: str) -> tuple[dict[str, dict], dict]:
    """LSOA rows (2011 codes) and the England row, as {code: value}."""
    z = zipfile.ZipFile(path)
    name = [n for n in z.namelist() if n.upper().endswith("DATA.CSV")][0]
    lsoas, england = {}, None
    with z.open(name) as f:
        reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
        for row in reader:
            code = row["GeographyCode"]
            if code.startswith(("E01", "W01")):
                lsoas[code] = row
            elif code == "E92000001":
                england = row
    return lsoas, england


def _sum(row: dict, cols: list[str]) -> int:
    total = 0
    for c in cols:
        try:
            total += int(float(row.get(c) or 0))
        except ValueError:
            pass
    return total


def main(argv: list[str]) -> int:
    folder = argv[1] if len(argv) > 1 else os.path.join(tempfile.gettempdir(), "census2011")
    os.makedirs(folder, exist_ok=True)
    lookup_path = os.path.join(folder, "lsoa11_to_lsoa21.csv")
    _fetch(LOOKUP, lookup_path)
    with open(lookup_path, encoding="utf-8-sig", newline="") as f:
        lookup = {r["LSOA11CD"]: r["LSOA21CD"] for r in csv.DictReader(f)}
    print(f"{len(lookup):,} 2011 areas in the lookup, {len(set(lookup.values())):,} 2021 areas")

    rows: dict[str, dict] = {}
    england_2011: dict[str, int] = {}
    tables = {}
    for t in TABLES:
        path = os.path.join(folder, f"{t}_2011_oa.zip")
        _fetch(NOMIS.format(table=t), path)
        tables[t] = _table_rows(path)
        print(f"  {t}: {len(tables[t][0]):,} LSOA rows")
    for field, (t, cols) in FIELDS.items():
        lsoas, england = tables[t]
        england_2011[field] = _sum(england, cols)
        for code11, row in lsoas.items():
            code21 = lookup.get(code11, code11)
            r = rows.setdefault(code21, {"lsoa_code": code21, "lsoa11_count": 0})
            r[field] = r.get(field, 0) + _sum(row, cols)
    for code11, code21 in lookup.items():
        if code21 in rows:
            rows[code21]["lsoa11_count"] += 1
    for r in rows.values():
        r["lsoa11_count"] = max(r["lsoa11_count"], 1)
    print(f"{len(rows):,} rows keyed by 2021 LSOA")

    db.init_db()
    with db.get_session() as session:
        session.query(Census2011).delete()
        session.commit()
        values = list(rows.values())
        for i in range(0, len(values), 2000):
            session.execute(Census2011.__table__.insert(), values[i:i + 2000])
            session.commit()
        # England 2021 from the tables already imported (England LSOAs only).
        def total(model, *cols):
            return [int(session.execute(select(func.sum(getattr(model, c))).where(model.lsoa_code.like("E01%"))).scalar() or 0) for c in cols]
        t_total, owned_o, owned_m, priv, soc = total(Tenure, "total", "owned_outright", "owned_mortgage", "private_rented", "social_rented")
        a_total, u15, a65, a85 = total(AgeProfile, "total", "under_15", "age_65_84", "age_85_plus")
        q_total, l4 = total(Qualification, "total", "level_4_plus")
        c_total, uk = total(CountryOfBirth, "total", "uk")
        h_total, vg, g = total(GeneralHealth, "total", "very_good", "good")
        e_total, white = total(Ethnicity, "total", "white")
    england_2021 = {
        "households": t_total, "owned": owned_o + owned_m, "private_rented": priv, "social_rented": soc,
        "residents": a_total, "under_15": u15, "over_65": a65 + a85, "adults": q_total, "level_4_plus": l4,
        "cob_total": c_total, "born_uk": uk, "health_total": h_total, "health_good": vg + g, "eth_total": e_total, "white": white,
    }
    context = {"england_2011": england_2011, "england_2021": england_2021, "rows": len(rows), "imported": datetime.date.today().isoformat()}
    with open(CONTEXT_PATH, "w", encoding="utf-8") as f:
        json.dump(context, f, indent=1)
    print(f"{len(rows):,} rows written; England 2011 -> 2021 private renting {england_2011['private_rented'] / england_2011['households']:.1%} -> {england_2021['private_rented'] / max(england_2021['households'], 1):.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
