"""Viewing checklist ticks kept with an account (18 Sep 2026, first-visitor
audit F2). The checklist told a buyer to open it on their phone at the
viewing, and every box on it was a picture of a box. Each item is now a
real checkbox with a line for what the seller or agent said and a Follow
up mark. The page keeps every change on the device; signed in, it also
posts them here, loads them from here first, and My properties and the
side-by-side comparison read "Viewed: 7 of 12 checked, 2 to follow up".

Every function is scoped to one account, the ownership boundary
app/watchlist.py keeps: a user id comes from the session, never from the
request, so no request can read or write another account's ticks. Rows
are keyed by account, postcode and house number (models.ViewingChecklistTicks).

Nothing here is a finding: the rows hold only what the buyer ticked and
typed. The page decides which items a reader may see; an item from a
locked check is never on the page for a reader who has not opened the
home, so its tick and answer are never rendered for them either.
"""
import json
import re
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import get_session
from app.models import ViewingChecklistTicks
from app.watchlist import same_home

# One page's items. The longest checklist the rules can build is about
# fifty (sixteen things to look at, the questions, the five asked at
# every viewing), so eighty leaves room and still bounds a request.
MAX_ITEMS = 80
# Items kept per home, the page's own first. A tick or answer on an item
# that has left the page (a question whose wording changed, or a locked
# item after a subscription ends) is kept, so it is there again if the
# item comes back, but never counted.
MAX_KEPT = 160
# What the seller or agent said: one line, as the page's field allows.
ANSWER_MAX = 200
# Homes per account. A buyer checks about thirty; this only stops a
# script filling the table.
MAX_HOMES = 300
# The width of the column, as watchlist_items.house_number.
HOUSE_MAX = 32

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_POSTCODE_RE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?\s*[0-9][A-Z]{2}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def canonical_postcode(postcode: str) -> str | None:
    """A full postcode as postcodes.io writes it ("M14 5TG"), or None.
    A home saved from My properties keeps its postcode as typed
    ("m145tg"), and the checklist has the looked-up one."""
    cleaned = (postcode or "").strip().upper()
    if not _POSTCODE_RE.match(cleaned):
        return None
    compact = re.sub(r"\s+", "", cleaned)
    return f"{compact[:-3]} {compact[-3:]}"


def clean_house(house_number: str) -> str | None:
    """The house number with its spacing tidied, or None when it is too
    long to store."""
    hn = " ".join((house_number or "").split())
    return hn if len(hn) <= HOUSE_MAX else None


def _answer(value: str) -> str:
    return " ".join(_CONTROL_RE.sub(" ", value or "").split())


def parse_form(pairs: list[tuple[str, str]]) -> dict | None:
    """The page's form as {key: {"c", "f", "a"}}, or None when it is not
    one. "k" names every item on the page, ticked or not; "c" and "f" are
    the ticked and Follow up boxes, each valued with its item's key; and
    "a_<key>" is what they said. A key the page did not name is ignored."""
    keys, ticked, follow, answers = [], set(), set(), {}
    for name, value in pairs:
        if name == "k":
            keys.append(value)
        elif name == "c":
            ticked.add(value)
        elif name == "f":
            follow.add(value)
        elif name.startswith("a_"):
            answers[name[2:]] = value
    keys = list(dict.fromkeys(keys))
    if not keys or len(keys) > MAX_ITEMS or not all(KEY_RE.match(k) for k in keys):
        return None
    items = {}
    for key in keys:
        said = _answer(answers.get(key, ""))
        if len(said) > ANSWER_MAX:
            return None
        items[key] = {"c": key in ticked, "f": key in follow, "a": said}
    return items


def _match(rows: list, house_number: str):
    """The row for this home: the exact house number first, then word by
    word, as the watchlist matches a saved home ("101" and "101 Maryhill
    Road", "Flat 1," and "flat 1")."""
    return (next((r for r in rows if r.house_number == house_number), None)
            or next((r for r in rows if same_home(r.house_number, house_number)), None))


