"""Выгрузка results.json в формате 1.0 (приложение к гайду)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ConversationRow, Evaluation
from app.store import latest_evaluations_map

# Модель-судья всегда вызывается через внешний API (Claude или OpenAI-совместимый).
MODEL_MODE = "external_api"


def export_checklist(session: Session, dataset: str, checklist_version_id: int | None = None,
                     conversation_ids: list[str] | None = None):
    """Чек-лист, по которому сделаны выгружаемые оценки, — против него и валидируем results.json.
    Без этого после замены критерия через интерфейс валидатор сверял бы файл со встроенным чек-листом."""
    from app.checklist.service import get_version, to_config
    from app.models import ChecklistVersion

    if checklist_version_id is None:
        q = select(ConversationRow.conversation_id).where(ConversationRow.dataset == dataset)
        ids = conversation_ids or [r for (r,) in session.execute(q)]
        used = {e.checklist_version_id for e in latest_evaluations_map(session, ids).values()}
        checklist_version_id = used.pop() if len(used) == 1 else None
    ver = session.get(ChecklistVersion, checklist_version_id) if checklist_version_id else get_version(session, None)
    return to_config(ver)


def _criterion_out(c: dict[str, Any], use_final: bool) -> dict[str, Any]:
    value = c["value"] if use_final else c.get("ai_value", c["value"])
    rationale = c.get("rationale") or ""
    review = c.get("review")
    if use_final and review and review.get("action") == "change":
        rationale = f"{rationale} Решение проверяющего: {review.get('reason')}".strip()
    return {"criterion_id": c["criterion_id"], "value": value, "rationale": rationale,
            "evidence_turns": [int(t) for t in c.get("evidence_turns") or []]}


def evaluation_record(ev: Evaluation | None, conversation_id: str, use_final: bool = True) -> dict[str, Any]:
    if ev is None:
        return {"conversation_id": conversation_id, "status": "error", "criteria": [], "score": None,
                "error": "Диалог не был оценён."}
    if ev.status != "ok":
        return {"conversation_id": conversation_id, "status": "error", "criteria": [], "score": None,
                "error": ev.error or "Не удалось получить ответ модели."}
    criteria = [_criterion_out(c, use_final) for c in ev.criteria]
    if use_final:
        score, flag = ev.final_score, ev.critical_flag
    else:
        score = ev.ai_score
        flag = any(c.get("ai_value") is True for c in ev.criteria if c.get("is_critical_flag"))
    return {"conversation_id": conversation_id, "status": "ok", "criteria": criteria,
            "score": {"final_score": score, "critical_flag": bool(flag)}}


def build_results(session: Session, dataset: str, checklist_version_id: int | None = None,
                  conversation_ids: list[str] | None = None, use_final: bool = True) -> dict[str, Any]:
    q = select(ConversationRow).where(ConversationRow.dataset == dataset)
    if conversation_ids:
        q = q.where(ConversationRow.conversation_id.in_(conversation_ids))
    rows = list(session.scalars(q.order_by(ConversationRow.seq, ConversationRow.conversation_id)))
    evs = latest_evaluations_map(session, [r.conversation_id for r in rows], checklist_version_id)
    results = [evaluation_record(evs.get(r.conversation_id), r.conversation_id, use_final) for r in rows]

    names: list[str] = []
    for e in evs.values():
        if e.model_name and e.model_name not in names:
            names.append(e.model_name)
    return {
        "schema_version": "1.0",
        "team_name": get_settings().team_name,
        "model": {"mode": MODEL_MODE, "name": "; ".join(names) or "not evaluated"},
        "results": results,
    }


def write_results(data: dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p
