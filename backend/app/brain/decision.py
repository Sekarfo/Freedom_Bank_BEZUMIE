"""Этап 3 — финальное решение по каждому критерию (код, фиксированные правила, раздел 6.6).

| Ситуация                                             | Значение  | agreement  | Уверенность | needs_review |
|------------------------------------------------------|-----------|------------|-------------|--------------|
| Облако подтвердило, доказательство проверено         | значение  | agree      | high        | нет          |
| Облако исправило, доказательство проверено           | облака    | override   | medium      | нет          |
| Облако исправило, доказательство НЕ проверено        | облака    | override   | low         | да           |
| Облако недоступно / ответ невалиден после повтора    | локальной | local_only | low         | да           |
| Обе модели дали невалидный ответ                     | —         | —          | —           | status=error |
| confidence=low у облака                              | облака    | как есть   | low         | да           |

Плюс особые правила: critical, «N/A против Нет», противоречие NLP-сигналам.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.brain.evidence import EvidenceCheck, verify_evidence
from app.schemas import CONFIDENCE_ORDER, ChecklistConfig, CriterionConfig, Conversation, Signals, StageResult, Verdict

SOURCE_LABELS = {
    "local": "локальная модель", "cloud": "облачная модель", "agree": "согласие моделей",
    "override": "исправлено облаком", "arbiter": "решение арбитра", "reviewer": "решение проверяющего",
}


class DecisionResult(BaseModel):
    status: str = "ok"
    error: str | None = None
    criteria: list[dict[str, Any]] = Field(default_factory=list)


def _lower(conf: str, steps: int = 1) -> str:
    i = max(0, CONFIDENCE_ORDER.index(conf) - steps)
    return CONFIDENCE_ORDER[i]


def _cap(conf: str, cap: str) -> str:
    return CONFIDENCE_ORDER[min(CONFIDENCE_ORDER.index(conf), CONFIDENCE_ORDER.index(cap))]


def _brief(v: Verdict | None, ev: EvidenceCheck | None = None) -> dict[str, Any] | None:
    if v is None:
        return None
    d = {"value": v.value, "rationale": v.rationale, "evidence_turns": v.evidence_turns, "quote": v.quote,
         "confidence": v.confidence}
    if v.decision:
        d["decision"] = v.decision
    if v.override_reason:
        d["override_reason"] = v.override_reason
    if ev is not None:
        d["evidence_status"] = ev.status
    return d


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
    """Явное противоречие NLP-сигнала финальному значению → понижаем уверенность."""
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


def _pick_evidence(primary: Verdict, secondary: Verdict | None, conv: Conversation, thr: float
                   ) -> tuple[Verdict, EvidenceCheck]:
    """Доказательство финального вердикта; если у основного не подтвердилось, а у второй модели с тем же
    значением подтвердилось — берём его."""
    ev = verify_evidence(primary.evidence_turns, primary.quote, conv, thr)
    if not ev.ok and secondary is not None and secondary.value == primary.value:
        ev2 = verify_evidence(secondary.evidence_turns, secondary.quote, conv, thr)
        if ev2.ok:
            return secondary, ev2
    return primary, ev


def decide(checklist: ChecklistConfig, conv: Conversation, sig: Signals, *, mode: str, cloud_role: str | None,
           local: StageResult | None, cloud: StageResult | None, arbiter: StageResult | None = None,
           threshold: float = 0.85) -> DecisionResult:
    """cloud_role: None (без облака) | primary (облако — единственный/основной судья) | review | blind."""
    lv_all = local.verdicts if (local and local.valid) else {}
    cv_all = cloud.verdicts if (cloud and cloud.valid) else {}
    av_all = arbiter.verdicts if (arbiter and arbiter.valid) else {}
    out: list[dict[str, Any]] = []
    missing: list[str] = []

    for c in checklist.criteria:
        cid = c.criterion_id
        lv, cv, av = lv_all.get(cid), cv_all.get(cid), av_all.get(cid)
        reasons: list[str] = []
        needs_review = False
        ev_owner: Verdict | None = None
        ev: EvidenceCheck | None = None

        if mode == "local" or (lv is not None and cv is None and cloud_role is None):
            if lv is None:
                missing.append(cid)
                continue
            final, agreement, source = lv, "single", "local"
            ev_owner, ev = _pick_evidence(lv, None, conv, threshold)
            conf = final.confidence
            if not ev.ok:
                conf, needs_review = "low", True
                reasons.append("Доказательство не подтверждено текстом диалога")
        elif mode == "cloud" or cloud_role == "primary":
            if cv is None:
                missing.append(cid)
                continue
            final, source = cv, "cloud"
            agreement = "single" if mode == "cloud" else "cloud_only"
            ev_owner, ev = _pick_evidence(cv, None, conv, threshold)
            conf = cv.confidence if mode == "cloud" else _cap(cv.confidence, "medium")
            if mode != "cloud":
                reasons.append("Локальная модель не дала валидный ответ — вердикт только облака")
            if not ev.ok:
                conf, needs_review = "low", True
                reasons.append("Доказательство не подтверждено текстом диалога")
        else:  # hybrid: review | blind
            if cv is None and lv is None:
                missing.append(cid)
                continue
            if cv is None:
                final, agreement, source = lv, "local_only", "local"
                ev_owner, ev = _pick_evidence(lv, None, conv, threshold)
                conf, needs_review = "low", True
                reasons.append("Облако недоступно или ответ невалиден — вердикт только локальной модели")
            elif lv is None:
                final, agreement, source = cv, "cloud_only", "cloud"
                ev_owner, ev = _pick_evidence(cv, None, conv, threshold)
                conf = _cap(cv.confidence, "medium")
                reasons.append("Локальная модель не дала валидный вердикт по критерию")
                if not ev.ok:
                    conf, needs_review = "low", True
                    reasons.append("Доказательство облака не подтверждено")
            elif cloud_role == "blind":
                if cv.value == lv.value:
                    final, agreement, source = cv, "agree", "agree"
                    ev_owner, ev = _pick_evidence(cv, lv, conv, threshold)
                    conf = "high" if ev.ok else "low"
                    if not ev.ok:
                        needs_review = True
                        reasons.append("Модели согласны, но доказательство не подтверждено")
                elif av is not None:
                    final, agreement, source = av, "arbiter", "arbiter"
                    ev_owner, ev = _pick_evidence(av, None, conv, threshold)
                    conf = "medium" if ev.ok else "low"
                    reasons.append("Модели разошлись — решение арбитра")
                    if not ev.ok:
                        needs_review = True
                        reasons.append("Доказательство арбитра не подтверждено")
                else:
                    final, agreement, source = cv, "override", "cloud"
                    ev_owner, ev = _pick_evidence(cv, None, conv, threshold)
                    conf, needs_review = "low", True
                    reasons.append("Модели разошлись, арбитр не дал вердикт — значение облака")
            else:  # review
                same = cv.value == lv.value
                if same:
                    final, agreement, source = cv, "agree", "agree"
                    ev_owner, ev = _pick_evidence(cv, lv, conv, threshold)
                    conf = "high" if ev.ok else "low"
                    if not ev.ok:
                        needs_review = True
                        reasons.append("Облако подтвердило, но доказательство не подтверждено текстом")
                else:
                    final, agreement, source = cv, "override", "override"
                    ev_owner, ev = _pick_evidence(cv, None, conv, threshold)
                    if ev.ok:
                        conf = "medium"
                    else:
                        conf, needs_review = "low", True
                        reasons.append("Облако исправило оценку, но его доказательство не подтверждено")
            # confidence=low у облака → на проверку
            if cv is not None and cv.confidence == "low" and final is cv:
                conf, needs_review = "low", True
                reasons.append("Облачная модель не уверена (confidence=low)")

        value = final.value

        # --- особые правила -------------------------------------------------------------
        if c.is_critical_flag:
            flagged = [name for name, v in (("локальная модель", lv), ("облачная модель", cv), ("арбитр", av))
                       if v is not None and v.value is True]
            if sig.critical_pattern_turns:
                flagged.append(f"NLP-сигнал {sig.critical_pattern_turns}")
            if value is False and flagged:
                needs_review = True
                reasons.append("Возможное нарушение: на него указали " + ", ".join(flagged))
        if lv is not None and cv is not None and {lv.value, cv.value} == {None, False} and c.na_allowed:
            needs_review = True
            reasons.append("Модели разошлись: N/A против «Нет»")
        conflict = _signal_conflict(c, value, sig)
        if conflict:
            conf = _lower(conf)
            reasons.append(conflict)
        if conf == "low":
            needs_review = True

        turns = ev.turns if ev else []
        evidence_status = ev.status if ev else "missing"
        if not turns:
            turns = default_checked_turns(c, conv, sig)
            evidence_status = "default" if evidence_status == "missing" else evidence_status
        rationale = final.rationale or "Обоснование не получено от модели."
        if agreement in ("override",) and cv is not None and cv.override_reason:
            rationale = f"{rationale} (Исправление облака: {cv.override_reason})"

        out.append({
            "criterion_id": cid,
            "title": c.title,
            "section": c.section,
            "weight": c.weight,
            "type": c.type,
            "na_allowed": c.na_allowed,
            "is_critical_flag": c.is_critical_flag,
            "allowed_values": c.allowed_values(),
            "ai_value": value,
            "value": value,
            "review": None,
            "rationale": rationale,
            "evidence_turns": turns,
            "quote": (ev_owner.quote if ev_owner else final.quote),
            "evidence_status": evidence_status,
            "evidence_note": ev.note if ev else "",
            "matched_turn": ev.matched_turn if ev else None,
            "confidence": conf,
            "agreement": agreement,
            "source": source,
            "source_label": SOURCE_LABELS.get(source, source),
            "needs_review": needs_review,
            "review_reasons": reasons,
            "local": _brief(lv),
            "cloud": _brief(cv),
            "arbiter": _brief(av),
            "appeal_status": None,
        })

    if missing:
        errs = []
        for name, st in (("локальная модель", local), ("облачная модель", cloud), ("арбитр", arbiter)):
            if st is not None and st.call.errors:
                errs.append(f"{name}: {st.call.errors[-1]}")
        msg = f"Нет валидного вердикта по критериям: {', '.join(missing)}."
        if errs:
            msg += " " + " | ".join(errs)
        return DecisionResult(status="error", error=msg, criteria=[])
    return DecisionResult(status="ok", criteria=out)
