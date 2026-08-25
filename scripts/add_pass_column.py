"""One-time migration: adds the pass_expires_at column to `users`.

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

with _get_engine().begin() as conn:
    conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS pass_expires_at TIMESTAMPTZ"))
print("pass_expires_at column ensured")
