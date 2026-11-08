"""Ручная разметка и сравнение ИИ с ней (экран «Качество» интерфейса).

Разметка — решения проверяющего по каждому критерию (тот же формат, что у ИИ). Утверждённая разметка —
эталон: «эксперимент» для каждой версии чек-листа сравнивает с ней последнюю оценку каждой модели —
точность по критериям и языкам, ошибка итога, список расхождений. Это честная замена AI-эталона
из eval/: метрика считается по людям и пересчитывается сама по мере разметки.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.brain.prompts import PROMPT_VERSIONS
from app.checklist.service import to_config
from app.models import Annotation, ChecklistVersion, ConversationRow, Evaluation
from app.scoring.calculator import compute_score
from app.ui.mapping import UIError, from_ui_value, to_ui_value, turn_ids

STATUSES = ("draft", "approved")
SPLITS = ("unassigned", "calibration", "validation")
REFERENCE = "Разметка (эталон)"


# --------------------------------------------------------------------------- разметка
def annotation_ui(a: Annotation) -> dict[str, Any]:
    return {"id": str(a.id), "conversationId": a.conversation_id, "checklistId": str(a.checklist_version_id),
            "status": a.status, "split": a.split, "scenarioGroup": a.scenario_group, "reviewer": a.reviewer,
            "notes": a.notes, "decisions": a.decisions or [], "source": a.source,
            "updatedAt": a.updated_at.isoformat() if a.updated_at else None}


def list_annotations(session: Session) -> list[dict[str, Any]]:
    """Свежие первыми: интерфейс берёт первую разметку диалога."""
    rows = session.scalars(select(Annotation).order_by(Annotation.updated_at.desc(), Annotation.id.desc())).all()
    return [annotation_ui(a) for a in rows]


def _check_decisions(decisions: list[dict[str, Any]], ver: ChecklistVersion, n_turns: int, approved: bool) -> list[dict]:
    spec = to_config(ver).by_id()
    out, seen = [], set()
    for d in decisions or []:
        cid = str(d.get("criterionId") or "")
        if cid not in spec:
            raise UIError(f"Критерия {cid!r} нет в выбранной версии чек-листа")
        if cid in seen:
            raise UIError(f"Критерий {cid} размечен дважды")
        seen.add(cid)
        value = from_ui_value(d.get("value"))
        if not spec[cid].is_allowed(value):
            raise UIError(f"{spec[cid].title}: значение {d.get('value')!r} недопустимо")
        rationale = str(d.get("rationale") or "").strip()
        if not rationale:
            raise UIError(f"{spec[cid].title}: нужно обоснование")
        bad = [t for t in turn_ids(d.get("evidence") or []) if not 1 <= t <= n_turns]
        if bad:
            raise UIError(f"{spec[cid].title}: нет реплик {bad}")
        if not d.get("evidence") and not d.get("checkedRange"):
            raise UIError(f"{spec[cid].title}: укажите реплики-доказательства или проверенный участок")
        out.append({"criterionId": cid, "value": to_ui_value(value), "rationale": rationale,
                    "evidence": [str(x) for x in d.get("evidence") or []], "checkedRange": d.get("checkedRange"),
                    "needsReview": bool(d.get("needsReview")), "reviewReason": d.get("reviewReason")})
    if approved:
        missing = [c.title for cid, c in spec.items() if cid not in seen]
        if missing:
            raise UIError("Для утверждения разметьте все критерии: " + ", ".join(missing))
    return out


def save_annotation(session: Session, body: dict[str, Any]) -> Annotation:
    cid = str(body.get("conversationId") or "")
    row = session.get(ConversationRow, cid)
    if row is None:
        raise UIError(f"Диалог {cid} не найден")
    try:
        ver = session.get(ChecklistVersion, int(body.get("checklistId")))
    except (TypeError, ValueError):
        ver = None
    if ver is None:
        raise UIError("Версия чек-листа не найдена")
    status = body.get("status") or "draft"
    split = body.get("split") or "unassigned"
    if status not in STATUSES or split not in SPLITS:
        raise UIError("status: draft | approved; split: unassigned | calibration | validation")
    if status == "approved" and not (str(body.get("scenarioGroup") or "").strip() and str(body.get("reviewer") or "").strip()):
        raise UIError("Для утверждения укажите группу сценария и проверяющего")
    decisions = _check_decisions(body.get("decisions") or [], ver, row.turns_count, status == "approved")
    a = session.scalar(select(Annotation).where(Annotation.conversation_id == cid,
                                                Annotation.checklist_version_id == ver.id))
    if a is None:
        a = Annotation(conversation_id=cid, checklist_version_id=ver.id)
        session.add(a)
    a.status, a.split = status, split
    a.scenario_group = str(body.get("scenarioGroup") or "").strip()
    a.reviewer = str(body.get("reviewer") or "").strip()
    a.notes = str(body.get("notes") or "")
    a.decisions = decisions
    a.source = body.get("source") or "manual"
    a.updated_at = datetime.now(timezone.utc)
    session.flush()
    return a


# --------------------------------------------------------------------------- сравнение ИИ с разметкой
def _same(a: Any, b: Any) -> bool:
    return a == b and type(a) is type(b)


def experiments(session: Session) -> list[dict[str, Any]]:
    approved = session.scalars(select(Annotation).where(Annotation.status == "approved")).all()
    by_version: dict[int, list[Annotation]] = defaultdict(list)
    for a in approved:
        by_version[a.checklist_version_id].append(a)
    out = []
    for ver_id, anns in sorted(by_version.items(), key=lambda kv: -kv[0]):
        ver = session.get(ChecklistVersion, ver_id)
        if ver is None:
            continue
        checklist = to_config(ver)
        conv_ids = [a.conversation_id for a in anns]
        rows = {r.conversation_id: r for r in session.scalars(
            select(ConversationRow).where(ConversationRow.conversation_id.in_(conv_ids)))}
        evs = session.scalars(select(Evaluation).where(Evaluation.conversation_id.in_(conv_ids),
                                                       Evaluation.checklist_version_id == ver_id)
                              .order_by(Evaluation.created_at.asc(), Evaluation.id.asc())).all()
        latest: dict[str, dict[str, Evaluation]] = defaultdict(dict)       # модель → диалог → последняя оценка
        for e in evs:
            latest[e.model_name or e.model or "model"][e.conversation_id] = e
        models, disagreements = [], []
        for model, per_conv in sorted(latest.items()):
            stat = _compare(model, anns, per_conv, rows, checklist, disagreements)
            models.append(stat)
        if not models:
            models.append({"model": "нет оценок ИИ", "succeeded": 0, "attempted": len(anns), "failed": len(anns),
                           "approved": len(anns), "criterionAccuracy": None, "totalScoreMAE": None,
                           "averageDurationMs": None, "perLanguage": {}, "perCriterion": {}})
        out.append({"id": f"reference-v{ver.version}-{ver.id}",
                    "method": f"ИИ против утверждённой ручной разметки · чек-лист v{ver.version}",
                    "promptVersion": PROMPT_VERSIONS.get("judge", ""), "requested": len(anns),
                    "interruptionReason": None, "finishedAt": datetime.now(timezone.utc).isoformat(),
                    "models": models, "disagreements": disagreements[:200]})
    return out


def _compare(model: str, anns: list[Annotation], per_conv: dict[str, Evaluation], rows: dict[str, ConversationRow],
             checklist, disagreements: list[dict[str, Any]]) -> dict[str, Any]:  # noqa: ANN001
    per_lang: dict[str, dict[str, int]] = defaultdict(lambda: {"correct": 0, "total": 0})
    per_crit: dict[str, dict[str, int]] = defaultdict(lambda: {"correct": 0, "total": 0})
    maes, durations, succeeded = [], [], 0
    for a in anns:
        ev = per_conv.get(a.conversation_id)
        if ev is None or ev.status != "ok":
            continue
        succeeded += 1
        durations.append(ev.duration_ms or 0)
        lang = rows[a.conversation_id].language if a.conversation_id in rows else "?"
        channel = rows[a.conversation_id].channel if a.conversation_id in rows else "chat"
        ai = {c["criterion_id"]: c.get("ai_value", c.get("value")) for c in ev.criteria or []}
        human = {d["criterionId"]: from_ui_value(d["value"]) for d in a.decisions or []}
        for cid, hv in human.items():
            if cid not in ai:
                continue
            ok = _same(ai[cid], hv)
            for bucket in (per_lang[lang], per_crit[cid]):
                bucket["total"] += 1
                bucket["correct"] += ok
            if not ok:
                disagreements.append({"externalId": a.conversation_id, "criterionId": cid,
                                      "values": [{"model": REFERENCE, "value": to_ui_value(hv)},
                                                 {"model": model, "value": to_ui_value(ai[cid])}]})
        crits = checklist.for_channel(channel).criteria
        if all(c.criterion_id in human for c in crits) and ev.ai_score is not None:
            try:
                ref = compute_score(human, crits)
            except (KeyError, ValueError):
                ref = None
            if ref is not None:
                maes.append(abs(ref - ev.ai_score))
    total = sum(b["total"] for b in per_crit.values())
    correct = sum(b["correct"] for b in per_crit.values())
    return {"model": model, "succeeded": succeeded, "attempted": len(anns), "failed": len(anns) - succeeded,
            "approved": len(anns), "criterionAccuracy": round(correct / total, 4) if total else None,
            "totalScoreMAE": round(mean(maes), 2) if maes else None,
            "averageDurationMs": round(mean(durations)) if durations else None,
            "perLanguage": dict(per_lang), "perCriterion": dict(per_crit)}
