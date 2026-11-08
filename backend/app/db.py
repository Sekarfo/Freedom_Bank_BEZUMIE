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
    _migrate(engine)
    SessionLocal.configure(bind=engine)
    _engine = engine

    from app.checklist.service import ensure_default_checklist
    from app.reasons.service import ensure_default_taxonomy

    with session_scope() as s:
        ensure_default_checklist(s)
        ensure_default_taxonomy(s)
    return engine


# Столбцы и настройки режимов «локальная модель» / «гибрид», которых больше нет.
_OBSOLETE_COLUMNS = ("mode", "local_model", "hybrid_strategy")
_OBSOLETE_SETTINGS = ("mode", "local_model", "hybrid_strategy", "cloud_provider")
_NEW_COLUMNS = {"reason_taxonomy_version_id": "INTEGER", "reason_code": "VARCHAR(100)", "reason_topic": "VARCHAR(50)",
                "resolution": "VARCHAR(20)", "reason": "JSON"}
_NEW_INDEXED = ("reason_taxonomy_version_id", "reason_code", "reason_topic", "resolution")


def _migrate(engine: Engine) -> None:
    """Старые базы: удалить устаревшие столбцы (mode был NOT NULL и ломал бы вставку новых оценок)."""
    from sqlalchemy import inspect, text

    cols = {c["name"] for c in inspect(engine).get_columns("evaluations")}
    with engine.begin() as conn:
        for col in _OBSOLETE_COLUMNS:
            if col in cols:
                conn.execute(text(f"ALTER TABLE evaluations DROP COLUMN {col}"))
        # Причины обращений: create_all не добавляет столбцы в существующую таблицу.
        for col, ddl in _NEW_COLUMNS.items():
            if col not in cols:
                conn.execute(text(f"ALTER TABLE evaluations ADD COLUMN {col} {ddl}"))
                if col in _NEW_INDEXED:
                    conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_evaluations_{col} ON evaluations ({col})"))
        keys = ", ".join(f"'{k}'" for k in _OBSOLETE_SETTINGS)
        conn.execute(text(f"DELETE FROM settings WHERE key IN ({keys})"))


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
