"""Оценка одного диалога: предобработка (код) → модель-судья (1 вызов) → проверка и решение (код) → расчёт (код).

Модель только классифицирует: по каждому критерию значение, номера реплик, цитата, обоснование, уверенность.
Цитаты проверяет код, баллы, перераспределение веса при N/A, округление и флаг грубой ошибки — тоже код.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

from pydantic import BaseModel, Field

from app.brain.decision import decide
from app.brain.llm_common import LLMClient, call_judge
from app.brain import pii
from app.brain.preprocess import compute_signals
from app.brain.prompts import PROMPT_VERSIONS, system_prompt, user_prompt
from app.checklist.render import criteria_hash, output_schema
from app.config import get_settings
from app.reasons.taxonomy import ReasonTaxonomy, normalize_reason, taxonomy_hash
from app.schemas import ChecklistConfig, Conversation, EvaluationOptions
from app.scoring.calculator import (compute_critical_flag, compute_score, compute_sections, criterion_points,
                                    redistributed_weights)


class EvalOutcome(BaseModel):
    status: str
    error: str | None = None
    criteria: list[dict[str, Any]] = Field(default_factory=list)
    sections: list[dict[str, Any]] = Field(default_factory=list)
    score: float | None = None
    critical_flag: bool | None = None
    needs_review: bool = False
    signals: dict[str, Any] = Field(default_factory=dict)
    audit: dict[str, Any] = Field(default_factory=dict)
    cost_usd: float = 0.0
    duration_ms: int = 0
    model_name: str = ""
    provider: str = ""
    model: str = ""
    reason: dict[str, Any] | None = None   # причина обращения и исход (None — справочник не задан)


def mask_for_model(conv: Conversation) -> pii.MaskResult | None:
    """Копия диалога для внешней модели с замаскированными персональными данными (None — маскирование выключено).
    pii.MaskingUnavailable — детектор не загрузился, диалог отправлять нельзя."""
    return pii.mask_for_model(conv, get_settings())


def cache_key(conv: Conversation, checklist: ChecklistConfig, provider: str, model: str,
              taxonomy: ReasonTaxonomy | None = None) -> str:
    """Одинаковые транскрипт, чек-лист, модель и промпты → та же оценка (повторно модель не вызывается)."""
    s = get_settings()
    try:
        masked = mask_for_model(conv)
    except pii.MaskingUnavailable:
        masked = None   # оценка всё равно завершится status=error, а ошибки не кэшируются
    parts = {
        "conv": hashlib.sha256(json.dumps([t.model_dump() for t in conv.turns], ensure_ascii=False,
                                          sort_keys=True).encode()).hexdigest(),
        "channel": conv.channel, "language": conv.language,
        "checklist": criteria_hash(checklist),
        "provider": provider, "model": model,
        "effort": s.anthropic_effort if provider == "anthropic" else None,
        "prompts": PROMPT_VERSIONS,
    }
    # Версия маскирования входит в ключ, только если она изменила текст для модели:
    # для диалогов без персональных данных промпт тот же, и прежний кэш остаётся в силе.
    if masked is not None and masked.total:
        parts["masking"] = pii.MASKING_VERSION
    if taxonomy is not None:
        parts["reasons"] = taxonomy_hash(taxonomy)
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def finalize_scores(criteria: list[dict[str, Any]], checklist: ChecklistConfig) -> tuple[float | None, bool, list[dict[str, Any]]]:
    """Пересчёт баллов по финальным значениям (используется и после апелляций)."""
    values = {c["criterion_id"]: c["value"] for c in criteria}
    cfg = checklist.by_id()
    crits = [cfg[c["criterion_id"]] for c in criteria if c["criterion_id"] in cfg]
    eff = redistributed_weights(values, crits)
    for c in criteria:
        cc = cfg.get(c["criterion_id"])
        if cc is None:
            continue
        c["points"] = criterion_points(cc, c["value"])
        c["max_points"] = None if cc.is_critical_flag else cc.weight
        c["effective_weight"] = eff.get(cc.criterion_id)
    score = compute_score(values, crits)
    flag = compute_critical_flag(values, crits)
    sections = compute_sections(values, crits)
    return score, flag, sections


ClientFactory = Callable[[str], LLMClient]


class Pipeline:
    def __init__(self, client_factory: ClientFactory):
        self.client_factory = client_factory

    async def evaluate(self, conv: Conversation, checklist_full: ChecklistConfig,
                       opts: EvaluationOptions, taxonomy: ReasonTaxonomy | None = None) -> EvalOutcome:
        s = get_settings()
        t0 = time.perf_counter()
        checklist = checklist_full.for_channel(conv.channel)
        # Эвристики считаются локально по исходному тексту, в модель уходит только замаскированный.
        signals = compute_signals(conv)
        try:
            masked = mask_for_model(conv)
        except pii.MaskingUnavailable as exc:
            # Fail-closed: без маскирования диалог во внешнюю модель не уходит.
            return EvalOutcome(status="error", error=f"Маскирование персональных данных недоступно: {exc}",
                               audit={"masking": {"enabled": True, "error": str(exc)}, "complete": False},
                               duration_ms=int((time.perf_counter() - t0) * 1000))
        llm_conv = conv
        if masked is not None:
            llm_conv = masked.conv
            signals.critical_pattern_hits = [masked.masker.mask(h) for h in signals.critical_pattern_hits]
        audit: dict[str, Any] = {
            "prompt_versions": PROMPT_VERSIONS,
            "checklist_hash": criteria_hash(checklist),
            "reason_taxonomy_hash": taxonomy_hash(taxonomy) if taxonomy is not None else None,
            "options": opts.model_dump(),
            "stages": {},
            "masking": masked.report() if masked is not None else {"enabled": False},
        }

        t_pre = time.perf_counter()
        user = user_prompt(llm_conv, signals, masked=bool(masked and masked.total))
        audit["stages"]["preprocess"] = {"duration_ms": int((time.perf_counter() - t_pre) * 1000)}

        client = self.client_factory(opts.provider)
        judge = await call_judge(client, prompt_version=PROMPT_VERSIONS["judge"], system=system_prompt(checklist, taxonomy),
                                 user=user, schema=output_schema(checklist, taxonomy), checklist=checklist)
        audit["stages"]["judge"] = judge.call.model_dump() | {"facts": judge.facts, "invalid": judge.invalid_criteria}

        t_dec = time.perf_counter()
        # Цитаты модели сверяются с тем же замаскированным текстом, который она видела.
        decision = decide(checklist, llm_conv, signals, judge, threshold=s.quote_match_threshold)
        audit["stages"]["decision"] = {"duration_ms": int((time.perf_counter() - t_dec) * 1000),
                                       "status": decision.status, "error": decision.error}

        # Неполные оценки (модель упала, часть критериев невалидна) не кэшируются.
        audit["complete"] = judge.call.ok
        audit["cost_known"] = judge.call.cost_usd is not None
        out = EvalOutcome(
            status=decision.status, error=decision.error, signals=signals.model_dump(), audit=audit,
            cost_usd=round(judge.call.cost_usd or 0.0, 6), model_name=client.display_name,
            provider=client.provider, model=client.model,
        )
        if decision.status == "ok":
            score, flag, sections = finalize_scores(decision.criteria, checklist)
            out.criteria = decision.criteria
            out.score, out.critical_flag, out.sections = score, flag, sections
            out.needs_review = any(c["needs_review"] for c in decision.criteria)
            if taxonomy is not None:
                out.reason = normalize_reason(judge.reason, taxonomy)
                out.reason["request_summary"] = str(judge.facts.get("client_request_summary") or "")[:300]
                audit["stages"]["reason"] = {"primary": out.reason["primary"], "issues": out.reason["issues"]}
        # Время оценки = время модели + код (ожидание в очереди параллельных запросов не считается).
        wall_ms = int((time.perf_counter() - t0) * 1000)
        queue_ms = max(0, int(judge.call.meta.get("wall_ms", judge.call.duration_ms)) - judge.call.duration_ms)
        out.duration_ms = max(0, wall_ms - queue_ms)
        audit["wall_ms"], audit["queue_ms"] = wall_ms, queue_ms
        audit["stages"]["scoring"] = {"score": out.score, "critical_flag": out.critical_flag}
        return out
