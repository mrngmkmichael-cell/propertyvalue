"""The 2011 to 2021 comparison for a small area."""
from app import db
from app.models import AgeProfile, Census2011, CountryOfBirth, Ethnicity, GeneralHealth, Qualification, Tenure
from app.services import census_change


def test_shares_and_changes_line_up(client, monkeypatch):
    monkeypatch.setattr(census_change, "context", lambda: {
        "england_2011": {"households": 1000, "owned": 633, "private_rented": 168, "social_rented": 177, "residents": 1000, "under_15": 177, "over_65": 163,
                         "adults": 1000, "level_4_plus": 274, "cob_total": 1000, "born_uk": 862, "health_total": 1000, "health_good": 814, "eth_total": 1000, "white": 854},
        "england_2021": {"households": 1000, "owned": 613, "private_rented": 205, "social_rented": 171, "residents": 1000, "under_15": 174, "over_65": 185,
                         "adults": 1000, "level_4_plus": 338, "cob_total": 1000, "born_uk": 826, "health_total": 1000, "health_good": 821, "eth_total": 1000, "white": 810},
    })
    with db.get_session() as session:
        session.query(Census2011).delete()
        session.add(Census2011(lsoa_code="E01099999", lsoa11_count=2, households=600, owned=338, private_rented=213, social_rented=41,
                               residents=1267, under_15=266, over_65=120, adults=1000, level_4_plus=300, cob_total=1267, born_uk=900,
                               health_total=1267, health_good=1050, eth_total=1267, white=700))
        session.merge(Tenure(lsoa_code="E01099999", total=650, owned_outright=120, owned_mortgage=169, shared_ownership=5, social_rented=54, private_rented=294, rent_free=8))
        session.merge(AgeProfile(lsoa_code="E01099999", total=1336, under_15=248, age_15_24=200, age_25_44=500, age_45_64=250, age_65_84=120, age_85_plus=18))
        session.merge(Qualification(lsoa_code="E01099999", total=1100, no_qualifications=100, level_1_entry=100, level_2=100, apprenticeship=50, level_3=150, level_4_plus=500, other_qualifications=100))
        session.merge(CountryOfBirth(lsoa_code="E01099999", total=1336, uk=880))
        session.merge(GeneralHealth(lsoa_code="E01099999", total=1336, very_good=700, good=400, fair=150, bad=60, very_bad=26))
        session.merge(Ethnicity(lsoa_code="E01099999", total=1336, white=650, asian=300, black=200, mixed=100, other=86))
        session.commit()
    out = census_change.for_lsoa("E01099999")
    rows = {r["key"]: r for r in out["rows"]}
    assert rows["owned"]["in_2011"] == 56.3 and rows["owned"]["in_2021"] == 44.5 and rows["owned"]["change"] == -11.8
    assert rows["private_rented"]["change"] == 9.7 and rows["private_rented"]["england_change"] == 3.7
    assert rows["born_outside_uk"]["in_2011"] == 29.0 and rows["born_outside_uk"]["in_2021"] == 34.1
    assert out["biggest"]["key"] == "level_4_plus" and out["merged_from"] == 2   # +15.5 points beats owning at -11.8
    assert out["residents_change_pct"] == 5.4 and out["has_2011"] and out["has_2021"]
    assert census_change.for_lsoa("E01000000") is None and census_change.for_lsoa("") is None
