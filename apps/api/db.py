"""Database engine/session. DATABASE_URL selects the dialect:

- docker/prod: postgresql+psycopg2://...
- local dev:   sqlite:///./data/govtrans.db (created automatically)

Sync SQLAlchemy by design: the orchestrator is async at the HTTP edge
(ToFu/SSE) but DB writes are short transactions executed via
asyncio.to_thread where needed. One code path for both dialects.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" not in url:
            Path(url.split("///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, **kwargs)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@event.listens_for(engine, "connect")
def _sqlite_pragma(dbapi_conn, _):  # pragma: no cover - dialect guard
    if engine.url.get_backend_name() == "sqlite":
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
