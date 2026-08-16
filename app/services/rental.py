"""Typical private-rental prices from our own rental_price table
(ONS Price Index of Private Rents, populated offline by
scripts/import_rental_prices.py) - keyed by local authority, same
lookup pattern as app/services/mobile_coverage.py.

This is an area + bedroom-count typical rent, not a "similar nearby
lettings" comparison - there's no free equivalent to Land Registry's
sold-price register for rental transactions, since tenancies aren't
publicly registered the way property sales are.
"""
from app.db import get_session, is_configured
from app.models import RentalPrice

BEDROOM_ROWS = [
    ("price_1bed", "change_1bed_pct", "1 bed"),
    ("price_2bed", "change_2bed_pct", "2 bed"),
    ("price_3bed", "change_3bed_pct", "3 bed"),
    ("price_4plus_bed", "change_4plus_bed_pct", "4+ bed"),
]


def rental_for_laua(laua_code: str) -> dict | None:
    if not is_configured() or not laua_code:
        return None
    with get_session() as session:
        row = session.get(RentalPrice, laua_code)
        if row is None or not row.price_all:
            return None
        by_bedroom = [
            {"label": label, "price": getattr(row, price_field), "change_pct": getattr(row, change_field)}
            for price_field, change_field, label in BEDROOM_ROWS
            if getattr(row, price_field) is not None
        ]
        return {
            "la_name": row.la_name,
            "period": row.period,
            "price_all": row.price_all,
            "change_all_pct": row.change_all_pct,
            "by_bedroom": by_bedroom,
        }
