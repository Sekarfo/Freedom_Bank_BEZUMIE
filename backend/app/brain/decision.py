"""Финальное решение по каждому критерию — код, фиксированные правила.

Модель-судья даёт по критерию значение, номера реплик, дословную цитату и уверенность.
Значение не меняется, код решает, можно ли ему доверять без человека:

| Ситуация                                               | Уверенность        | needs_review |
|--------------------------------------------------------|--------------------|--------------|
| Цитата найдена в указанной реплике или в другой        | как у модели       | если low     |
| (во втором случае номер реплики исправляет код)        |                    |              |
| Цитата не найдена в тексте диалога                     | low                | да           |
| Модель сама указала confidence = low                   | low                | да           |
| Эвристика предобработки противоречит значению          | на ступень ниже    | если low     |
| critical = «Нет», но словарь нашёл грубую фразу        | как есть           | да           |
| Нет валидного значения хотя бы по одному критерию      | —                  | status=error |
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.brain.evidence import verify_evidence
from app.schemas import CONFIDENCE_ORDER, ChecklistConfig, CriterionConfig, Conversation, Signals, StageResult


class DecisionResult(BaseModel):
    status: str = "ok"
    error: str | None = None
    criteria: list[dict[str, Any]] = Field(default_factory=list)


def _lower(conf: str, steps: int = 1) -> str:
    i = max(0, CONFIDENCE_ORDER.index(conf) - steps)
    return CONFIDENCE_ORDER[i]


def default_checked_turns(c: CriterionConfig, conv: Conversation, sig: Signals) -> list[int]:
    """Проверенный участок, если модель не дала корректных номеров реплик."""
    first_client = next((t.idx for t in conv.turns if t.speaker == "client"), 1)
    cid = c.criterion_id
    if cid == "contact" and sig.first_operator_idx:
        return [sig.first_operator_idx]
    if cid == "closure":
        turns = ([sig.last_operator_idx] if sig.last_operator_idx else []) + sig.operator_closed_turns + sig.abrupt_end_turns
        return sorted(set(turns)) or [conv.n_turns]
    if cid == "critical":
        return sig.operator_turns[:10] or [first_client]
    if cid == "identification":
        return sorted(set(sig.verification_system_turns + ([sig.first_operator_idx] if sig.first_operator_idx else []))) or [first_client]
    if cid in ("discovery", "consultation"):
        return [first_client] + ([sig.first_operator_idx] if sig.first_operator_idx else [])
    if sig.operator_turns:
        return sig.operator_turns[:4]
    return [first_client]


def _signal_conflict(c: CriterionConfig, value: Any, sig: Signals) -> str | None:
    """Явное противоречие эвристики финальному значению → понижаем уверенность."""
    cid, first = c.criterion_id, sig.first_operator_idx
    if cid == "contact" and first:
        greeted = first in sig.greeting_turns
        introduced = first in sig.intro_turns
        same_lang = sig.operator_lang_first_turn in (None, sig.client_lang)
        if value is False and greeted and introduced and same_lang:
            return "Сигналы: в первой реплике оператора найдены приветствие и представление на языке клиента"
        if value is True and not (greeted and introduced):
            return "Сигналы: в первой реплике оператора не найдено " + ("приветствие" if not greeted else "представление")
        if value is True and not same_lang:
            return "Сигналы: язык первой реплики оператора отличается от языка клиента"
    if cid == "identification" and value is False and sig.verification_system_turns:
        return f"Сигналы: есть системное подтверждение личности в {sig.verification_system_turns}"
    if cid == "closure":
        if value is True and not sig.farewell_turns:
            return "Сигналы: прощание в последних репликах оператора не найдено"
        if value is None and not sig.chat_ended_abruptly:
            return "Сигналы: признаков обрыва диалога не найдено"
    return None


def decide(checklist: ChecklistConfig, conv: Conversation, sig: Signals, judge: StageResult | None,
           threshold: float = 0.85) -> DecisionResult:
    verdicts = judge.verdicts if (judge and judge.valid) else {}
    out: list[dict[str, Any]] = []
    missing: list[str] = []

    for c in checklist.criteria:
        v = verdicts.get(c.criterion_id)
        if v is None:
            missing.append(c.criterion_id)
            continue
        reasons: list[str] = []
        needs_review = False
        conf = v.confidence

        ev = verify_evidence(v.evidence_turns, v.quote, conv, threshold)
        if not ev.ok:
            conf = "low"
            reasons.append("Доказательство не подтверждено текстом диалога")
        elif v.confidence == "low":
            reasons.append("Модель не уверена (confidence=low)")

        if c.is_critical_flag and v.value is False and sig.critical_pattern_turns:
            needs_review = True
            reasons.append(f"Возможное нарушение: словарь грубых фраз сработал в репликах {sig.critical_pattern_turns}")
        conflict = _signal_conflict(c, v.value, sig)
        if conflict:
            conf = _lower(conf)
            reasons.append(conflict)
        if conf == "low":
            needs_review = True

        turns, evidence_status = ev.turns, ev.status
        if not turns:
            turns = default_checked_turns(c, conv, sig)
            evidence_status = "default" if evidence_status == "missing" else evidence_status

        out.append({
            "criterion_id": c.criterion_id,
            "title": c.title,
            "section": c.section,
            "weight": c.weight,
            "type": c.type,
            "na_allowed": c.na_allowed,
            "is_critical_flag": c.is_critical_flag,
            "allowed_values": c.allowed_values(),
            "ai_value": v.value,
            "value": v.value,
            "review": None,
            "rationale": v.rationale or "Обоснование не получено от модели.",
            "evidence_turns": turns,
            "quote": v.quote,
            "evidence_status": evidence_status,
            "evidence_note": ev.note,
            "matched_turn": ev.matched_turn,
            "confidence": conf,
            "needs_review": needs_review,
            "review_reasons": reasons,
            "appeal_status": None,
        })

    if missing:
        msg = f"Нет валидного вердикта по критериям: {', '.join(missing)}."
        if judge is not None and judge.call.errors:
            msg += f" {judge.call.errors[-1]}"
        return DecisionResult(status="error", error=msg, criteria=[])
    return DecisionResult(status="ok", criteria=out)
