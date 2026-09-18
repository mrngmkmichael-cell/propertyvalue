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
import math

from sqlalchemy import select

from app.db import get_session, is_configured
from app.models import AirQuality

# WHO 2021 Air Quality Guideline annual mean levels, in ug/m3.
WHO_GUIDELINE = {"no2_ug_m3": 10, "pm25_ug_m3": 5, "pm10_ug_m3": 15}


def _grid_cell(easting: float, northing: float) -> tuple[int, int]:
    return (int(easting) // 1000) * 1000 + 500, (int(northing) // 1000) * 1000 + 500


def wgs84_to_bng(lat: float, lon: float) -> tuple[float, float]:
    """British National Grid easting and northing for a WGS84 point: the
    Ordnance Survey's Helmert shift to OSGB36, then its transverse
    Mercator projection. Within 4 m of postcodes.io's own grid references
    at M1 1AE, SW1A 1AA, EH1 1YZ, CF10 1EP, ZE1 0AA and TR18 2AA, and
    equal to pyproj's at BT1 5GS, which is plenty for a 1 km cell."""
    a1, b1 = 6378137.000, 6356752.3141
    phi, lam = math.radians(lat), math.radians(lon)
    e2 = 1 - (b1 * b1) / (a1 * a1)
    nu = a1 / math.sqrt(1 - e2 * math.sin(phi) ** 2)
    x1 = nu * math.cos(phi) * math.cos(lam)
    y1 = nu * math.cos(phi) * math.sin(lam)
    z1 = (1 - e2) * nu * math.sin(phi)
    tx, ty, tz, s = -446.448, 125.157, -542.060, 20.4894e-6
    rx, ry, rz = (math.radians(v / 3600) for v in (-0.1502, -0.2470, -0.8421))
    x2 = tx + (1 + s) * x1 - rz * y1 + ry * z1
    y2 = ty + rz * x1 + (1 + s) * y1 - rx * z1
    z2 = tz - ry * x1 + rx * y1 + (1 + s) * z1
    a, b = 6377563.396, 6356256.909
    e2 = 1 - (b * b) / (a * a)
    p = math.hypot(x2, y2)
    phi = math.atan2(z2, p * (1 - e2))
    for _ in range(10):
        nu = a / math.sqrt(1 - e2 * math.sin(phi) ** 2)
        phi = math.atan2(z2 + e2 * nu * math.sin(phi), p)
    lam = math.atan2(y2, x2)
    f0, phi0, lam0 = 0.9996012717, math.radians(49), math.radians(-2)
    n = (a - b) / (a + b)
    sinp, cosp, tanp = math.sin(phi), math.cos(phi), math.tan(phi)
    nu = a * f0 / math.sqrt(1 - e2 * sinp ** 2)
    rho = a * f0 * (1 - e2) / (1 - e2 * sinp ** 2) ** 1.5
    eta2 = nu / rho - 1
    dphi, sphi = phi - phi0, phi + phi0
    m = b * f0 * (
        (1 + n + 1.25 * n ** 2 + 1.25 * n ** 3) * dphi
        - (3 * n + 3 * n ** 2 + 2.625 * n ** 3) * math.sin(dphi) * math.cos(sphi)
        + (1.875 * n ** 2 + 1.875 * n ** 3) * math.sin(2 * dphi) * math.cos(2 * sphi)
        - (35 / 24) * n ** 3 * math.sin(3 * dphi) * math.cos(3 * sphi)
    )
    dl = lam - lam0
    north = (m - 100000 + nu / 2 * sinp * cosp * dl ** 2
             + nu / 24 * sinp * cosp ** 3 * (5 - tanp ** 2 + 9 * eta2) * dl ** 4
             + nu / 720 * sinp * cosp ** 5 * (61 - 58 * tanp ** 2 + tanp ** 4) * dl ** 6)
    east = (400000 + nu * cosp * dl
            + nu / 6 * cosp ** 3 * (nu / rho - tanp ** 2) * dl ** 3
            + nu / 120 * cosp ** 5 * (5 - 18 * tanp ** 2 + tanp ** 4 + 14 * eta2
                                      - 58 * tanp ** 2 * eta2) * dl ** 5)
    return east, north


def for_location(easting: float | None, northing: float | None, latitude: float | None = None,
                 longitude: float | None = None, country: str | None = None) -> dict | None:
    # Northern Ireland's grid references from postcodes.io are on the
    # Irish Grid, and Defra's cells are British National Grid, which
    # covers Northern Ireland too. Read as British, BT1 5GS (333832,
    # 374014) landed in a cell near Chester, so every Northern Ireland
    # report showed an English reading: 6.2 ug/m3 of NO2 for central
    # Belfast, whose own cell holds 14.1 (18 Sep 2026). There the point
    # is converted from its latitude and longitude instead.
    if country == "Northern Ireland":
        if latitude is None or longitude is None:
            return None
        easting, northing = wgs84_to_bng(latitude, longitude)
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
