from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    watchlist_items: Mapped[list["WatchlistItem"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("user_id", "postcode", name="uq_user_postcode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    postcode: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    user: Mapped["User"] = relationship(back_populates="watchlist_items")


class SchoolShortlistItem(Base):
    """A logged-in user's saved/shortlisted schools - the same
    account system as WatchlistItem, keyed by school URN instead of
    a postcode."""
    __tablename__ = "school_shortlist_items"
    __table_args__ = (UniqueConstraint("user_id", "urn", name="uq_user_school_urn"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    urn: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class School(Base):
    """Open schools in England, from DfE's GIAS establishment data,
    joined with Ofsted's state-funded school inspection outcomes.
    Populated by scripts/import_schools.py (a one-time/periodic
    offline import, not something the deployed app runs itself) -
    see that script for source URLs and field mapping.
    """
    __tablename__ = "schools"

    urn: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    phase: Mapped[str] = mapped_column(String(100), default="")
    type_name: Mapped[str] = mapped_column(String(150), default="")
    postcode: Mapped[str] = mapped_column(String(16), default="")
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    ofsted_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ofsted_rating_label: Mapped[str] = mapped_column(String(50), default="")
    ofsted_inspection_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class Deprivation(Base):
    """English Indices of Deprivation 2025, by LSOA (2021 boundaries).
    Populated by scripts/import_area_stats.py from MHCLG's official
    File 7 release - a periodic (every few years) official dataset,
    not something with a live API.
    """
    __tablename__ = "deprivation"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    lsoa_name: Mapped[str] = mapped_column(String(100), default="")
    la_code: Mapped[str] = mapped_column(String(16), default="")
    la_name: Mapped[str] = mapped_column(String(150), default="")
    imd_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    imd_decile: Mapped[int | None] = mapped_column(Integer, nullable=True)
    income_decile: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employment_decile: Mapped[int | None] = mapped_column(Integer, nullable=True)
    education_decile: Mapped[int | None] = mapped_column(Integer, nullable=True)
    health_decile: Mapped[int | None] = mapped_column(Integer, nullable=True)
    crime_decile: Mapped[int | None] = mapped_column(Integer, nullable=True)
    housing_barriers_decile: Mapped[int | None] = mapped_column(Integer, nullable=True)
    living_environment_decile: Mapped[int | None] = mapped_column(Integer, nullable=True)


class HouseholdIncome(Base):
    """ONS model-based total annual household income estimates, by
    MSOA, financial year ending 2023. Populated by
    scripts/import_area_stats.py. Modelled (not measured) estimates -
    the official ONS caveat, not a limitation of this app.
    """
    __tablename__ = "household_income"

    msoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    msoa_name: Mapped[str] = mapped_column(String(100), default="")
    la_code: Mapped[str] = mapped_column(String(16), default="")
    la_name: Mapped[str] = mapped_column(String(150), default="")
    region_code: Mapped[str] = mapped_column(String(16), default="")
    region_name: Mapped[str] = mapped_column(String(100), default="")
    total_annual_income: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Occupation(Base):
    """Census 2021 occupation breakdown (TS063), by LSOA - counts of
    usual residents 16+ in employment, by occupation category.
    Populated by scripts/import_census_stats.py. Static until the
    2031 census.
    """
    __tablename__ = "occupation"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    managers_directors_senior: Mapped[int] = mapped_column(Integer, default=0)
    professional: Mapped[int] = mapped_column(Integer, default=0)
    associate_professional_technical: Mapped[int] = mapped_column(Integer, default=0)
    admin_secretarial: Mapped[int] = mapped_column(Integer, default=0)
    skilled_trades: Mapped[int] = mapped_column(Integer, default=0)
    caring_leisure_service: Mapped[int] = mapped_column(Integer, default=0)
    sales_customer_service: Mapped[int] = mapped_column(Integer, default=0)
    process_plant_machine_operatives: Mapped[int] = mapped_column(Integer, default=0)
    elementary: Mapped[int] = mapped_column(Integer, default=0)


class Qualification(Base):
    """Census 2021 highest qualification breakdown (TS067), by LSOA -
    counts of usual residents 16+, by highest qualification level.
    Populated by scripts/import_census_stats.py. Static until the
    2031 census.
    """
    __tablename__ = "qualification"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    no_qualifications: Mapped[int] = mapped_column(Integer, default=0)
    level_1_entry: Mapped[int] = mapped_column(Integer, default=0)
    level_2: Mapped[int] = mapped_column(Integer, default=0)
    apprenticeship: Mapped[int] = mapped_column(Integer, default=0)
    level_3: Mapped[int] = mapped_column(Integer, default=0)
    level_4_plus: Mapped[int] = mapped_column(Integer, default=0)
    other_qualifications: Mapped[int] = mapped_column(Integer, default=0)


class Ks4Result(Base):
    """GCSE (Key Stage 4) headline performance measures, by school
    URN - the "Total" row across all pupil characteristic breakdowns.
    Populated by scripts/import_exam_results.py from DfE's school
    performance tables (explore-education-statistics). Republished
    annually.
    """
    __tablename__ = "ks4_results"

    urn: Mapped[int] = mapped_column(Integer, primary_key=True)
    academic_year: Mapped[str] = mapped_column(String(16), default="")
    pupil_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attainment8_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    progress8_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    grade5_english_maths_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    grade4_english_maths_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    ebacc_entry_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    ebacc_aps_avg: Mapped[float | None] = mapped_column(Float, nullable=True)


class Ks2Result(Base):
    """KS2 (SATs) headline performance measures, by school URN - the
    "Total" pupils, reading/writing/maths combined subject row.
    Populated by scripts/import_exam_results.py from DfE's Key Stage
    2 attainment data (explore-education-statistics). Republished
    annually.
    """
    __tablename__ = "ks2_results"

    urn: Mapped[int] = mapped_column(Integer, primary_key=True)
    academic_year: Mapped[str] = mapped_column(String(16), default="")
    pupil_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rwm_expected_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    rwm_higher_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


class SchoolCharacteristics(Base):
    """Free school meal (FSM) eligibility, by school URN - a common
    school-level deprivation/characteristics indicator. Populated by
    scripts/import_school_characteristics.py from DfE's "schools,
    pupils and their characteristics" school census.

    SEN status, class sizes, workforce and finance data were also
    investigated for this table but aren't published at individual
    school level as free open data - only aggregated to local
    authority/national, which isn't useful per-property. Not faked.
    """
    __tablename__ = "school_characteristics"

    urn: Mapped[int] = mapped_column(Integer, primary_key=True)
    academic_year: Mapped[str] = mapped_column(String(16), default="")
    fsm_eligible_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


class BroadbandCoverage(Base):
    """Fixed-line broadband availability, by full postcode (unit
    level, not just district) - from Ofcom's Connected Nations 2025
    data. Populated by scripts/import_broadband.py. Republished
    roughly annually.
    """
    __tablename__ = "broadband_coverage"

    postcode: Mapped[str] = mapped_column(String(16), primary_key=True)
    gigabit_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    ultrafast_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    superfast_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    below_uso_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


class MobileCoverage(Base):
    """Mobile signal coverage, by local authority (laua) - from the
    same Ofcom Connected Nations 2025 release as BroadbandCoverage,
    but mobile coverage is only published at local-authority level,
    not postcode-unit level (signal geography doesn't map to
    individual premises the way fixed-line does). Populated by
    scripts/import_mobile_coverage.py.
    """
    __tablename__ = "mobile_coverage"

    laua_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    la_name: Mapped[str] = mapped_column(String(150), default="")
    coverage_4g_outdoor_all_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_4g_indoor_all_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    no_4g_outdoor_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_5g_outdoor_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


# --- Live external lookups below don't need their own DB models -
# radon.py and heritage.py query the BGS/Historic England ArcGIS
# services directly per-request, cached in-memory like noise.py,
# rather than a bulk import (the underlying grid/point data changes
# rarely, but there's no practical need to mirror the whole GB radon
# atlas or the full national heritage list into our own database).