def _items(row) -> dict:
    try:
        items = json.loads(row.items or "{}")
    except (TypeError, ValueError):
        return {}
    return items if isinstance(items, dict) else {}


def _summary(row) -> dict:
    return {
        "checked": row.checked, "total": row.total, "follow_up": row.follow_up,
        "answered": row.answered, "updated_at": row.updated_at,
        "started": bool(row.checked or row.follow_up or row.answered),
    }


def _ms(moment: datetime | None) -> int:
    if moment is None:
        return 0
    if moment.tzinfo is None:  # SQLite hands a naive datetime back
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def load(user_id: int, postcode: str, house_number: str) -> dict | None:
    """This account's ticks for the home, or None when it has none. The
    page renders them into its boxes, so the account is read first."""
    with get_session() as session:
        rows = session.scalars(select(ViewingChecklistTicks).where(
            ViewingChecklistTicks.user_id == user_id, ViewingChecklistTicks.postcode == postcode,
        )).all()
        row = _match(rows, house_number)
        if row is None:
            return None
        return {**_summary(row), "items": _items(row), "updated_ms": _ms(row.updated_at)}


def save(user_id: int, postcode: str, house_number: str, items: dict, _retry: bool = True) -> dict | None:
    """Keep the page's ticks for this account's home and return its
    counts, or None when the account already holds MAX_HOMES homes and
    this is a new one. Ticks and answers on items no longer on the page
    are kept after the page's own, uncounted, up to MAX_KEPT."""
    try:
        with get_session() as session:
            rows = session.scalars(select(ViewingChecklistTicks).where(
                ViewingChecklistTicks.user_id == user_id, ViewingChecklistTicks.postcode == postcode,
            )).all()
            row = _match(rows, house_number)
            if row is None:
                held = session.scalar(select(func.count()).select_from(ViewingChecklistTicks)
                                      .where(ViewingChecklistTicks.user_id == user_id))
                if held >= MAX_HOMES:
                    return None
                row = ViewingChecklistTicks(user_id=user_id, postcode=postcode, house_number=house_number)
                session.add(row)
                old = {}
            else:
                old = _items(row)
            merged = dict(items)
            for key, value in old.items():
                if len(merged) >= MAX_KEPT:
                    break
                if key not in merged and isinstance(value, dict) and (value.get("c") or value.get("f") or value.get("a")):
                    merged[key] = {"c": bool(value.get("c")), "f": bool(value.get("f")),
                                   "a": _answer(str(value.get("a") or ""))[:ANSWER_MAX]}
            row.items = json.dumps(merged, separators=(",", ":"))
            row.total = len(items)
            row.checked = sum(1 for v in items.values() if v["c"])
            row.follow_up = sum(1 for v in items.values() if v["f"])
            row.answered = sum(1 for v in items.values() if v["a"])
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
            return {**_summary(row), "updated_ms": _ms(row.updated_at)}
    except IntegrityError:
        # Two first saves for one home at once (a phone's beacon and its
        # last request): the other made the row, so this one updates it.
        if not _retry:
            raise
        return save(user_id, postcode, house_number, items, _retry=False)


def for_items(user_id: int, items: list[dict]) -> dict[int, dict]:
    """Checklist counts for some of this account's saved homes, by item
    id, in one query. Homes whose checklist was never started are absent."""
    by_postcode: dict[str, list[dict]] = {}
    for item in items:
        postcode = canonical_postcode(item.get("postcode", ""))
        if postcode:
            by_postcode.setdefault(postcode, []).append(item)
    if not by_postcode:
        return {}
    with get_session() as session:
        rows = session.scalars(select(ViewingChecklistTicks).where(
            ViewingChecklistTicks.user_id == user_id, ViewingChecklistTicks.postcode.in_(list(by_postcode)),
        )).all()
        out = {}
        for postcode, homes in by_postcode.items():
            here = [r for r in rows if r.postcode == postcode]
            for home in homes:
                house = clean_house(home.get("house_number", ""))
                row = _match(here, house) if house is not None else None
                if row is not None and row.total:
                    summary = _summary(row)
                    if summary["started"]:
                        out[home["id"]] = summary
        return out
