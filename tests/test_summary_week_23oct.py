"""The morning summary's week line (23 Oct 2026, asked for by Michael when
the site "seems very quiet"): this week against last, the seven full days
to yesterday against the seven before, so the movement is visible as it
happens. Test accounts and today's part day are in neither week."""
import datetime

from app import db
from app import main as app_main
from app.models import PageSeen, PageView, PremiumUnlock, User

UTC = datetime.timezone.utc
# A Thursday afternoon years ahead, where no other test leaves rows.
NOW = datetime.datetime(2031, 3, 20, 15, 0, tzinfo=UTC)


def _at(day: int) -> datetime.datetime:
    return datetime.datetime(2031, 3, day, 12, 0, tzinfo=UTC)


def test_the_week_line_sets_seven_full_days_against_the_seven_before(client):
    with db.get_session() as session:
        def account(email, day, paid_on=None):
            user = User(email=email, password_hash="x", created_at=_at(day),
                        premium_since=_at(paid_on) if paid_on else None)
            session.add(user)
            session.flush()
            return user

        first = account("week-first@customer.test", 14)
        payer = account("week-payer@customer.test", 15, paid_on=17)
        account("week-yesterday@customer.test", 19)
        last_week = account("week-last@customer.test", 8)
        account("week-start@customer.test", 6)       # the first day of the week before
        today = account("week-today@customer.test", 20)
        account("week-early@customer.test", 5)       # before either week
        tester = account("week-tester@example.com", 16, paid_on=16)
        for user, day in ((first, 14), (payer, 15), (last_week, 9), (tester, 16), (today, 20)):
            session.add(PremiumUnlock(user_id=user.id, postcode="M1 1AA", house_number=str(user.id),
                                      created_at=_at(day)))
        session.add(PageView(path=app_main.PAYWALL_PATH, user_id=first.id, created_at=_at(18)))
        for day, views in ((13, 3), (19, 2), (12, 4), (6, 1), (20, 9), (5, 7)):
            session.add_all(PageSeen(path="/", created_at=_at(day)) for _ in range(views))
        session.commit()
        week = app_main._admin_metrics(session, NOW)["week_compare"]

    assert week == {"people": (5, 5), "accounts": (3, 2), "free_reports": (2, 1),
                    "paywall": (1, 0), "subscriptions": (1, 0)}
    assert app_main._week_compare_line(week) == (
        "\U0001F4C5 7 days to yesterday, the 7 before in brackets: people <b>5</b> (5), "
        "new accounts <b>3</b> (2), free reports used <b>2</b> (1), reached the price <b>1</b> (0), "
        "new subscriptions <b>1</b> (0)")


def test_the_line_groups_thousands():
    week = {"people": (1516, 985), "accounts": (0, 0), "free_reports": (0, 0),
            "paywall": (0, 0), "subscriptions": (0, 0)}
    assert "people <b>1,516</b> (985)" in app_main._week_compare_line(week)
