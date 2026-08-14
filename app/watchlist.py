"""Watchlist CRUD helpers, kept separate from the route handlers
in main.py the same way the external-API lookups live in
app/services/.
"""
from sqlalchemy import select

from app.db import get_session
from app.models import WatchlistItem


def get_item(user_id: int, postcode: str) -> dict | None:
    with get_session() as session:
        item = session.scalar(
            select(WatchlistItem).where(
                WatchlistItem.user_id == user_id,
                WatchlistItem.postcode == postcode,
            )
        )
        return {"id": item.id, "note": item.note} if item else None


def list_items(user_id: int) -> list[dict]:
    with get_session() as session:
        items = session.scalars(
            select(WatchlistItem)
            .where(WatchlistItem.user_id == user_id)
            .order_by(WatchlistItem.created_at.desc())
        )
        return [
            {"id": i.id, "postcode": i.postcode, "note": i.note, "created_at": i.created_at}
            for i in items
        ]


def save_item(user_id: int, postcode: str, note: str) -> None:
    with get_session() as session:
        existing = session.scalar(
            select(WatchlistItem).where(
                WatchlistItem.user_id == user_id,
                WatchlistItem.postcode == postcode,
            )
        )
        if existing:
            existing.note = note
        else:
            session.add(WatchlistItem(user_id=user_id, postcode=postcode, note=note))
        session.commit()


def remove_item(user_id: int, item_id: int) -> None:
    with get_session() as session:
        item = session.get(WatchlistItem, item_id)
        if item and item.user_id == user_id:
            session.delete(item)
            session.commit()
