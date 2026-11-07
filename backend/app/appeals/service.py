"""Апелляции: решение не перезаписывает исходную оценку.

Три слоя на критерий: ai_value (исходная оценка ИИ) → review (решение проверяющего: значение, причина, кто, когда)
→ value (финальное). Итог пересчитывается калькулятором по финальным значениям.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brain.pipeline import finalize_scores
from app.checklist.service import to_config
from app.models import Appeal, ChecklistVersion, ConversationRow, Evaluation
from app.store import appeal_dict, to_conversation


class AppealError(ValueError):
    pass


def _criterion(ev: Evaluation, criterion_id: str) -> dict[str, Any]:
    for c in ev.criteria:
        if c["criterion_id"] == criterion_id:
            return c
    raise AppealError(f"В оценке {ev.id} нет критерия {criterion_id}")


def create_appeal(session: Session, evaluation_id: int, criterion_id: str, comment: str, author: str) -> Appeal:
    ev = session.get(Evaluation, evaluation_id)
    if ev is None:
        raise AppealError(f"Оценка {evaluation_id} не найдена")
    if ev.status != "ok":
        raise AppealError("Нельзя оспорить оценку со статусом error — сначала переоцените диалог")
    crit = _criterion(ev, criterion_id)
    existing = session.scalar(select(Appeal).where(Appeal.evaluation_id == evaluation_id,
                                                   Appeal.criterion_id == criterion_id, Appeal.status == "open"))
    if existing is not None:
        raise AppealError("По этому критерию уже есть открытая апелляция")
    appeal = Appeal(evaluation_id=evaluation_id, conversation_id=ev.conversation_id, criterion_id=criterion_id,
                    comment=comment.strip(), author=author, status="open", disputed_value=crit["value"],
                    score_before=ev.final_score)
    session.add(appeal)
    criteria = copy.deepcopy(ev.criteria)
    for c in criteria:
        if c["criterion_id"] == criterion_id:
            c["appeal_status"] = "open"
    ev.criteria = criteria
    session.flush()
    return appeal


def decide_appeal(session: Session, appeal_id: int, action: str, new_value: Any, reason: str, reviewer: str) -> Appeal:
    appeal = session.get(Appeal, appeal_id)
    if appeal is None:
        raise AppealError(f"Апелляция {appeal_id} не найдена")
    if appeal.status != "open":
        raise AppealError("Апелляция уже рассмотрена")
    ev = session.get(Evaluation, appeal.evaluation_id)
    ver = session.get(ChecklistVersion, ev.checklist_version_id)
    checklist = to_config(ver)
    cfg = checklist.by_id().get(appeal.criterion_id)
    if cfg is None:
        raise AppealError("Критерий отсутствует в версии чек-листа оценки")
    crit = _criterion(ev, appeal.criterion_id)
    if action == "confirm":
        new_value = crit["value"]
    elif not cfg.is_allowed(new_value):
        allowed = ", ".join("N/A" if v is None else str(v).lower() for v in cfg.allowed_values())
        raise AppealError(f"Недопустимое значение {new_value!r}; допустимо: {allowed}")

    now = datetime.now(timezone.utc)
    criteria = copy.deepcopy(ev.criteria)
    for c in criteria:
        if c["criterion_id"] == appeal.criterion_id:
            c["review"] = {"action": action, "value": new_value, "previous_value": c["value"], "reason": reason,
                           "reviewer": reviewer, "decided_at": now.isoformat(), "appeal_id": appeal.id,
                           "comment": appeal.comment}
            c["value"] = new_value
            c["needs_review"] = False
            c["appeal_status"] = "resolved"
    # Баллы по критериям канала оценки
    conv_row = session.get(ConversationRow, ev.conversation_id)
    if conv_row is not None:
        checklist = checklist.for_channel(to_conversation(conv_row).channel)
    score, flag, sections = finalize_scores(criteria, checklist)
    ev.criteria = criteria
    ev.final_score, ev.critical_flag, ev.sections = score, flag, sections
    ev.needs_review = any(c.get("needs_review") for c in criteria)

    appeal.status = "resolved"
    appeal.action, appeal.new_value, appeal.reason, appeal.reviewer = action, new_value, reason, reviewer
    appeal.decided_at = now
    appeal.score_after = score
    session.flush()
    return appeal


def list_appeals(session: Session, status: str | None = None) -> list[dict[str, Any]]:
    q = select(Appeal)
    if status:
        q = q.where(Appeal.status == status)
    out = []
    for a in session.scalars(q.order_by(Appeal.created_at.desc())):
        d = appeal_dict(a)
        ev = session.get(Evaluation, a.evaluation_id)
        crit = next((c for c in (ev.criteria if ev else []) if c["criterion_id"] == a.criterion_id), None)
        d["criterion"] = crit
        d["evaluation"] = None if ev is None else {"id": ev.id, "final_score": ev.final_score, "ai_score": ev.ai_score,
                                                   "model_name": ev.model_name}
        row = session.get(ConversationRow, a.conversation_id)
        if row is not None and crit is not None:
            conv = to_conversation(row)
            wanted: set[int] = set()
            for t in crit.get("evidence_turns") or []:
                wanted |= {t - 1, t, t + 1}
            d["fragment"] = [t.model_dump() for t in conv.turns if t.idx in wanted]
            d["channel"], d["language"] = conv.channel, conv.language
        out.append(d)
    return out
