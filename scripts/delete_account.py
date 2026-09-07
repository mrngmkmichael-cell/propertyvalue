"""Delete one account and everything that hangs off it.

For test sign-ups and mistyped addresses. Refuses Premium accounts and
anyone with a Stripe subscription, since those have money attached and
belong in Stripe first. Rows in other tables that point at the account
are removed where the column is required and blanked where it is not,
so nothing is left pointing at a user that no longer exists.

    .venv/Scripts/python.exe scripts/delete_account.py someone@example.com --yes

Without --yes it only reports what it would remove.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from sqlalchemy import text  # noqa: E402

from app.db import get_session  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("email")
    ap.add_argument("--yes", action="store_true", help="actually delete; otherwise a dry run")
    args = ap.parse_args()
    email = args.email.strip().lower()

    with get_session() as s:
        user = s.execute(text("SELECT id, email, is_premium, stripe_subscription_id FROM users WHERE email = :e"), {"e": email}).first()
        if user is None:
            print(f"No account with the address {email}.")
            return 1
        if user.is_premium or user.stripe_subscription_id:
            print(f"Refusing: {email} is Premium or has a Stripe subscription. Deal with it in Stripe first.")
            return 2

        # Every column in the database that points at users.id.
        refs = s.execute(text("""
            SELECT tc.table_name, kcu.column_name, c.is_nullable
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name
            JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name
            JOIN information_schema.columns c ON c.table_name = tc.table_name AND c.column_name = kcu.column_name
            WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name = 'users' AND ccu.column_name = 'id'
        """)).all()

        plan = []
        for table, column, nullable in refs:
            n = s.execute(text(f"SELECT count(*) FROM {table} WHERE {column} = :i"), {"i": user.id}).scalar()
            if n:
                plan.append((table, column, nullable == "YES", n))
        print(f"Account {email} (id {user.id}):")
        for table, column, nullable, n in plan:
            print(f"   {'blank' if nullable else 'delete'} {n} row(s) in {table}")
        if not args.yes:
            print("Dry run. Add --yes to delete.")
            return 0

        for table, column, nullable, n in plan:
            if nullable:
                s.execute(text(f"UPDATE {table} SET {column} = NULL WHERE {column} = :i"), {"i": user.id})
            else:
                s.execute(text(f"DELETE FROM {table} WHERE {column} = :i"), {"i": user.id})
        s.execute(text("DELETE FROM users WHERE id = :i"), {"i": user.id})
        s.commit()
        print(f"Deleted {email}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
