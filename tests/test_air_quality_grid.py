"""Northern Ireland reads its own air quality cell (18 Sep 2026).

postcodes.io gives Northern Ireland postcodes Irish Grid references, and
Defra's PCM cells are British National Grid. Read as British, BT1 5GS
(333832, 374014) landed in a cell near Chester, so a Belfast report
showed 6.2 ug/m3 of NO2 where its own cell holds 14.1.
"""
from app.services import air_quality

# postcodes.io's own latitude, longitude and grid reference, 18 Sep 2026.
GB_REFERENCES = [
    ("M1 1AE", 53.483487, -2.231182, 384756, 398553),
    ("SW1A 1AA", 51.50101, -0.141563, 529090, 179645),
    ("EH1 1YZ", 55.950328, -3.193018, 325597, 673676),
    ("CF10 1EP", 51.475764, -3.179217, 318200, 175860),
    ("ZE1 0AA", 60.153231, -1.141602, 447759, 1141280),
    ("TR18 2AA", 50.121037, -5.538954, 147132, 30551),
]


def test_the_conversion_matches_the_ordnance_survey_grid():
    for postcode, lat, lon, easting, northing in GB_REFERENCES:
        east, north = air_quality.wgs84_to_bng(lat, lon)
        assert abs(east - easting) < 5 and abs(north - northing) < 5, postcode
    # pyproj, EPSG:4326 to EPSG:27700, gives (146239, 529486) for BT1 5GS.
    east, north = air_quality.wgs84_to_bng(54.596633, -5.930077)
    assert abs(east - 146239) < 2 and abs(north - 529486) < 2


def test_a_belfast_address_reads_the_belfast_cell(client):
    from app import db
    from app.models import AirQuality

    with db.get_session() as session:
        for cell in (AirQuality(grid_easting=146500, grid_northing=529500, year=2024,
                                no2_ug_m3=14.12, pm25_ug_m3=5.95, pm10_ug_m3=10.84),
                     AirQuality(grid_easting=333500, grid_northing=374500, year=2024,
                                no2_ug_m3=6.22, pm25_ug_m3=5.62, pm10_ug_m3=9.0)):
            session.merge(cell)
        session.commit()
    belfast = air_quality.for_location(333832, 374014, 54.596633, -5.930077, "Northern Ireland")
    assert [p["value"] for p in belfast["pollutants"]][:2] == [14.1, 6.0]
    # A British grid reference is still read as it comes.
    england = air_quality.for_location(333832, 374014, 53.25, -2.9, "England")
    assert england["pollutants"][0]["value"] == 6.2
    # Without a latitude and longitude a Northern Ireland point is not guessed.
    assert air_quality.for_location(333832, 374014, None, None, "Northern Ireland") is None
