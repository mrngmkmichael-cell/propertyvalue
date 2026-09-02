"""School shortlist CRUD helpers - same pattern as watchlist.py,
keyed by school URN instead of a postcode. Since 1 Sep 2026 each item
carries the school's current admission distance and grade, and a user
can ask to be emailed when a saved school's distance is republished.
"""
import datetime

from sqlalchemy import select

from app.db import get_session
from app.models import (
    School, SchoolAdmissionRadius, SchoolAlertOptIn, SchoolShortlistItem, SchoolShortlistSnapshot,
    User,
)


def _slug(name: str) -> str:
    from app.services.schools_db import _slugify
    return _slugify(name)


def _item_dict(session, i: SchoolShortlistItem) -> dict:
    school = session.get(School, i.urn)
    radius = session.get(SchoolAdmissionRadius, i.urn)
    return {
        "id": i.id,
        "urn": i.urn,
        "note": i.note,
        "created_at": i.created_at,
        "name": school.name if school else f"School URN {i.urn}",
        "slug": _slug(school.name) if school else "",
        "type": school.type_name if school else "",
        "ofsted_rating": school.ofsted_rating if school else None,
        "ofsted_rating_label": school.ofsted_rating_label if school else "",
        # Only schools with a real published figure have a page and a distance.
        "miles": round(radius.last_distance_miles, 2) if radius else None,
        "academic_year": radius.academic_year if radius else "",
        "has_page": radius is not None,
    }


def list_items(user_id: int) -> list[dict]:
    with get_session() as session:
        items = session.scalars(
            select(SchoolShortlistItem)
            .where(SchoolShortlistItem.user_id == user_id)
            .order_by(SchoolShortlistItem.created_at.desc())
        )
        return [_item_dict(session, i) for i in items]


def save_item(user_id: int, urn: int, note: str) -> None:
    with get_session() as session:
        existing = session.scalar(
            select(SchoolShortlistItem).where(
                SchoolShortlistItem.user_id == user_id,
                SchoolShortlistItem.urn == urn,
            )
        )
        if existing:
            existing.note = note
        else:
            session.add(SchoolShortlistItem(user_id=user_id, urn=urn, note=note))
        session.commit()


def remove_item(user_id: int, item_id: int) -> None:
    with get_session() as session:
        item = session.get(SchoolShortlistItem, item_id)
        if item and item.user_id == user_id:
            snap = session.get(SchoolShortlistSnapshot, item_id)
            if snap:
                session.delete(snap)
            session.delete(item)
            session.commit()


def alerts_enabled(user_id: int) -> bool:
    with get_session() as session:
        row = session.get(SchoolAlertOptIn, user_id)
        return bool(row and row.enabled)


def set_alerts(user_id: int, enabled: bool) -> None:
    with get_session() as session:
        row = session.get(SchoolAlertOptIn, user_id)
        if row is None:
            session.add(SchoolAlertOptIn(user_id=user_id, enabled=enabled))
        else:
            row.enabled = enabled
            row.updated_at = datetime.datetime.now(datetime.timezone.utc)
        session.commit()


def alert_subscribers() -> list[dict]:
    """Every opted-in user with their shortlist, each item carrying the
    current published figure and what we recorded last time."""
    with get_session() as session:
        rows = session.execute(
            select(User, SchoolAlertOptIn)
            .join(SchoolAlertOptIn, SchoolAlertOptIn.user_id == User.id)
            .where(SchoolAlertOptIn.enabled.is_(True))
        ).all()
        out = []
        for user, _ in rows:
            items = []
            for i in session.scalars(
                select(SchoolShortlistItem).where(SchoolShortlistItem.user_id == user.id)
            ):
                d = _item_dict(session, i)
                snap = session.get(SchoolShortlistSnapshot, i.id)
                d["snapshot"] = (
                    {"miles": snap.miles, "academic_year": snap.academic_year} if snap else None
                )
                items.append(d)
            out.append({"user_id": user.id, "email": user.email, "items": items})
        return out


def record_snapshot(item_id: int, miles: float | None, academic_year: str) -> None:
    with get_session() as session:
        snap = session.get(SchoolShortlistSnapshot, item_id)
        if snap is None:
            session.add(SchoolShortlistSnapshot(item_id=item_id, miles=miles, academic_year=academic_year))
        else:
            snap.miles, snap.academic_year = miles, academic_year
            snap.seen_at = datetime.datetime.now(datetime.timezone.utc)
        session.commit()
