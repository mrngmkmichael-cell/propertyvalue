"""Residents' and estate management companies from Companies House, and
the offices they are registered to. Backs /estate-charges/managing-agents,
the per-agent pages and the name search. Everything here is a fact from
the register or a count of them; the site never says "managed by",
only "registered to X's office". See scripts/import_estate_companies.py.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from sqlalchemy import func, select

from app.db import get_session, is_configured
from app.models import EstateCompany
from app.services import _cache

_AGENTS_PATH = Path(__file__).resolve().parent.parent / "data" / "managing_agents.json"
AGENTS_TTL_S = 24 * 3600
MIN_FOR_PAGE = 5  # an office page needs this many companies to be worth indexing
COMPANIES_HOUSE = "https://find-and-update.company-information.service.gov.uk/company/"

try:
    _AGENTS = {a["slug"]: a for a in json.loads(_AGENTS_PATH.read_text(encoding="utf-8"))["agents"]}
except (OSError, ValueError, KeyError):
    _AGENTS = {}


def agent(slug: str) -> dict | None:
    return _AGENTS.get(slug)


def agents_table() -> dict:
    """Every office with companies registered to it, most first, with
    the totals the hub quotes. One group-by a day."""
    cached = _cache.get_persistent(("estate_agents_table", 1), AGENTS_TTL_S)
    if cached is not None:
        return cached
    if not is_configured():
        return {"agents": [], "total": 0, "attributed": 0, "snapshot": ""}
    with get_session() as session:
        rows = session.execute(
            select(EstateCompany.agent_slug, func.count(), func.min(EstateCompany.incorporated), func.max(EstateCompany.incorporated))
            .group_by(EstateCompany.agent_slug)
        ).all()
    total = sum(n for _, n, _, _ in rows)
    agents = []
    for slug, n, first, last in rows:
        meta = _AGENTS.get(slug or "")
        if not meta or meta.get("kind") == "service":
            continue
        agents.append({
            "slug": slug, "name": meta["name"], "kind": meta.get("kind", "agent"), "website": meta.get("website", ""),
            "count": n, "share_pct": round(100 * n / total, 1) if total else 0,
            "first": first.isoformat() if first else "", "last": last.isoformat() if last else "",
        })
    agents.sort(key=lambda a: -a["count"])
    result = {"agents": agents, "total": total, "attributed": sum(a["count"] for a in agents)}
    _cache.set_persistent(("estate_agents_table", 1), result)
    return result


def agent_page(slug: str) -> dict | None:
    """One office: its companies by year of incorporation and the most
    recent two hundred by name, each linking to the register."""
    meta = _AGENTS.get(slug)
    if not meta or meta.get("kind") == "service":
        return None
    cached = _cache.get_persistent(("estate_agent_page", 1, slug), AGENTS_TTL_S)
    if cached is not None:
        return cached
    if not is_configured():
        return None
    with get_session() as session:
        rows = session.execute(
            select(EstateCompany.company_number, EstateCompany.name, EstateCompany.incorporated, EstateCompany.post_town)
            .where(EstateCompany.agent_slug == slug)
            .order_by(EstateCompany.incorporated.desc().nullslast(), EstateCompany.name)
        ).all()
    if not rows:
        return None
    by_year: dict[int, int] = {}
    for _, _, inc, _ in rows:
        if inc:
            by_year[inc.year] = by_year.get(inc.year, 0) + 1
    this_year = dt.date.today().year
    years = [(y, by_year.get(y, 0)) for y in range(this_year - 9, this_year + 1)]
    result = {
        **meta, "count": len(rows),
        "years": years,
        "recent": [{"number": n, "name": name, "incorporated": inc.isoformat() if inc else "", "url": COMPANIES_HOUSE + n}
                   for n, name, inc, _ in rows[:200]],
        "indexable": len(rows) >= MIN_FOR_PAGE,
    }
    _cache.set_persistent(("estate_agent_page", 1, slug), result)
    return result


def search(q: str, limit: int = 50) -> list[dict]:
    """Companies whose name contains the words typed, for "who manages my
    estate". Case-insensitive, whole phrase, capped."""
    q = " ".join(q.split())
    if len(q) < 3 or not is_configured():
        return []
    with get_session() as session:
        rows = session.execute(
            select(EstateCompany.company_number, EstateCompany.name, EstateCompany.incorporated,
                   EstateCompany.address, EstateCompany.agent_slug)
            .where(EstateCompany.name.ilike(f"%{q}%"))
            .order_by(EstateCompany.name)
            .limit(limit)
        ).all()
    out = []
    for number, name, inc, address, slug in rows:
        meta = _AGENTS.get(slug or "")
        out.append({
            "number": number, "name": name, "incorporated": inc.isoformat() if inc else "",
            "address": address, "url": COMPANIES_HOUSE + number,
            "agent": meta["name"] if meta and meta.get("kind") != "service" else "",
            "agent_slug": slug if meta and meta.get("kind") != "service" else "",
        })
    return out


def indexable_agent_slugs() -> list[str]:
    return [a["slug"] for a in agents_table()["agents"] if a["count"] >= MIN_FOR_PAGE]
