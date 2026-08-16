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
