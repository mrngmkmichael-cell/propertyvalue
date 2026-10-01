"""How often children are out of school, by council (1 Oct 2026).

The same limit as the admission appeals pages: the Department for
Education publishes absence by local authority and never by school, so
the block names the council every time and says a school's own rate is
not in this release. These tests fail if that sentence goes missing.

They also hold the distinction the two figures carry. Overall absence
is days missed. Persistent absence is children who missed a tenth or
more. A page that blurred them would be telling a parent something
untrue about both.
"""
from app.services import school_absence
from tests.test_pages import _seed_admission_school


def flat(text):
    return " ".join(text.split())


def test_the_file_is_there_and_is_recent():
    source = school_absence.source()
    assert source, "app/data/school_absence.json is missing or unreadable"
    assert source["name"] == "DfE pupil absence in schools in England"
    assert source["councils"] > 100, "England has around 150 education authorities"
    assert int(source["year"].split("/")[0]) >= 2023


def test_a_council_reads_with_england_beside_it():
    here = school_absence.for_council("Manchester")
    assert here["council"] == "Manchester"
    phases = {row["phase"] for row in here["phases"]}
    assert {"Primary", "Secondary"} <= phases
    for row in here["phases"]:
        assert 0 < row["overall_pct"] < 30, "a share of sessions, not a count"
        assert 0 < row["persistent_pct"] < 80, "a share of pupils"
        assert row["persistent_pct"] > row["overall_pct"], (
            "more children miss a tenth of sessions than the share of all sessions missed")
        assert row["england_overall_pct"] and row["england_persistent_pct"]


def test_secondary_absence_runs_higher_than_primary_as_it_does_nationally():
    england = school_absence._data["england"]
    assert england["secondary"]["overall_pct"] > england["primary"]["overall_pct"]
    assert england["secondary"]["persistent_pct"] > england["primary"]["persistent_pct"]


def test_the_comparison_has_one_stated_rule():
    assert school_absence._against(5.5, 5.2) == "about the same as"
    assert school_absence._against(7.0, 5.2) == "higher than"
    assert school_absence._against(4.0, 5.2) == "lower than"
    assert school_absence._against(None, 5.2) == ""
    assert school_absence.SAME_WITHIN_PP == 1.0


def test_a_council_the_release_does_not_hold_is_none():
    assert school_absence.for_council("Narnia") is None
    assert school_absence.for_council(None) is None


def test_a_differently_spelled_council_still_matches():
    assert school_absence.for_council("Bristol, City of")["council"] == "Bristol, City of"
    assert school_absence.for_council("bristol city of") is not None


def test_every_council_with_appeals_also_has_an_absence_figure():
    """A block missing on half the hubs is worse than no block. Both
    files are keyed on council names the DfE spells its own way, so this
    checks the two against each other: 151 councils in the appeals
    release, and every one of them found here. The database is not
    involved, because in a full suite run its council names are whatever
    other tests seeded."""
    from app.services import appeals
    names = sorted({row["council"] for row in appeals.league()})
    assert len(names) > 100
    missing = [name for name in names if school_absence.for_council(name) is None]
    assert not missing, f"no absence figure for {missing[:5]}"


def test_the_council_hub_shows_both_figures_and_whose_they_are(client):
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = flat(client.get("/schools/admissions/manchester").text)
    here = school_absence.for_council("Manchester")
    assert "How often children here are out of school" in body
    assert f"{here['primary']['overall_pct']}% of primary school sessions were missed" in body
    assert f"{here['primary']['persistent_pct']}% of primary pupils here were persistently absent" in body
    assert "publishes absence by council, not school by school" in body
    assert "explore-education-statistics.service.gov.uk" in body


def test_the_hub_never_offers_an_absence_rate_for_one_school(client):
    _seed_admission_school()
    from app.services import _cache
    _cache._store.clear(); _cache._bytes = 0
    body = client.get("/schools/admissions/manchester").text.lower()
    for claim in ("this school's absence", "absence rate for this school",
                  "attendance score", "worst attendance"):
        assert claim not in body
