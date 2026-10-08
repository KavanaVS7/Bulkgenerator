"""Database engine / session setup."""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def make_engine(database_url: str) -> Engine:
    kwargs: dict = {}
    if database_url.startswith("sqlite"):
        # Worker threads and request threads each use their own Session, so SQLite's
        # same-thread check must be relaxed.
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in database_url or database_url in ("sqlite://", "sqlite:///"):
            # An in-memory DB exists per-connection; share a single one across threads.
            kwargs["poolclass"] = StaticPool

    engine = create_engine(database_url, **kwargs)

    if database_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            # WAL lets readers (status polling) proceed while a worker is writing.
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    # expire_on_commit=False: objects stay usable after commit without a refresh query.
    return sessionmaker(bind=engine, expire_on_commit=False)
