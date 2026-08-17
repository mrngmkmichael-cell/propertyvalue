"""One-time/periodic offline import of Defra's national Pollution
Climate Mapping (PCM) modelled background air quality concentrations
into the `air_quality` table.

NOT run by the deployed app - run manually from a dev machine.
Republished annually - the hardcoded URLs below (year 2024, the most
recent available at the time this was written) will need updating to
the next year's files periodically; the file naming convention is
stable (https://uk-air.defra.gov.uk/datastore/pcm/map{pollutant}{year}.csv,
minus a trailing "g"/"gh"/"grav" suffix code some years use).

Source: https://uk-air.defra.gov.uk/data/pcm-data - separate CSVs per
pollutant per year, each ~255,000 rows (one per 1km British National
Grid cell covering the whole UK). Grid x/y are cell-centre BNG
easting/northing (EPSG:27700) - the same coordinate system
postcodes.io already returns per postcode (see app/services/
postcodes.py), so property lookups need no separate conversion: round
to the nearest 1000 + 500 to find the matching cell.
"""
import csv
import io
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.db import Base, _get_engine  # noqa: E402
from app.models import AirQuality  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

YEAR = 2024
URLS = {
    "no2_ug_m3": f"https://uk-air.defra.gov.uk/datastore/pcm/mapno2{YEAR}.csv",
    "pm25_ug_m3": f"https://uk-air.defra.gov.uk/datastore/pcm/mappm25{YEAR}g.csv",
    "pm10_ug_m3": f"https://uk-air.defra.gov.uk/datastore/pcm/mappm10{YEAR}g.csv",
}
HEADERS = {"User-Agent": "Mozilla/5.0"}
BATCH_SIZE = 20000


def fetch_grid(field_name: str, url: str) -> dict[tuple[int, int], float]:
    print(f"Downloading {url}")
    resp = httpx.get(url, timeout=120, follow_redirects=True, headers=HEADERS)
    resp.raise_for_status()
    text = resp.content.decode("utf-8-sig")
    lines = text.splitlines()
    # The first ~5 lines are a metadata preamble (pollutant, year, unit,
    # blank), not the CSV header - the real header is "gridcode,x,y,<col>".
    header_idx = next(i for i, line in enumerate(lines) if line.startswith("gridcode"))
    reader = csv.DictReader(lines[header_idx:])
    value_col = next(c for c in reader.fieldnames if c not in ("gridcode", "x", "y"))

    grid = {}
    for row in reader:
        try:
            x, y, value = int(row["x"]), int(row["y"]), float(row[value_col])
        except (TypeError, ValueError, KeyError):
            continue
        grid[(x, y)] = value
    print(f"  {len(grid)} cells")
    return grid


def load_into_db(grids: dict[str, dict[tuple[int, int], float]]) -> None:
    all_cells = set()
    for grid in grids.values():
        all_cells.update(grid.keys())
    print(f"{len(all_cells)} unique grid cells across all pollutants")

    engine = _get_engine()
    Base.metadata.create_all(engine, tables=[AirQuality.__table__])
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        print("Clearing existing air_quality table...")
        session.query(AirQuality).delete()
        session.commit()

        batch = []
        total = 0
        for x, y in all_cells:
            batch.append({
                "grid_easting": x,
                "grid_northing": y,
                "year": YEAR,
                "no2_ug_m3": grids["no2_ug_m3"].get((x, y)),
                "pm25_ug_m3": grids["pm25_ug_m3"].get((x, y)),
                "pm10_ug_m3": grids["pm10_ug_m3"].get((x, y)),
            })
            if len(batch) >= BATCH_SIZE:
                session.execute(AirQuality.__table__.insert(), batch)
                session.commit()
                total += len(batch)
                print(f"  inserted {total} rows so far")
                batch = []
        if batch:
            session.execute(AirQuality.__table__.insert(), batch)
            session.commit()
            total += len(batch)
        print(f"Inserted {total} rows total")


def main():
    grids = {field: fetch_grid(field, url) for field, url in URLS.items()}
    load_into_db(grids)
    print("Done.")


if __name__ == "__main__":
    main()
