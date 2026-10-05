"""The revenue line on /admin counted a test account (5 Oct 2026).

Every other account figure on the page leaves test accounts out and
says so; the monthly revenue estimate did not. It read £38.30 from four
subscribers when customers paid £28.31 from three: an owner account
with an active monthly subscription was the fourth.
"""
import datetime

from app import auth, db
from app import main as app_main
from app.models import User


def test_monthly_revenue_and_its_subscriber_count_leave_test_accounts_out(client):
    now = datetime.datetime.now(datetime.timezone.utc)
    with db.get_session() as session:
        before = app_main._admin_metrics(session, now)
        added = []
        for email, plan, status in (("owner-mrr@example.test", "monthly", "active"),
                                    ("owner-trial@example.test", "monthly", "trialing"),
                                    ("real-mrr@customer.test", "quarterly", "active")):
            user = User(email=email, password_hash=auth.hash_password("password123"),
                        stripe_customer_id="cus_" + email, stripe_subscription_id="sub_" + email,
                        subscription_status=status, is_premium=True, plan=plan)
            session.add(user)
            added.append(user)
        session.commit()
        try:
            after = app_main._admin_metrics(session, now)
        finally:
            for user in added:
                session.delete(user)
            session.commit()
    assert after["active_subscriber_count"] - before["active_subscriber_count"] == 1
    assert round(after["mrr_estimate"] - before["mrr_estimate"], 2) == round(24.99 / 3, 2)
    assert after["trialing_count"] == before["trialing_count"]
