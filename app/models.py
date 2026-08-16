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
