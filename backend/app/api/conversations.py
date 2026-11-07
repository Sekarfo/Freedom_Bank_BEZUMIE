"""Диалоги: загрузка набора у организаторов, загрузка файла, список, карточка, аудио."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.ingest.organizers_api import OrganizersAPIError, fetch_dataset, load_cached_dataset, resolve_audio_src
from app.models import ConversationRow, Evaluation
from app.schemas import FetchRequest, UploadRequest
from app.store import (conversation_summary, evaluation_dict, latest_evaluation, latest_evaluations_map,
                       to_conversation, upsert_conversations)

router = APIRouter(prefix="/api", tags=["conversations"])


def _dataset_label(group_id: str, dataset: str | None) -> str:
    if dataset:
        return dataset.strip()
    return "DEV" if group_id.strip().upper() == "DEV" else "PROD"


@router.post("/datasets/fetch")
def fetch(req: FetchRequest, db: Session = Depends(get_db)):
    dataset = _dataset_label(req.group_id, req.dataset)
    source = "api"
    warning = None
    try:
        payload = fetch_dataset(req.group_id.strip())
    except OrganizersAPIError as exc:
        payload = load_cached_dataset(req.group_id.strip())
        if payload is None:
            raise HTTPException(502, str(exc)) from exc
        source, warning = "cache", f"{exc}. Загружена локальная копия из data/raw."
    result = upsert_conversations(db, payload, dataset)
    data = payload.get("data") or {}
    result.update({"source": source, "warning": warning, "group_id": req.group_id,
                   "total_records_reported": data.get("total_records")})
    return result


@router.post("/conversations/upload")
def upload(req: UploadRequest, db: Session = Depends(get_db)):
    try:
        return upsert_conversations(db, req.payload, req.dataset or "upload")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/conversations")
def list_conversations(
    dataset: str | None = None, channel: str | None = None, language: str | None = None,
    status: str | None = Query(None, description="ok | error | not_evaluated"),
    needs_review: bool | None = None, critical: bool | None = None, q: str | None = None,
    db: Session = Depends(get_db),
):
    query = select(ConversationRow)
    if dataset:
        query = query.where(ConversationRow.dataset == dataset)
    if channel:
        query = query.where(ConversationRow.channel == channel)
    if language:
        query = query.where(ConversationRow.language == language)
    if q:
        query = query.where(ConversationRow.conversation_id.contains(q.strip()))
    rows = list(db.scalars(query.order_by(ConversationRow.dataset, ConversationRow.seq, ConversationRow.conversation_id)))
    evs = latest_evaluations_map(db, [r.conversation_id for r in rows])
    items = []
    for r in rows:
        ev = evs.get(r.conversation_id)
        if status == "not_evaluated" and ev is not None:
            continue
        if status in ("ok", "error") and (ev is None or ev.status != status):
            continue
        if needs_review is not None and (ev is None or ev.needs_review != needs_review):
            continue
        if critical is not None and (ev is None or bool(ev.critical_flag) != critical):
            continue
        items.append(conversation_summary(r, ev))
    datasets = sorted({d for (d,) in db.execute(select(ConversationRow.dataset).distinct())})
    return {"items": items, "total": len(items), "datasets": datasets}


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, evaluation_id: int | None = None, db: Session = Depends(get_db)):
    row = db.get(ConversationRow, conversation_id)
    if row is None:
        raise HTTPException(404, f"Диалог {conversation_id} не найден")
    conv = to_conversation(row)
    if evaluation_id is not None:
        ev = db.get(Evaluation, evaluation_id)
        if ev is None or ev.conversation_id != conversation_id:
            raise HTTPException(404, "Оценка не найдена")
    else:
        ev = latest_evaluation(db, conversation_id)
    history = db.scalars(select(Evaluation).where(Evaluation.conversation_id == conversation_id)
                         .order_by(Evaluation.id.desc())).all()
    return {
        "conversation": conv.model_dump(exclude={"raw"}) | {"has_audio": bool(row.audio_url)},
        "evaluation": evaluation_dict(db, ev) if ev else None,
        "history": [{"id": h.id, "created_at": h.created_at.isoformat(), "status": h.status,
                     "final_score": h.final_score, "model_name": h.model_name,
                     "checklist_version_id": h.checklist_version_id} for h in history],
    }


@router.get("/conversations/{conversation_id}/audio")
def audio(conversation_id: str, db: Session = Depends(get_db)):
    """Свежая ссылка на mp3 (у организаторов audio_url — HTML-страница, подпись mp3 живёт ~1 час)."""
    row = db.get(ConversationRow, conversation_id)
    if row is None or not row.audio_url:
        raise HTTPException(404, "У диалога нет записи")
    try:
        src = resolve_audio_src(row.audio_url)
    except OrganizersAPIError as exc:
        raise HTTPException(502, str(exc)) from exc
    return RedirectResponse(src, status_code=307)


@router.get("/conversations/{conversation_id}/audio-page")
def audio_page(conversation_id: str, db: Session = Depends(get_db)):
    row = db.get(ConversationRow, conversation_id)
    if row is None or not row.audio_url:
        raise HTTPException(404, "У диалога нет записи")
    return RedirectResponse(row.audio_url, status_code=307)
