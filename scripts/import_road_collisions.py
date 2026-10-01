"""Reported road collisions, from the DfT's STATS19 returns, into a
compact local file rather than the database.

Source: https://www.data.gov.uk/dataset/cb7ae6f0-4be6-4935-9277-47e5ce24a11f
File:   https://data.dft.gov.uk/road-accidents-safety-data/
        dft-road-casualty-statistics-collision-last-5-years.csv
Refresh: once a year. DfT publishes the final year each autumn, with the
         most recent year provisional until then.

Every collision the police attended is a row with a latitude, a longitude,
a severity and a casualty count, so "how often has someone been hurt on
this road" is answerable for an address rather than for a district. That
is the whole reason this is worth importing: nothing else in the report
tells a buyer what the junction outside is actually like, and nobody
selling the house will mention it.

**Why a file and not a table.** The five-year file is around half a
million rows. On 1 Oct 2026 the Neon database was at 499 MB of 512 MB,
and a collisions table with its index would have been 40 MB or more of
that, on top of a round trip per report. Packed to fourteen bytes a row
this is about 7 MB on disk, read once into three arrays at first use,
and answered in microseconds with no database involved. Coverage is
Great Britain: Northern Ireland's collisions are published by the PSNI
and are not in this file, so the card says so rather than reading as
none.

    .venv/Scripts/python.exe scripts/import_road_collisions.py

Writes app/data/road_collisions.bin. Re-runnable: the file is replaced.
"""
import array
import csv
import datetime as dt
import io
import json
import os
import struct
import sys

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CSV_URL = ("https://data.dft.gov.uk/road-accidents-safety-data/"
           "dft-road-casualty-statistics-collision-last-5-years.csv")
DATASET_PAGE = "https://www.data.gov.uk/dataset/cb7ae6f0-4be6-4935-9277-47e5ce24a11f"
OUT = os.path.join(ROOT, "app", "data", "road_collisions.bin")

MAGIC = b"UKPIRC1"
SCALE = 100000          # degrees to fixed point, about a metre
CELL_DEG = 0.02         # roughly 2.2 km north to south: a few dozen rows per cell
LON_CELLS = 20000       # cells per row of latitude, for the single-integer key


def cell_key(lat: float, lon: float) -> int:
    """One integer per grid cell, ordered so that a cell's rows are
    contiguous once sorted and a lookup is two bisects per latitude."""
    return int((lat + 90) / CELL_DEG) * LON_CELLS + int((lon + 180) / CELL_DEG)


def download() -> bytes:
    print(f"Downloading {CSV_URL}")
    with httpx.stream("GET", CSV_URL, timeout=600, follow_redirects=True,
                      headers={"User-Agent": "UKPropertyInsight import "
                                             "(support@ukpropertyinsight.co.uk)"}) as reply:
        reply.raise_for_status()
        body = bytearray()
        for chunk in reply.iter_bytes(1 << 20):
            body += chunk
            if len(body) % (20 << 20) < (1 << 20):
                print(f"  {len(body) / 1e6:.0f} MB")
    print(f"  {len(body) / 1e6:.1f} MB")
    return bytes(body)


def parse(body: bytes):
    rows = []
    by_year: dict[int, int] = {}
    skipped = 0
    reader = csv.DictReader(io.TextIOWrapper(io.BytesIO(body), encoding="utf-8-sig"))
    for row in reader:
        try:
            lat = float(row["latitude"])
            lon = float(row["longitude"])
            severity = int(row["collision_severity"])
            year = int(row["collision_year"])
        except (TypeError, ValueError):
            skipped += 1          # a handful every year have no grid reference
            continue
        if not (49 < lat < 61.5 and -8.5 < lon < 2.5) or severity not in (1, 2, 3):
            skipped += 1
            continue
        try:
            month = int(row["date"].split("/")[1])
        except (IndexError, ValueError):
            month = 0
        casualties = int(row["number_of_casualties"] or 0)
        rows.append((cell_key(lat, lon), round(lat * SCALE), round(lon * SCALE),
                     severity, min(casualties, 63), year, month))
        by_year[year] = by_year.get(year, 0) + 1
    return rows, by_year, skipped


def write(rows, by_year, skipped):
    rows.sort(key=lambda row: row[0])
    base_year = min(by_year)
    cells = array.array("I", (row[0] for row in rows))
    lats = array.array("i", (row[1] for row in rows))
    lons = array.array("i", (row[2] for row in rows))
    packed = array.array("H", (
        (row[3] - 1) | (row[4] << 2) | ((row[5] - base_year) << 8) | (row[6] << 12)
        for row in rows))
    header = {
        "source": "DfT road safety data (STATS19 police returns)",
        "dataset_page": DATASET_PAGE,
        "file": CSV_URL,
        "read_on": dt.date.today().isoformat(),
        "count": len(rows),
        "base_year": base_year,
        "years": sorted(by_year),
        "by_year": {str(year): by_year[year] for year in sorted(by_year)},
        "no_grid_reference": skipped,
        "scale": SCALE,
        "cell_deg": CELL_DEG,
        "lon_cells": LON_CELLS,
        "coverage": ["England", "Wales", "Scotland"],
        "coverage_note": ("Northern Ireland is not in this file. The PSNI publishes "
                          "collisions there separately."),
    }
    blob = json.dumps(header, separators=(",", ":")).encode("utf-8")
    with open(OUT, "wb") as handle:
        handle.write(MAGIC)
        handle.write(struct.pack("<I", len(blob)))
        handle.write(blob)
        for store in (cells, lats, lons, packed):
            store.tofile(handle)
    return header


def main():
    rows, by_year, skipped = parse(download())
    if not rows:
        raise SystemExit("no collisions parsed, refusing to write an empty file")
    header = write(rows, by_year, skipped)
    print(f"Wrote {OUT}")
    print(f"  {header['count']:,} collisions, {os.path.getsize(OUT) / 1e6:.1f} MB")
    for year in header["years"]:
        print(f"  {year}: {header['by_year'][str(year)]:,}")
    print(f"  {skipped:,} rows had no usable grid reference and were left out")


if __name__ == "__main__":
    main()
