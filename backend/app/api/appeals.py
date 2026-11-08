"""Апелляции: подача, очередь проверяющего, решение с пересчётом итога."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.appeals.service import AppealError, create_appeal, decide_appeal, list_appeals
from app.db import get_db
from app.models import Evaluation
from app.schemas import AppealCreate, AppealDecision
from app.store import appeal_dict, evaluation_dict

router = APIRouter(prefix="/api/v1/appeals", tags=["appeals"])


@router.post("")
def create(body: AppealCreate, db: Session = Depends(get_db)):
    try:
        appeal = create_appeal(db, body.evaluation_id, body.criterion_id, body.comment, body.author)
    except AppealError as exc:
        raise HTTPException(400, str(exc)) from exc
    return appeal_dict(appeal)


@router.get("")
def queue(status: str | None = None, db: Session = Depends(get_db)):
    return list_appeals(db, status)


@router.post("/{appeal_id}/decision")
def decide(appeal_id: int, body: AppealDecision, db: Session = Depends(get_db)):
    try:
        appeal = decide_appeal(db, appeal_id, body.action, body.new_value, body.reason.strip(), body.reviewer)
    except AppealError as exc:
        raise HTTPException(400, str(exc)) from exc
    ev = db.get(Evaluation, appeal.evaluation_id)
    return {"appeal": appeal_dict(appeal), "evaluation": evaluation_dict(db, ev, include_audit=False)}
