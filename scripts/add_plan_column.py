"""One-time migration: adds the subscription-plan column to the
existing `users` table.

Same reasoning as the other scripts/add_*.py migrations - init_db()
only creates missing tables, never alters an existing one. Idempotent
(IF NOT EXISTS), safe to run more than once.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app.db import _get_engine  # noqa: E402

_STATEMENTS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS plan VARCHAR(32)",
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
