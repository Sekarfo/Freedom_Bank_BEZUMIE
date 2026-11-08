"""Чек-листы и их версии. Изменение критериев = новая версия (старые оценки ссылаются на свою)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checklist import service
from app.checklist.render import render_checklist
from app.db import get_db
from app.models import Checklist, ChecklistVersion
from app.schemas import ChecklistCreate, ChecklistVersionCreate

router = APIRouter(prefix="/api/v1/checklists", tags=["checklists"])


@router.get("")
def list_checklists(db: Session = Depends(get_db)):
    out = []
    for cl in db.scalars(select(Checklist).order_by(Checklist.id)):
        versions = db.scalars(select(ChecklistVersion).where(ChecklistVersion.checklist_id == cl.id)
                              .order_by(ChecklistVersion.version.desc())).all()
        out.append({"id": cl.id, "name": cl.name, "description": cl.description,
                    "versions": [{"id": v.id, "version": v.version, "note": v.note,
                                  "created_at": v.created_at.isoformat(), "criteria_count": len(v.criteria)}
                                 for v in versions]})
    return out


@router.post("")
def create_checklist(body: ChecklistCreate, db: Session = Depends(get_db)):
    try:
        ver = service.create_checklist(db, body.name, body.description, body.criteria, body.note)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return service.version_dict(db, ver)


@router.post("/{checklist_id}/versions")
def create_version(checklist_id: int, body: ChecklistVersionCreate, db: Session = Depends(get_db)):
    try:
        ver = service.create_version(db, checklist_id, body.criteria, body.note)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return service.version_dict(db, ver)


@router.get("/versions/{version_id}")
def get_version_by_id(version_id: int, db: Session = Depends(get_db)):
    ver = db.get(ChecklistVersion, version_id)
    if ver is None:
        raise HTTPException(404, "Версия не найдена")
    return service.version_dict(db, ver)


@router.get("/{checklist_id}/versions/{version}")
def get_version(checklist_id: int, version: int, db: Session = Depends(get_db)):
    ver = db.scalar(select(ChecklistVersion).where(ChecklistVersion.checklist_id == checklist_id,
                                                   ChecklistVersion.version == version))
    if ver is None:
        raise HTTPException(404, "Версия не найдена")
    return service.version_dict(db, ver)


@router.get("/versions/{version_id}/prompt")
def preview_prompt(version_id: int, db: Session = Depends(get_db)):
    """Как критерии попадут в промпт (для проверки после редактирования)."""
    ver = db.get(ChecklistVersion, version_id)
    if ver is None:
        raise HTTPException(404, "Версия не найдена")
    return {"text": render_checklist(service.to_config(ver))}


@router.get("/diff/{a_id}/{b_id}")
def diff(a_id: int, b_id: int, db: Session = Depends(get_db)):
    a, b = db.get(ChecklistVersion, a_id), db.get(ChecklistVersion, b_id)
    if a is None or b is None:
        raise HTTPException(404, "Версия не найдена")
    return service.diff_versions(a.criteria, b.criteria)
