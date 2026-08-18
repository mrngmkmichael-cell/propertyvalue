"""User-submitted reviews for a property's area or a school.

Every competitor researched for this project (Crystal Roof's
resident reviews, Locrating's parent reviews) treats this as a trust
signal open data genuinely can't replace - the point isn't to
compete on data breadth here, it's to start collecting the one thing
none of our other sources can give us. Starts empty; content
accumulates as real users write reviews. No moderation queue/admin
UI yet - a length cap and one-review-per-user-per-target are the
only guards, matching this project's habit of shipping the honest
minimum rather than a half-built admin system nobody's asked for.
"""
from sqlalchemy import func, select

from app.db import get_session
from app.models import Review

MAX_BODY_LENGTH = 1000


def submit(user_id: int, target_type: str, target_key: str, rating: int, body: str) -> None:
    rating = max(1, min(5, int(rating)))
    body = (body or "").strip()[:MAX_BODY_LENGTH]

    with get_session() as session:
        existing = session.scalar(
            select(Review).where(
                Review.user_id == user_id, Review.target_type == target_type, Review.target_key == target_key,
            )
        )
        if existing:
            existing.rating = rating
            existing.body = body
        else:
            session.add(Review(
                user_id=user_id, target_type=target_type, target_key=target_key, rating=rating, body=body,
            ))
        session.commit()


def summary_for(target_type: str, target_key: str) -> dict:
    """{"average": float | None, "count": int, "reviews": [...]} -
    reviews listed newest first, deliberately without any
    user-identifying field (see Review's docstring)."""
    with get_session() as session:
        rows = session.scalars(
            select(Review)
            .where(Review.target_type == target_type, Review.target_key == target_key)
            .order_by(Review.created_at.desc())
        ).all()

    if not rows:
        return {"average": None, "count": 0, "reviews": []}

    average = sum(r.rating for r in rows) / len(rows)
    return {
        "average": round(average, 1),
        "count": len(rows),
        "reviews": [
            {"rating": r.rating, "body": r.body, "created_at": r.created_at} for r in rows
        ],
    }


def user_review(user_id: int, target_type: str, target_key: str) -> dict | None:
    """The current user's own review for this target, if any - used
    to pre-fill the submission form as an edit rather than a fresh
    write."""
    with get_session() as session:
        r = session.scalar(
            select(Review).where(
                Review.user_id == user_id, Review.target_type == target_type, Review.target_key == target_key,
            )
        )
        return {"rating": r.rating, "body": r.body} if r else None


def summaries_for_many(target_type: str, target_keys: list[str]) -> dict[str, dict]:
    """Batched average+count for several targets at once (e.g. every
    school shown on a School Guide page) - one query rather than one
    per target, same pattern as schools_db.py's batched IN-queries."""
    if not target_keys:
        return {}
    with get_session() as session:
        rows = session.execute(
            select(Review.target_key, func.avg(Review.rating), func.count(Review.id))
            .where(Review.target_type == target_type, Review.target_key.in_(target_keys))
            .group_by(Review.target_key)
        ).all()
    return {key: {"average": round(avg, 1), "count": count} for key, avg, count in rows}
