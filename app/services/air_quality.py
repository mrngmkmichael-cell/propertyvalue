"""Modelled background air pollutant concentrations near a property,
from our own `air_quality` table (populated offline by
scripts/import_air_quality.py from Defra's national Pollution Climate
Mapping) rather than a live API - see that script for source details.

Reports against the World Health Organization's 2021 Air Quality
Guideline levels (the current international health-based reference
point) rather than the UK's own legal objectives, which are
considerably laxer and mostly unchanged since the early 2000s - WHO's
numbers give a more honest sense of "how much pollution is this,
really" than "does this technically comply with UK law".
"""
from sqlalchemy import select

from app.db import get_session, is_configured
from app.models import AirQuality

# WHO 2021 Air Quality Guideline annual mean levels, in ug/m3.
WHO_GUIDELINE = {"no2_ug_m3": 10, "pm25_ug_m3": 5, "pm10_ug_m3": 15}


def _grid_cell(easting: float, northing: float) -> tuple[int, int]:
    return (int(easting) // 1000) * 1000 + 500, (int(northing) // 1000) * 1000 + 500


def for_location(easting: float | None, northing: float | None) -> dict | None:
    if not is_configured() or not easting or not northing:
        return None
    grid_easting, grid_northing = _grid_cell(easting, northing)
    with get_session() as session:
        row = session.scalar(
            select(AirQuality).where(
                AirQuality.grid_easting == grid_easting,
                AirQuality.grid_northing == grid_northing,
            )
        )
        if not row:
            return None
        pollutants = []
        for field, label in [("no2_ug_m3", "NO2"), ("pm25_ug_m3", "PM2.5"), ("pm10_ug_m3", "PM10")]:
            value = getattr(row, field)
            if value is None:
                continue
            guideline = WHO_GUIDELINE[field]
            pollutants.append({
                "label": label,
                "value": round(value, 1),
                "who_guideline": guideline,
                "times_guideline": round(value / guideline, 1),
            })
        return {"year": row.year, "pollutants": pollutants}
