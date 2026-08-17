"""Watchlist CRUD helpers, kept separate from the route handlers
in main.py the same way the external-API lookups live in
app/services/.
"""
from sqlalchemy import select

from app.db import get_session
from app.models import WatchlistItem


def get_item(user_id: int, postcode: str, house_number: str = "") -> dict | None:
    with get_session() as session:
        item = session.scalar(
            select(WatchlistItem).where(
                WatchlistItem.user_id == user_id,
                WatchlistItem.postcode == postcode,
                WatchlistItem.house_number == house_number,
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
            {
                "id": i.id,
                "postcode": i.postcode,
                "house_number": i.house_number,
                "note": i.note,
                "created_at": i.created_at,
                "last_snapshot": i.last_snapshot,
            }
            for i in items
        ]


def get_items_by_ids(user_id: int, item_ids: list[int]) -> list[dict]:
    with get_session() as session:
        items = session.scalars(
            select(WatchlistItem).where(
                WatchlistItem.user_id == user_id,
                WatchlistItem.id.in_(item_ids),
            )
        )
        by_id = {
            i.id: {"id": i.id, "postcode": i.postcode, "house_number": i.house_number, "note": i.note}
            for i in items
        }
        # Preserve the order the user selected them in, not DB order.
        return [by_id[i] for i in item_ids if i in by_id]


def save_item(user_id: int, postcode: str, house_number: str, note: str) -> None:
    with get_session() as session:
        existing = session.scalar(
            select(WatchlistItem).where(
                WatchlistItem.user_id == user_id,
                WatchlistItem.postcode == postcode,
                WatchlistItem.house_number == house_number,
            )
        )
        if existing:
            existing.note = note
        else:
            session.add(WatchlistItem(
                user_id=user_id, postcode=postcode, house_number=house_number, note=note,
            ))
        session.commit()


def update_snapshot(item_id: int, snapshot_json: str) -> None:
    with get_session() as session:
        item = session.get(WatchlistItem, item_id)
        if item:
            item.last_snapshot = snapshot_json
            session.commit()


def remove_item(user_id: int, item_id: int) -> None:
    with get_session() as session:
        item = session.get(WatchlistItem, item_id)
        if item and item.user_id == user_id:
            session.delete(item)
            session.commit()
