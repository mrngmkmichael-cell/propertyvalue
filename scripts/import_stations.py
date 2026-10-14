"""Every railway, Underground, metro and tram station in Great Britain,
from the Department for Transport's NaPTAN register, into
app/data/stations.json for the report's "Getting around" card.

Why (14 Oct 2026): the card read stations from OpenStreetMap's Overpass
service, which now refuses this site by name. NaPTAN is the official
register every timetable and journey planner keys on, published under
the Open Government Licence, and a file held here cannot be refused.

Sources, all open data:
  - NaPTAN rail, area 910, as XML: one RLY access area per National Rail
    station, with its CRS code (the three letters journey times need).
  - NaPTAN metro and tram, area 940, as CSV: one MET access area per
    Underground, DLR, metro and tram station. Systems are told apart by
    the ATCO prefix; heritage and minor lines are left out.
  - TfL open data, /Line/{id}/StopPoints, for which Underground lines
    call at each London station (the badges on the card). "Powered by
    TfL Open Data".

Refresh: monthly, with the other imports. Stations open and close a few
times a year; a missed month costs a new station at most.

    .venv/Scripts/python.exe scripts/import_stations.py
"""
import csv
import io
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

import httpx

OUT = pathlib.Path(__file__).resolve().parent.parent / "app" / "data" / "stations.json"
NAPTAN = "https://naptan.api.dft.gov.uk/v1/access-nodes"
TFL = "https://api.tfl.gov.uk"
NS = {"n": "http://www.naptan.org.uk/"}

# ATCO prefix of a 940 MET access area: (mode, network). Mode is what the
# card groups by ("tube" for the two underground systems, "tram" for the
# street-running and light-rail ones, "rail" for the DLR as OpenStreetMap
# had it), network is the name shown beside the station.
METRO_SYSTEMS = {
    "9400ZZLU": ("tube", "London Underground"),
    "9400ZZGL": ("tube", "Glasgow Subway"),
    "9400ZZDL": ("rail", "Docklands Light Railway"),
    "9400ZZTW": ("tram", "Tyne and Wear Metro"),
    "9400ZZMA": ("tram", "Manchester Metrolink"),
    "9400ZZSY": ("tram", "Sheffield Supertram"),
    "9400ZZNO": ("tram", "Nottingham Express Transit"),
    "9400ZZWM": ("tram", "West Midlands Metro"),
    "9400ZZCR": ("tram", "London Trams"),
    "9400ZZED": ("tram", "Edinburgh Trams"),
    "9400ZZBP": ("tram", "Blackpool Tramway"),
}
TUBE_LINES = ["bakerloo", "central", "circle", "district", "hammersmith-city", "jubilee",
              "metropolitan", "northern", "piccadilly", "victoria", "waterloo-city"]


def tidy(name: str) -> str:
    """'Aberdare Rail Station' -> 'Aberdare'; 'Pipers Row (West Midlands
    Metro)' -> 'Pipers Row'; 'Oxford Circus Underground Station' ->
    'Oxford Circus'. The network is shown beside the name already."""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name.strip())
    name = re.sub(r"\s+(Rail|Railway|Underground|DLR|Tram|Metro|Subway)\s+(Station|Stop)$", "", name)
    name = re.sub(r"\s+(Station|Tram Stop|Metrolink Stop|Stop)$", "", name)
    return name.strip()


def rail(client: httpx.Client) -> list[dict]:
    r = client.get(NAPTAN, params={"dataFormat": "xml", "atcoAreaCodes": "910"})
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for sp in root.iter("{http://www.naptan.org.uk/}StopPoint"):
        if sp.get("Status") != "active":
            continue
        if sp.findtext("n:StopClassification/n:StopType", namespaces=NS) != "RLY":
            continue
        lat = sp.findtext("n:Place/n:Location/n:Translation/n:Latitude", namespaces=NS)
        lon = sp.findtext("n:Place/n:Location/n:Translation/n:Longitude", namespaces=NS)
        if not lat or not lon:
            continue
        crs = sp.findtext(".//n:AnnotatedRailRef/n:CrsRef", namespaces=NS)
        out.append({
            "atco": sp.findtext("n:AtcoCode", namespaces=NS),
            "name": tidy(sp.findtext("n:Descriptor/n:CommonName", namespaces=NS) or ""),
            "mode": "rail", "network": "",
            "crs": crs if crs and re.fullmatch(r"[A-Z]{3}", crs) else None,
            "lat": round(float(lat), 6), "lon": round(float(lon), 6), "lines": [],
        })
    return out


def metro(client: httpx.Client) -> list[dict]:
    r = client.get(NAPTAN, params={"dataFormat": "csv", "atcoAreaCodes": "940"})
    r.raise_for_status()
    out = []
    for row in csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))):
        if row["StopType"] != "MET" or row["Status"] != "active":
            continue
        system = METRO_SYSTEMS.get(row["ATCOCode"][:8])
        if system is None or not row["Latitude"] or not row["Longitude"]:
            continue
        mode, network = system
        out.append({
            "atco": row["ATCOCode"], "name": tidy(row["CommonName"]), "mode": mode, "network": network,
            "crs": None, "lat": round(float(row["Latitude"]), 6), "lon": round(float(row["Longitude"]), 6),
            "lines": [],
        })
    return out


def tube_lines(client: httpx.Client) -> dict[str, list[str]]:
    """ATCO code of each London station -> the Underground lines calling
    there. TfL's stop point id is the station's stop area (940G...);
    NaPTAN's access area is the same code with 9400 for 940G."""
    names = {line["id"]: line["name"] for line in client.get(f"{TFL}/Line/Mode/tube").json()}
    by_station: dict[str, list[str]] = {}
    for line_id in TUBE_LINES:
        for stop in client.get(f"{TFL}/Line/{line_id}/StopPoints").json():
            naptan = stop.get("naptanId") or ""
            if naptan.startswith("940G"):
                by_station.setdefault("9400" + naptan[4:], []).append(names.get(line_id, line_id))
    return by_station


def main() -> None:
    with httpx.Client(timeout=120, headers={"User-Agent": "UKPropertyInsight station import"}) as client:
        stations = rail(client) + metro(client)
        lines = tube_lines(client)
    for s in stations:
        if s["network"] == "London Underground":
            s["lines"] = sorted(set(lines.get(s["atco"], [])))
    counts = {}
    for s in stations:
        counts[s["mode"]] = counts.get(s["mode"], 0) + 1
    missing_crs = sum(1 for s in stations if s["mode"] == "rail" and s["network"] == "" and not s["crs"])
    tube_without_lines = sum(1 for s in stations if s["network"] == "London Underground" and not s["lines"])
    assert counts.get("rail", 0) > 2500 and counts.get("tube", 0) > 250 and counts.get("tram", 0) > 300, counts
    OUT.write_text(json.dumps({"source": "NaPTAN (Department for Transport), TfL open data",
                               "stations": stations}, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT}: {len(stations)} stations {counts}; rail without CRS {missing_crs}; "
          f"Underground without lines {tube_without_lines}")


if __name__ == "__main__":
    sys.exit(main())
