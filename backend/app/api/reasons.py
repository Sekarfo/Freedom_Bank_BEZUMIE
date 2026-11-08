"""Причины обращений: справочник (версии), аналитика «причина × качество», поиск новых подтем."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ReasonTaxonomyVersion
from app.reasons import service
from app.reasons.analytics import reasons_summary
from app.reasons.discovery import discover
from app.reasons.taxonomy import RESOLUTIONS, render_taxonomy
from app.schemas import ReasonDiscoverRequest, ReasonTaxonomyCreate
from app.settings_store import get_runtime_settings, update_runtime_settings

router = APIRouter(prefix="/api/v1", tags=["reasons"])


def _version_or_404(db: Session, version_id: int) -> ReasonTaxonomyVersion:
    ver = db.get(ReasonTaxonomyVersion, version_id)
    if ver is None:
        raise HTTPException(404, f"Версия справочника причин {version_id} не найдена")
    return ver


@router.get("/reasons/taxonomy")
def active_taxonomy(db: Session = Depends(get_db)):
    """Версия, которой классифицируются новые оценки (из настроек, иначе последняя)."""
    ver = service.get_version(db)
    return service.version_dict(ver) | {"active": True, "resolutions": RESOLUTIONS}


@router.get("/reasons/versions")
def list_versions(db: Session = Depends(get_db)):
    active = service.get_version(db).id
    vers = db.scalars(select(ReasonTaxonomyVersion).order_by(ReasonTaxonomyVersion.version.desc())).all()
    return [service.version_dict(v, include_topics=False) | {"active": v.id == active} for v in vers]


@router.get("/reasons/versions/{version_id}")
def get_version(version_id: int, db: Session = Depends(get_db)):
    return service.version_dict(_version_or_404(db, version_id))


@router.post("/reasons/versions")
def create_version(body: ReasonTaxonomyCreate, db: Session = Depends(get_db)):
    """Новая версия справочника. Старые оценки остаются со своей версией; activate=true — классифицировать ею новые."""
    try:
        ver = service.create_version(db, body.topics, note=body.note, name=body.name)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.activate:
        update_runtime_settings(db, {"reason_taxonomy_version_id": ver.id})
    return service.version_dict(ver) | {"active": body.activate or get_runtime_settings(db).get(
        "reason_taxonomy_version_id") is None}


@router.post("/reasons/versions/{version_id}/activate")
def activate(version_id: int, db: Session = Depends(get_db)):
    ver = _version_or_404(db, version_id)
    update_runtime_settings(db, {"reason_taxonomy_version_id": ver.id})
    return service.version_dict(ver, include_topics=False) | {"active": True}


@router.get("/reasons/versions/{version_id}/prompt")
def preview_prompt(version_id: int, db: Session = Depends(get_db)):
    """Как справочник попадёт в системный промпт."""
    return {"text": render_taxonomy(service.to_taxonomy(_version_or_404(db, version_id)))}


@router.get("/reasons/diff/{a_id}/{b_id}")
def diff(a_id: int, b_id: int, db: Session = Depends(get_db)):
    return service.diff_versions(_version_or_404(db, a_id).topics, _version_or_404(db, b_id).topics)


@router.get("/analytics/reasons")
def analytics(dataset: str | None = None, channel: str | None = None, language: str | None = None,
              date_from: str | None = None, date_to: str | None = None, taxonomy_version_id: int | None = None,
              level: str = "topic", db: Session = Depends(get_db)):
    if level not in ("topic", "subtopic"):
        raise HTTPException(422, "level: topic | subtopic")
    return reasons_summary(db, dataset, channel, language, date_from, date_to, taxonomy_version_id, level)


@router.post("/reasons/discover")
async def discover_reasons(body: ReasonDiscoverRequest, db: Session = Depends(get_db)):
    """Предложить новые подтемы по описаниям из «Прочего». Справочник не меняется: проверьте предложения
    и сохраните proposed_topics новой версией (POST /api/reasons/versions)."""
    client = None
    if body.use_llm:
        from app.jobs import _client_factory, resolve_options

        client = _client_factory(resolve_options(body.provider, get_runtime_settings(db)).provider)
    tax = service.to_taxonomy(service.get_version(db))
    return await discover(db, tax, dataset=body.dataset, min_cluster=body.min_cluster, limit=body.limit, client=client)
