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
    __table_args__ = (
        UniqueConstraint("user_id", "postcode", "house_number", name="uq_user_postcode_housenum"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    postcode: Mapped[str] = mapped_column(String(16))
    house_number: Mapped[str] = mapped_column(String(32), default="")
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


class AgeProfile(Base):
    """Census 2021 age structure (TS007A), by LSOA - usual residents
    bucketed into six bands from the published five-year bands.
    Populated by scripts/import_census_demographics.py. Static until
    the 2031 census.
    """
    __tablename__ = "age_profile"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    under_15: Mapped[int] = mapped_column(Integer, default=0)
    age_15_24: Mapped[int] = mapped_column(Integer, default=0)
    age_25_44: Mapped[int] = mapped_column(Integer, default=0)
    age_45_64: Mapped[int] = mapped_column(Integer, default=0)
    age_65_84: Mapped[int] = mapped_column(Integer, default=0)
    age_85_plus: Mapped[int] = mapped_column(Integer, default=0)


class HousingType(Base):
    """Census 2021 accommodation type (TS044), by LSOA - households
    by dwelling type. Populated by scripts/import_census_demographics.py.
    Static until the 2031 census.
    """
    __tablename__ = "housing_type"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    detached: Mapped[int] = mapped_column(Integer, default=0)
    semi_detached: Mapped[int] = mapped_column(Integer, default=0)
    terraced: Mapped[int] = mapped_column(Integer, default=0)
    flat_or_converted: Mapped[int] = mapped_column(Integer, default=0)
    caravan_or_other: Mapped[int] = mapped_column(Integer, default=0)


class Tenure(Base):
    """Census 2021 tenure of household (TS054), by LSOA. Populated by
    scripts/import_census_demographics.py. Static until the 2031
    census.
    """
    __tablename__ = "tenure"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    owned_outright: Mapped[int] = mapped_column(Integer, default=0)
    owned_mortgage: Mapped[int] = mapped_column(Integer, default=0)
    shared_ownership: Mapped[int] = mapped_column(Integer, default=0)
    social_rented: Mapped[int] = mapped_column(Integer, default=0)
    private_rented: Mapped[int] = mapped_column(Integer, default=0)
    rent_free: Mapped[int] = mapped_column(Integer, default=0)


class OccupancyRating(Base):
    """Census 2021 occupancy rating for bedrooms (TS052), by LSOA -
    whether households have more or fewer bedrooms than the standard
    calls for. Populated by scripts/import_census_demographics.py.
    Static until the 2031 census.
    """
    __tablename__ = "occupancy_rating"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    plus_2_or_more: Mapped[int] = mapped_column(Integer, default=0)
    plus_1: Mapped[int] = mapped_column(Integer, default=0)
    exact: Mapped[int] = mapped_column(Integer, default=0)
    minus_1: Mapped[int] = mapped_column(Integer, default=0)
    minus_2_or_less: Mapped[int] = mapped_column(Integer, default=0)


class Ethnicity(Base):
    """Census 2021 ethnic group (TS021), by LSOA - top-level
    categories only (not the detailed sub-groups). Populated by
    scripts/import_census_demographics.py. Static until the 2031
    census.
    """
    __tablename__ = "ethnicity"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    asian: Mapped[int] = mapped_column(Integer, default=0)
    black: Mapped[int] = mapped_column(Integer, default=0)
    mixed: Mapped[int] = mapped_column(Integer, default=0)
    white: Mapped[int] = mapped_column(Integer, default=0)
    other: Mapped[int] = mapped_column(Integer, default=0)


class Religion(Base):
    """Census 2021 religion (TS030), by LSOA. Populated by
    scripts/import_census_demographics.py. Static until the 2031
    census.
    """
    __tablename__ = "religion"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    no_religion: Mapped[int] = mapped_column(Integer, default=0)
    christian: Mapped[int] = mapped_column(Integer, default=0)
    buddhist: Mapped[int] = mapped_column(Integer, default=0)
    hindu: Mapped[int] = mapped_column(Integer, default=0)
    jewish: Mapped[int] = mapped_column(Integer, default=0)
    muslim: Mapped[int] = mapped_column(Integer, default=0)
    sikh: Mapped[int] = mapped_column(Integer, default=0)
    other_religion: Mapped[int] = mapped_column(Integer, default=0)
    not_answered: Mapped[int] = mapped_column(Integer, default=0)


class CountryOfBirth(Base):
    """Census 2021 country of birth (TS004), by LSOA - top-level
    regions only. Populated by scripts/import_census_demographics.py.
    Static until the 2031 census.
    """
    __tablename__ = "country_of_birth"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    uk: Mapped[int] = mapped_column(Integer, default=0)
    eu: Mapped[int] = mapped_column(Integer, default=0)
    non_eu_europe: Mapped[int] = mapped_column(Integer, default=0)
    africa: Mapped[int] = mapped_column(Integer, default=0)
    middle_east_asia: Mapped[int] = mapped_column(Integer, default=0)
    americas_caribbean: Mapped[int] = mapped_column(Integer, default=0)
    oceania_other: Mapped[int] = mapped_column(Integer, default=0)
    british_overseas: Mapped[int] = mapped_column(Integer, default=0)


class GeneralHealth(Base):
    """Census 2021 general health (TS037), by LSOA - self-reported
    health of usual residents. Populated by
    scripts/import_census_demographics.py. Static until the 2031
    census.
    """
    __tablename__ = "general_health"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    very_good: Mapped[int] = mapped_column(Integer, default=0)
    good: Mapped[int] = mapped_column(Integer, default=0)
    fair: Mapped[int] = mapped_column(Integer, default=0)
    bad: Mapped[int] = mapped_column(Integer, default=0)
    very_bad: Mapped[int] = mapped_column(Integer, default=0)


class MaritalStatus(Base):
    """Census 2021 marital and civil partnership status (TS002), by
    LSOA - top-level categories only. Populated by
    scripts/import_census_demographics.py. Static until the 2031
    census.
    """
    __tablename__ = "marital_status"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    never_married: Mapped[int] = mapped_column(Integer, default=0)
    married_or_civil_partnership: Mapped[int] = mapped_column(Integer, default=0)
    separated: Mapped[int] = mapped_column(Integer, default=0)
    divorced_or_dissolved: Mapped[int] = mapped_column(Integer, default=0)
    widowed_or_surviving_partner: Mapped[int] = mapped_column(Integer, default=0)


class SocioeconomicClassification(Base):
    """Census 2021 NS-SEC (TS062), by LSOA - the official
    occupation-based socio-economic classification. Note this is NOT
    the same as commercial market-research "social grade" (AB/C1/C2/DE)
    - that scheme isn't published as an open bulk dataset, only via a
    more involved long-format API query, so NS-SEC (a very similar,
    equally standard measure, and what's actually free) is used instead.
    Populated by scripts/import_census_demographics.py. Static until
    the 2031 census.
    """
    __tablename__ = "socioeconomic_classification"

    lsoa_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    higher_managerial_professional: Mapped[int] = mapped_column(Integer, default=0)
    lower_managerial_professional: Mapped[int] = mapped_column(Integer, default=0)
    intermediate: Mapped[int] = mapped_column(Integer, default=0)
    small_employers_self_employed: Mapped[int] = mapped_column(Integer, default=0)
    lower_supervisory_technical: Mapped[int] = mapped_column(Integer, default=0)
    semi_routine: Mapped[int] = mapped_column(Integer, default=0)
    routine: Mapped[int] = mapped_column(Integer, default=0)
    never_worked_long_term_unemployed: Mapped[int] = mapped_column(Integer, default=0)
    full_time_students: Mapped[int] = mapped_column(Integer, default=0)


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


class AirQuality(Base):
    """Modelled annual mean background air pollutant concentrations,
    by 1km British National Grid cell - from Defra's national
    Pollution Climate Mapping (PCM), the standard UK modelled
    background dataset (used alongside real monitoring stations for
    official air quality reporting). Covers every 1km square in the
    UK, not just where a monitor happens to sit. Populated by
    scripts/import_air_quality.py. Republished annually - grid_easting/
    grid_northing are the cell's centre point (British National Grid,
    EPSG:27700), matching what postcodes.io already returns per
    postcode, so no separate coordinate conversion is needed.
    """
    __tablename__ = "air_quality"

    grid_easting: Mapped[int] = mapped_column(Integer, primary_key=True)
    grid_northing: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, default=0)
    no2_ug_m3: Mapped[float | None] = mapped_column(Float, nullable=True)
    pm25_ug_m3: Mapped[float | None] = mapped_column(Float, nullable=True)
    pm10_ug_m3: Mapped[float | None] = mapped_column(Float, nullable=True)


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


class RentalPrice(Base):
    """Median private-rental prices, by local authority (laua) and
    bedroom count - from ONS's Price Index of Private Rents (PIPR),
    the successor to their discontinued "Private rental market
    summary statistics". Updated monthly by ONS; this table isn't -
    it's a periodic manual re-run of scripts/import_rental_prices.py,
    same cadence as the Ofcom Connected Nations imports.

    There's no free per-property rental comparables source (unlike
    sold prices, tenancies aren't publicly registered), so this is an
    area + bedroom-count typical rent, not a "similar nearby lettings"
    comparison.
    """
    __tablename__ = "rental_price"

    laua_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    la_name: Mapped[str] = mapped_column(String(150), default="")
    period: Mapped[str] = mapped_column(String(7), default="")
    price_all: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_all_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_1bed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_1bed_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_2bed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_2bed_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_3bed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_3bed_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_4plus_bed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    change_4plus_bed_pct: Mapped[float | None] = mapped_column(Float, nullable=True)


# --- Live external lookups below don't need their own DB models -
# radon.py and heritage.py query the BGS/Historic England ArcGIS
# services directly per-request, cached in-memory like noise.py,
# rather than a bulk import (the underlying grid/point data changes
# rarely, but there's no practical need to mirror the whole GB radon
# atlas or the full national heritage list into our own database).
