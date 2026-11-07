"""Подключение к SQLite через SQLAlchemy."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
SessionLocal: sessionmaker[Session] = sessionmaker(expire_on_commit=False)


def configure(url: str) -> Engine:
    """Создаёт движок, таблицы и начальные данные. Повторный вызов пересоздаёт движок (тесты)."""
    global _engine
    kwargs: dict = {"connect_args": {"check_same_thread": False, "timeout": 30}}
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.replace("sqlite:///", "", 1)).parent.mkdir(parents=True, exist_ok=True)
    if ":memory:" in url:
        kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        if ":memory:" not in url:
            cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    from app import models  # noqa: F401  регистрирует таблицы

    Base.metadata.create_all(engine)
    SessionLocal.configure(bind=engine)
    _engine = engine

    from app.checklist.service import ensure_default_checklist

    with session_scope() as s:
        ensure_default_checklist(s)
    return engine


def get_engine() -> Engine:
    if _engine is None:
        from app.config import get_settings

        configure(get_settings().database_url)
    assert _engine is not None
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """Зависимость FastAPI."""
    get_engine()
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
