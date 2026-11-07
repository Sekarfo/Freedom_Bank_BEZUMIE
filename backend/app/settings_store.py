"""Настройки оценки, которые меняются из интерфейса (хранятся в БД, по умолчанию — из .env)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import SettingRow

KEYS = ("provider", "checklist_version_id")


def defaults() -> dict[str, Any]:
    return {"provider": get_settings().default_provider, "checklist_version_id": None}


def get_runtime_settings(session: Session) -> dict[str, Any]:
    out = defaults()
    for row in session.query(SettingRow).filter(SettingRow.key.in_(KEYS)).all():
        out[row.key] = row.value
    return out


def update_runtime_settings(session: Session, values: dict[str, Any]) -> dict[str, Any]:
    for key, value in values.items():
        if key not in KEYS:
            continue
        row = session.get(SettingRow, key)
        if row is None:
            session.add(SettingRow(key=key, value=value))
        else:
            row.value = value
    session.flush()
    return get_runtime_settings(session)
