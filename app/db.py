"""Database setup. Uses Neon (hosted Postgres) via DATABASE_URL —
plain local SQLite isn't viable here because Render's free tier
wipes its filesystem on every restart/idle spin-down, which would
lose the watchlist data.
"""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def is_configured() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


_engine = None
_SessionLocal = None


def _get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = os.environ["DATABASE_URL"]
        # Neon connection strings use postgresql://; SQLAlchemy needs
        # the psycopg3 driver spelled out explicitly.
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        _engine = create_engine(url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db():
    if not is_configured():
        return
    engine = _get_engine()
    Base.metadata.create_all(engine)


@contextmanager
def get_session():
    if _SessionLocal is None:
        _get_engine()
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()
