"""One-time migration: adds the weekly-digest opt-in columns to the
existing `users` table.

Same reasoning as scripts/add_referral_column.py - init_db() only
creates missing tables, never alters an existing one, so a new column
needs a manual ALTER TABLE. Idempotent (IF NOT EXISTS), safe to run
more than once.

The default is FALSE on purpose. Every change-alert email already sent
tells the reader they only hear from us when something actually
changed; defaulting existing accounts into a scheduled email would
break that promise for people who never asked for it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app.db import _get_engine  # noqa: E402

_STATEMENTS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS weekly_digest BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS digest_sent_at TIMESTAMPTZ",
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
