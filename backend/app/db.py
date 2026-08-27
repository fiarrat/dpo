"""Подключение к БД и сессии."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = settings.database_url
    kwargs: dict = {"echo": settings.sql_echo, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    return create_engine(url, **kwargs)


engine = _make_engine()

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):  # pragma: no cover - инфраструктура
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()
        # Встроенный lower() в SQLite работает только с ASCII: поиск по
        # «Роскомнадзор» не находил «Роскомнадзора». Подменяем на Python-версию,
        # которая знает про Unicode.
        dbapi_conn.create_function(
            "lower", 1, lambda v: v.lower() if isinstance(v, str) else v,
            deterministic=True,
        )

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_session() -> Iterator[Session]:
    """Зависимость FastAPI."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    from . import models  # noqa: F401  — регистрация мэпперов
    Base.metadata.create_all(engine)
