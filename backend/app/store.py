"""Сохранение диалогов и сериализация оценок для API."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters import extract_records, normalize_record
from app.models import Appeal, ConversationRow, Evaluation
from app.schemas import Conversation


def content_hash(conv: Conversation) -> str:
    blob = json.dumps(conv.raw, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def upsert_conversations(session: Session, payload: Any, dataset: str) -> dict[str, Any]:
    records = extract_records(payload)
    added, updated, unchanged, errors = 0, 0, 0, []
    next_seq = (session.scalar(select(func.max(ConversationRow.seq)).where(ConversationRow.dataset == dataset)) or 0) + 1
    ids: list[str] = []
    for i, record in enumerate(records):
        try:
            conv = normalize_record(record, dataset, i)
        except Exception as exc:  # noqa: BLE001
            errors.append({"index": i, "conversation_id": record.get("conversation_id"), "error": str(exc)})
            continue
        h = content_hash(conv)
        row = session.get(ConversationRow, conv.conversation_id)
        data = conv.model_dump(exclude={"raw"})
        if row is None:
            session.add(ConversationRow(
                conversation_id=conv.conversation_id, dataset=dataset, seq=next_seq + i if dataset == "upload" else i,
                channel=conv.channel, language=conv.language, audio_url=conv.audio_url, turns_count=conv.n_turns,
                content_hash=h, raw=record, normalized=data))
            added += 1
        elif row.content_hash != h or row.dataset != dataset:
            row.dataset, row.channel, row.language = dataset, conv.channel, conv.language
            row.audio_url, row.turns_count, row.content_hash = conv.audio_url, conv.n_turns, h
            row.raw, row.normalized, row.seq = record, data, i
            updated += 1
        else:
            if row.seq != i and dataset != "upload":
                row.seq = i
            unchanged += 1
        ids.append(conv.conversation_id)
    session.flush()
    return {"dataset": dataset, "total": len(records), "added": added, "updated": updated,
            "unchanged": unchanged, "errors": errors, "conversation_ids": ids}


def to_conversation(row: ConversationRow) -> Conversation:
    data = dict(row.normalized)
    data["raw"] = row.raw
    return Conversation.model_validate(data)


def latest_evaluation(session: Session, conversation_id: str, checklist_version_id: int | None = None) -> Evaluation | None:
    q = select(Evaluation).where(Evaluation.conversation_id == conversation_id)
    if checklist_version_id is not None:
        q = q.where(Evaluation.checklist_version_id == checklist_version_id)
    return session.scalar(q.order_by(Evaluation.last_requested_at.desc(), Evaluation.id.desc()).limit(1))


def latest_evaluations_map(session: Session, conversation_ids: list[str] | None = None,
                           checklist_version_id: int | None = None) -> dict[str, Evaluation]:
    q = select(Evaluation)
    if conversation_ids is not None:
        q = q.where(Evaluation.conversation_id.in_(conversation_ids))
    if checklist_version_id is not None:
        q = q.where(Evaluation.checklist_version_id == checklist_version_id)
    out: dict[str, Evaluation] = {}
    for ev in session.scalars(q.order_by(Evaluation.last_requested_at.asc(), Evaluation.id.asc())):
        out[ev.conversation_id] = ev
    return out


def conversation_summary(row: ConversationRow, ev: Evaluation | None) -> dict[str, Any]:
    return {
        "conversation_id": row.conversation_id, "dataset": row.dataset, "seq": row.seq,
        "channel": row.channel, "language": row.language, "turns_count": row.turns_count,
        "has_audio": bool(row.audio_url),
        "evaluation": None if ev is None else {
            "id": ev.id, "status": ev.status, "error": ev.error, "final_score": ev.final_score,
            "ai_score": ev.ai_score, "critical_flag": ev.critical_flag, "needs_review": ev.needs_review,
            "model_name": ev.model_name, "checklist_version_id": ev.checklist_version_id,
            "created_at": ev.created_at.isoformat(),
            "reason_code": ev.reason_code, "reason_title": (ev.reason or {}).get("title"), "resolution": ev.resolution,
        },
    }


def evaluation_dict(session: Session, ev: Evaluation, include_audit: bool = True) -> dict[str, Any]:
    from app.checklist.service import version_label
    from app.models import ChecklistVersion

    ver = session.get(ChecklistVersion, ev.checklist_version_id)
    appeals = session.scalars(select(Appeal).where(Appeal.evaluation_id == ev.id).order_by(Appeal.id)).all()
    d = {
        "id": ev.id, "conversation_id": ev.conversation_id, "checklist_version_id": ev.checklist_version_id,
        "checklist_label": version_label(session, ver) if ver else "",
        "job_id": ev.job_id, "provider": ev.provider, "model": ev.model, "model_name": ev.model_name,
        "status": ev.status, "error": ev.error, "ai_score": ev.ai_score, "final_score": ev.final_score,
        "critical_flag": ev.critical_flag, "needs_review": ev.needs_review, "criteria": ev.criteria,
        "sections": ev.sections, "signals": ev.signals, "cost_usd": ev.cost_usd, "duration_ms": ev.duration_ms,
        "created_at": ev.created_at.isoformat(), "last_requested_at": ev.last_requested_at.isoformat(),
        "appeals": [appeal_dict(a) for a in appeals],
        "reason": ev.reason, "reason_code": ev.reason_code, "resolution": ev.resolution,
        "reason_taxonomy_version_id": ev.reason_taxonomy_version_id,
    }
    if include_audit:
        d["audit"] = ev.audit
    return d


def appeal_dict(a: Appeal) -> dict[str, Any]:
    return {
        "id": a.id, "evaluation_id": a.evaluation_id, "conversation_id": a.conversation_id,
        "criterion_id": a.criterion_id, "comment": a.comment, "author": a.author, "status": a.status,
        "disputed_value": a.disputed_value, "score_before": a.score_before, "action": a.action,
        "new_value": a.new_value, "reason": a.reason, "reviewer": a.reviewer, "score_after": a.score_after,
        "created_at": a.created_at.isoformat(), "decided_at": a.decided_at.isoformat() if a.decided_at else None,
    }
