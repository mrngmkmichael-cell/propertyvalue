"""Watchlist CRUD helpers, kept separate from the route handlers
in main.py the same way the external-API lookups live in
app/services/.
"""
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import get_session
from app.models import User, WatchlistItem


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


def all_items_with_owner_email() -> list[dict]:
    """Every watchlist item across every user, with the owner's email -
    used only by the scheduled alert check (main.py), never by a
    per-user route, since it deliberately ignores the user_id ownership
    boundary every other function here enforces."""
    with get_session() as session:
        rows = session.execute(
            select(WatchlistItem, User.email).join(User, WatchlistItem.user_id == User.id)
        ).all()
        return [
            {
                "id": item.id, "user_id": item.user_id, "email": email,
                "postcode": item.postcode, "house_number": item.house_number,
                "last_snapshot": item.last_snapshot,
            }
            for item, email in rows
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


def update_snapshot(user_id: int, item_id: int, snapshot_json: str) -> None:
    with get_session() as session:
        item = session.get(WatchlistItem, item_id)
        if item and item.user_id == user_id:
            item.last_snapshot = snapshot_json
            session.commit()


def remove_item(user_id: int, item_id: int) -> None:
    with get_session() as session:
        item = session.get(WatchlistItem, item_id)
        if item and item.user_id == user_id:
            session.delete(item)
            session.commit()


def digest_subscribers() -> list[dict]:
    """Everyone who has opted into the weekly digest, with their saved
    items. Opt-in only: the change-alert email promises readers they
    hear from us only when something changed, so a scheduled send may
    never include someone who has not asked for it."""
    with get_session() as session:
        rows = session.execute(
            select(WatchlistItem, User.id, User.email)
            .join(User, WatchlistItem.user_id == User.id)
            .where(User.weekly_digest.is_(True))
            .order_by(WatchlistItem.created_at.desc())
        ).all()

    by_user: dict[int, dict] = {}
    for item, user_id, email in rows:
        entry = by_user.setdefault(user_id, {"user_id": user_id, "email": email, "items": []})
        entry["items"].append({
            "id": item.id,
            "postcode": item.postcode,
            "house_number": item.house_number,
            "last_snapshot": item.last_snapshot,
        })
    return list(by_user.values())


def set_weekly_digest(user_id: int, enabled: bool) -> None:
    with get_session() as session:
        user = session.get(User, user_id)
        if user is not None:
            user.weekly_digest = bool(enabled)
            session.commit()


def mark_digest_sent(user_id: int) -> None:
    with get_session() as session:
        user = session.get(User, user_id)
        if user is not None:
            user.digest_sent_at = datetime.now(timezone.utc)
            session.commit()
