"""School shortlist CRUD helpers - same pattern as watchlist.py,
keyed by school URN instead of a postcode.
"""
from sqlalchemy import select

from app.db import get_session
from app.models import School, SchoolShortlistItem


def list_items(user_id: int) -> list[dict]:
    with get_session() as session:
        items = session.scalars(
            select(SchoolShortlistItem)
            .where(SchoolShortlistItem.user_id == user_id)
            .order_by(SchoolShortlistItem.created_at.desc())
        )
        results = []
        for i in items:
            school = session.get(School, i.urn)
            results.append({
                "id": i.id,
                "urn": i.urn,
                "note": i.note,
                "created_at": i.created_at,
                "name": school.name if school else f"School URN {i.urn}",
            })
        return results


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
            session.delete(item)
            session.commit()
