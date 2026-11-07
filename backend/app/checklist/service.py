"""Работа с чек-листами в БД: начальная загрузка, версии, сравнение."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.checklist.render import criteria_hash, load_default_checklist
from app.models import Checklist, ChecklistVersion
from app.schemas import ChecklistConfig, CriterionConfig

DEFAULT_CHECKLIST_ID = 1


def ensure_default_checklist(session: Session) -> None:
    if session.get(Checklist, DEFAULT_CHECKLIST_ID) is not None:
        return
    config, description = load_default_checklist()
    cl = Checklist(id=DEFAULT_CHECKLIST_ID, name=config.name, description=description)
    session.add(cl)
    session.flush()
    _add_version(session, cl.id, config.criteria, note="Исходная версия из PDF организаторов")


def _add_version(session: Session, checklist_id: int, criteria: list[CriterionConfig], note: str) -> ChecklistVersion:
    config = ChecklistConfig(criteria=criteria)  # валидация уникальности и шкал
    dumped = [c.model_dump() for c in config.criteria]
    last = session.scalar(select(func.max(ChecklistVersion.version)).where(ChecklistVersion.checklist_id == checklist_id))
    ver = ChecklistVersion(checklist_id=checklist_id, version=(last or 0) + 1, note=note,
                           criteria=dumped, criteria_hash=criteria_hash(dumped))
    session.add(ver)
    session.flush()
    return ver


def create_checklist(session: Session, name: str, description: str, criteria: list[CriterionConfig], note: str) -> ChecklistVersion:
    cl = Checklist(name=name, description=description)
    session.add(cl)
    session.flush()
    return _add_version(session, cl.id, criteria, note or "Первая версия")


def create_version(session: Session, checklist_id: int, criteria: list[CriterionConfig], note: str) -> ChecklistVersion:
    if session.get(Checklist, checklist_id) is None:
        raise KeyError(f"Чек-лист {checklist_id} не найден")
    return _add_version(session, checklist_id, criteria, note)


def latest_version(session: Session, checklist_id: int = DEFAULT_CHECKLIST_ID) -> ChecklistVersion:
    ver = session.scalar(select(ChecklistVersion).where(ChecklistVersion.checklist_id == checklist_id)
                         .order_by(ChecklistVersion.version.desc()).limit(1))
    if ver is None:
        raise KeyError(f"У чек-листа {checklist_id} нет версий")
    return ver


def get_version(session: Session, version_id: int | None) -> ChecklistVersion:
    if version_id is None:
        from app.settings_store import get_runtime_settings

        version_id = get_runtime_settings(session).get("checklist_version_id")
        if version_id is None:
            return latest_version(session)
    ver = session.get(ChecklistVersion, version_id)
    if ver is None:
        raise KeyError(f"Версия чек-листа {version_id} не найдена")
    return ver


def to_config(ver: ChecklistVersion, name: str = "") -> ChecklistConfig:
    return ChecklistConfig(name=name or f"v{ver.version}", criteria=ver.criteria)


def version_label(session: Session, ver: ChecklistVersion) -> str:
    cl = session.get(Checklist, ver.checklist_id)
    return f"{cl.name if cl else 'Чек-лист'} · v{ver.version}"


def version_dict(session: Session, ver: ChecklistVersion) -> dict[str, Any]:
    cl = session.get(Checklist, ver.checklist_id)
    return {
        "id": ver.id, "checklist_id": ver.checklist_id, "checklist_name": cl.name if cl else "",
        "version": ver.version, "note": ver.note, "criteria": ver.criteria,
        "criteria_hash": ver.criteria_hash, "created_at": ver.created_at.isoformat(),
        "label": version_label(session, ver),
    }


def diff_versions(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> dict[str, Any]:
    """Сравнение двух версий: добавленные, удалённые и изменённые критерии (по полям)."""
    ma = {c["criterion_id"]: c for c in a}
    mb = {c["criterion_id"]: c for c in b}
    changed = []
    for cid in ma.keys() & mb.keys():
        fields = sorted(k for k in set(ma[cid]) | set(mb[cid]) if ma[cid].get(k) != mb[cid].get(k))
        if fields:
            changed.append({"criterion_id": cid, "fields": fields,
                            "before": {k: ma[cid].get(k) for k in fields},
                            "after": {k: mb[cid].get(k) for k in fields}})
    return {
        "added": [mb[c] for c in mb if c not in ma],
        "removed": [ma[c] for c in ma if c not in mb],
        "changed": sorted(changed, key=lambda x: x["criterion_id"]),
    }
