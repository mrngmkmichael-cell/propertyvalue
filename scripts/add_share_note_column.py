"""One-time migration: a note on a shared report link.

Same reasoning as scripts/add_referral_column.py - init_db() creates
missing tables but never alters an existing one, so a new column needs
a manual ALTER TABLE. Idempotent, safe to run more than once.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app.db import _get_engine  # noqa: E402

_STATEMENTS = [
    "ALTER TABLE share_links ADD COLUMN IF NOT EXISTS note VARCHAR(280) NOT NULL DEFAULT ''",
]


def main():
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set - nothing to migrate.")
        return
    engine = _get_engine()
    with engine.begin() as conn:
        for statement in _STATEMENTS:
            print(statement)
            conn.execute(text(statement))
    print("Done.")


if __name__ == "__main__":
    main()
