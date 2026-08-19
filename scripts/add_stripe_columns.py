"""One-time migration: adds the Stripe billing columns to the existing
`users` table.

NOT run by the deployed app. app/db.py's init_db() only calls
Base.metadata.create_all(), which creates missing TABLES but never
alters an existing one - this project has no migration tooling
(Alembic etc.), so a new column on an already-live table needs a
manual ALTER TABLE, same as any other one-off schema change here would.

Idempotent (IF NOT EXISTS on every column) - safe to run more than
once, e.g. once against local dev and once against the real Neon
DATABASE_URL used by the Render deployment (point .env at whichever
one you want to migrate before running).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app.db import _get_engine  # noqa: E402

_STATEMENTS = [
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_customer_id VARCHAR(255)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS stripe_subscription_id VARCHAR(255)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(32)",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP WITH TIME ZONE",
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
