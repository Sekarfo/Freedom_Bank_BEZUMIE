"""Запуск оценки (один диалог или пакет) и прогресс задач."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.checklist.service import get_version
from app.db import get_db
from app.jobs import create_job, job_dict, resolve_options, start_job_background
from app.models import ConversationRow, Evaluation, Job
from app.schemas import EvaluationRequest
from app.settings_store import get_runtime_settings
from app.store import evaluation_dict, latest_evaluations_map

router = APIRouter(prefix="/api", tags=["evaluations"])


@router.post("/evaluations")
async def start_evaluation(req: EvaluationRequest, db: Session = Depends(get_db)):
    ids = list(dict.fromkeys(req.conversation_ids))
    if req.all_in_dataset:
        ids += [r for (r,) in db.execute(select(ConversationRow.conversation_id)
                                         .where(ConversationRow.dataset == req.all_in_dataset)
                                         .order_by(ConversationRow.seq)) if r not in ids]
    if not ids:
        raise HTTPException(400, "Не выбраны диалоги для оценки")
    missing = [i for i in ids if db.get(ConversationRow, i) is None]
    if missing:
        raise HTTPException(404, f"Диалоги не найдены: {missing[:10]}")
    try:
        ver = get_version(db, req.checklist_version_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    if req.only_unevaluated:
        done = latest_evaluations_map(db, ids, ver.id)
        ids = [i for i in ids if i not in done or done[i].status != "ok"]
        if not ids:
            return {"job_id": None, "message": "Все выбранные диалоги уже оценены этой версией чек-листа"}
    opts = resolve_options(req.provider, get_runtime_settings(db))
    db.commit()  # job создаётся в отдельной сессии
    job_id = create_job(ids, ver.id, opts, req.force)
    start_job_background(job_id)
    return {"job_id": job_id, "total": len(ids), "checklist_version_id": ver.id, "options": opts.model_dump()}


@router.get("/jobs")
def list_jobs(limit: int = 20, db: Session = Depends(get_db)):
    return [job_dict(j) for j in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit))]


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Задача не найдена")
    return job_dict(job)


@router.get("/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: int, db: Session = Depends(get_db)):
    ev = db.get(Evaluation, evaluation_id)
    if ev is None:
        raise HTTPException(404, "Оценка не найдена")
    return evaluation_dict(db, ev)
