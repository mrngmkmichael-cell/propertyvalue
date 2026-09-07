"""GP list pressure and A&E performance near a point, from the imported
NHS tables."""
import datetime

from app import db
from app.models import AeTrust, GpPractice
from app.services import _cache, health_services


def test_period_label():
    assert health_services.period_label("MSitAE-JULY-2026") == "July 2026"
    assert health_services.period_label("") == ""


def test_names_are_cased_once():
    assert health_services.tidy_name("KING'S COLLEGE HOSPITAL NHS FOUNDATION TRUST") == "King's College Hospital NHS Foundation Trust"
    assert health_services.tidy_name("Guy'S And St Thomas' Nhs Foundation Trust") == "Guy's and St Thomas' NHS Foundation Trust"
    assert health_services.tidy_name("The Doc'S Surgery") == "The Doc's Surgery"
    assert health_services.tidy_name("STOCKTON-ON-TEES PCN") == "Stockton-On-Tees PCN"


def test_near_returns_the_nearest_practices_and_the_boards_a_and_e(client, monkeypatch):
    monkeypatch.setattr(health_services, "context", lambda: {
        "median_patients_per_qualified_gp": 2186, "patients_date": "2026-08-01", "workforce_date": "2026-07-01",
        "ae_period": "MSitAE-JULY-2026", "national_type1_within_4h_pct": 61.5,
    })
    with db.get_session() as session:
        session.query(GpPractice).delete(); session.query(AeTrust).delete()
        session.add_all([
            GpPractice(code="A1", name="High Street Surgery", postcode="M14 5TG", latitude=53.4502, longitude=-2.2200, icb_code="QOP", icb_name="NHS Greater Manchester Integrated Care Board",
                       pcn_name="Central PCN", patients=8744, gp_fte=5.2, qualified_gp_fte=3.1, gp_source="Fully provided",
                       patients_date=datetime.date(2026, 8, 1), workforce_date=datetime.date(2026, 7, 1)),
            GpPractice(code="A2", name="Park Medical", postcode="M14 5AA", latitude=53.4560, longitude=-2.2250, icb_code="QOP", icb_name="NHS Greater Manchester Integrated Care Board",
                       patients=4000, gp_fte=2.0, qualified_gp_fte=0.4, gp_source="Includes FTE Estimates"),
            GpPractice(code="FAR", name="Elsewhere", postcode="B1 1AA", latitude=52.48, longitude=-1.9, icb_code="QHL", patients=9000, qualified_gp_fte=4),
        ])
        session.add_all([
            AeTrust(org_code="R0A", name="Manchester University Nhs Foundation Trust", icb_code="QOP", period="MSitAE-JULY-2026",
                    type1_attendances=30000, type1_over_4h=12000, all_attendances=40000, all_over_4h=13000),
            AeTrust(org_code="RM3", name="Northern Care Alliance", icb_code="QOP", period="MSitAE-JULY-2026",
                    type1_attendances=20000, type1_over_4h=6000, all_attendances=25000, all_over_4h=6500),
            AeTrust(org_code="RRK", name="Birmingham", icb_code="QHL", period="MSitAE-JULY-2026", type1_attendances=50000, type1_over_4h=20000),
        ])
        session.commit()
    _cache._store.clear(); _cache._bytes = 0
    out = health_services.near(53.4500, -2.2200)
    assert [g["code"] for g in out["practices"]] == ["A1", "A2"]
    nearest = out["nearest"]
    assert nearest["patients_per_qualified_gp"] == 2821 and nearest["vs_median"] == 1.29 and nearest["estimated"] is False
    assert out["practices"][1]["patients_per_qualified_gp"] is None   # under half an FTE: no ratio
    assert out["practices"][1]["estimated"] is True
    assert [t["org_code"] for t in out["trusts"]] == ["R0A", "RM3"]
    assert out["trusts"][0]["type1_within_4h_pct"] == 60.0 and out["trusts"][0]["all_within_4h_pct"] == 67.5
    assert out["ae_period"] == "July 2026" and out["icb_name"].startswith("NHS Greater Manchester")
    assert health_services.near(51.5, 0.5)["count"] == 0
