"""Версии справочника причин в БД: начальная загрузка, новая версия, активная версия, сравнение."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ReasonTaxonomyVersion
from app.reasons.taxonomy import ReasonTaxonomy, ReasonTopic, load_default_taxonomy, taxonomy_hash


def ensure_default_taxonomy(session: Session) -> None:
    if session.scalar(select(func.count()).select_from(ReasonTaxonomyVersion)):
        return
    tax, description = load_default_taxonomy()
    create_version(session, tax.topics, note=description or "Стартовый справочник", name=tax.name)


def create_version(session: Session, topics: list[ReasonTopic] | list[dict[str, Any]], note: str = "",
                   name: str = "") -> ReasonTaxonomyVersion:
    tax = ReasonTaxonomy(name=name or "Причины обращений", topics=topics)   # валидация и обязательный other.other
    dumped = tax.topics_dump()
    last = session.scalar(select(func.max(ReasonTaxonomyVersion.version)))
    ver = ReasonTaxonomyVersion(version=(last or 0) + 1, name=tax.name, note=note, topics=dumped,
                                taxonomy_hash=taxonomy_hash(dumped))
    session.add(ver)
    session.flush()
    return ver


def latest_version(session: Session) -> ReasonTaxonomyVersion:
    ver = session.scalar(select(ReasonTaxonomyVersion).order_by(ReasonTaxonomyVersion.version.desc()).limit(1))
    if ver is None:
        ensure_default_taxonomy(session)
        return latest_version(session)
    return ver


def get_version(session: Session, version_id: int | None = None) -> ReasonTaxonomyVersion:
    """Явная версия → она; иначе выбранная в настройках; иначе последняя."""
    if version_id is None:
        from app.settings_store import get_runtime_settings

        version_id = get_runtime_settings(session).get("reason_taxonomy_version_id")
        if version_id is None:
            return latest_version(session)
    ver = session.get(ReasonTaxonomyVersion, version_id)
    if ver is None:
        raise KeyError(f"Версия справочника причин {version_id} не найдена")
    return ver


def to_taxonomy(ver: ReasonTaxonomyVersion) -> ReasonTaxonomy:
    return ReasonTaxonomy(name=ver.name or "Причины обращений", topics=ver.topics)


def version_dict(ver: ReasonTaxonomyVersion, include_topics: bool = True) -> dict[str, Any]:
    d = {"id": ver.id, "version": ver.version, "name": ver.name, "note": ver.note, "taxonomy_hash": ver.taxonomy_hash,
         "created_at": ver.created_at.isoformat(), "topics_count": len(ver.topics),
         "codes_count": sum(len(t.get("subtopics") or []) for t in ver.topics)}
    if include_topics:
        d["topics"] = ver.topics
    return d


def diff_versions(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> dict[str, Any]:
    """Добавленные / удалённые / переименованные коды «тема.подтема»."""
    def flat(topics: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {f"{t['code']}.{s['code']}": {"topic": t["title"], "title": s["title"], "description": s.get("description", "")}
                for t in topics for s in t.get("subtopics") or []}
    fa, fb = flat(a), flat(b)
    return {
        "added": [{"code": c, **fb[c]} for c in fb if c not in fa],
        "removed": [{"code": c, **fa[c]} for c in fa if c not in fb],
        "changed": [{"code": c, "before": fa[c], "after": fb[c]} for c in fa.keys() & fb.keys() if fa[c] != fb[c]],
    }
