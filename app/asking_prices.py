"""Asking prices kept with saved homes (18 Sep 2026, first-visitor audit
F1). A buyer types a home's asking price on its Comparables page to see
where it sits among the sales recorded nearby; signed in, with the home
in My properties, they can keep it there, and it shows on My properties
and in the side-by-side comparison. The same row keeps the home's
council tax band since 18 Sep 2026 (F4, save_band below).

Every function is scoped to one account, the same ownership boundary
app/watchlist.py keeps. See models.SavedAskingPrice for why a row is
matched to its home on every read rather than by a foreign key.
"""
import re
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db import get_session
from app.models import SavedAskingPrice, WatchlistItem
from app.watchlist import same_home


def _postcode_key(postcode: str) -> str:
    # A home saved from My properties keeps its postcode as typed
    # ("M145TG"), while the Comparables page has the looked-up "M14 5TG".
    return re.sub(r"\s+", "", postcode or "").upper()


def _belongs(row: SavedAskingPrice | None, item: WatchlistItem) -> bool:
    return bool(
        row is not None and row.user_id == item.user_id
        and row.postcode == item.postcode and row.house_number == item.house_number
    )


def saved_home(user_id: int, postcode: str, house_number: str) -> dict | None:
    """This account's saved row for the home, with any asking price kept
    on it, or None when the home is not in My properties. One round trip.
    The house number matches as the watchlist matches it: exact first,
    then word by word ("101" and "101 Maryhill Road")."""
    key = _postcode_key(postcode)
    with get_session() as session:
        pairs = session.execute(
            select(WatchlistItem, SavedAskingPrice)
            .outerjoin(SavedAskingPrice, SavedAskingPrice.item_id == WatchlistItem.id)
            .where(
                WatchlistItem.user_id == user_id,
                func.upper(func.replace(WatchlistItem.postcode, " ", "")) == key,
            )
        ).all()
        match = (next((p for p in pairs if p[0].house_number == house_number), None)
                 or next((p for p in pairs if same_home(p[0].house_number, house_number)), None))
        if match is None:
            return None
        item, row = match
        kept = _belongs(row, item) and row.price is not None
        return {
            "item_id": item.id,
            "price": row.price if kept else None,
            "updated_at": row.updated_at if kept else None,
            # The council tax band kept with the home (18 Sep 2026, F4).
            "band": row.band if _belongs(row, item) and row.band else None,
        }


def for_items(user_id: int, items: list[dict]) -> dict[int, dict]:
    """Asking prices for some of this account's saved homes, by item id,
    in one query. Homes without one are simply absent."""
    by_id = {i["id"]: i for i in items}
    if not by_id:
        return {}
    with get_session() as session:
        rows = session.scalars(
            select(SavedAskingPrice).where(
                SavedAskingPrice.user_id == user_id, SavedAskingPrice.item_id.in_(list(by_id)),
            )
        ).all()
    out = {}
    for row in rows:
        item = by_id.get(row.item_id)
        if (item and row.price is not None and row.postcode == item["postcode"]
                and row.house_number == item["house_number"]):
            out[row.item_id] = {"price": row.price, "updated_at": row.updated_at}
    return out


def save(user_id: int, item_id: int, price: int | None) -> bool:
    """Keep an asking price on one of this account's saved homes, or clear
    it with None. Returns False, and writes nothing, when the home is not
    this account's."""
    with get_session() as session:
        item = session.get(WatchlistItem, item_id)
        if item is None or item.user_id != user_id:
            return False
        row = session.get(SavedAskingPrice, item_id)
        if row is None:
            row = SavedAskingPrice(item_id=item_id)
            session.add(row)
        elif not _belongs(row, item):
            # 18 Sep 2026 (F4): the band a removed home kept was that
            # home's, so it is not handed to the one taking its row over.
            row.band = None
        # A row left by a removed home belongs to nobody now; it is taken
        # over by the home that holds its id (SQLite can reuse one).
        row.user_id, row.postcode, row.house_number = user_id, item.postcode, item.house_number
        row.price = price
        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        return True


# The council tax band kept with a saved home (18 Sep 2026, first-visitor
# audit F4). The home's band is on the seller's bill and in no open
# data, so the buyer picks it on /running-costs; kept here, that page
# opens on it next time and the report's running-costs line uses it. The
# same row and the same ownership rules as the asking price above.
BAND_LETTERS = frozenset("ABCDEFGHI")  # A to H in England and Scotland, A to I in Wales


def save_band(user_id: int, item_id: int, band: str | None) -> bool:
    """Keep a council tax band with one of this account's saved homes, or
    clear it with None. Returns False, and writes nothing, when the home
    is not this account's or the band is not a band."""
    if band is not None and band not in BAND_LETTERS:
        return False
    with get_session() as session:
        item = session.get(WatchlistItem, item_id)
        if item is None or item.user_id != user_id:
            return False
        row = session.get(SavedAskingPrice, item_id)
        if row is None:
            row = SavedAskingPrice(item_id=item_id, price=None)
            session.add(row)
        elif not _belongs(row, item):
            # A removed home's price was that home's, as in save().
            row.price = None
        row.user_id, row.postcode, row.house_number = user_id, item.postcode, item.house_number
        row.band = band
        row.updated_at = datetime.now(timezone.utc)
        session.commit()
        return True


def band_for(user_id: int, item: dict) -> str | None:
    """The band kept with one of this account's saved homes (an item as
    watchlist.list_items gives it), or None. One primary-key read."""
    with get_session() as session:
        row = session.get(SavedAskingPrice, item["id"])
        if (row is None or row.user_id != user_id or row.postcode != item.get("postcode")
                or row.house_number != item.get("house_number")):
            return None
        return row.band or None
