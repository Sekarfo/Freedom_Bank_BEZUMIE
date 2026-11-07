"""Оркестрация «мозга»: этап 0 (код) → этап 1 (локальная) → этап 2 (облако) → этап 3 (решение) → итог.

Режимы:  local  — только локальная модель + код;
         cloud  — облачная модель как первичный судья + код;
         hybrid — локальная оценка → облачная проверка (review) или слепая оценка + арбитр (blind) → код.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

from pydantic import BaseModel, Field

from app.brain.decision import decide
from app.brain.llm_common import LLMClient, parse_output, run_stage
from app.brain.preprocess import compute_signals
from app.brain.prompts import (PROMPT_VERSIONS, arbiter_user_prompt, review_user_prompt, system_prompt,
                               user_prompt)
from app.checklist.render import criteria_hash, output_schema
from app.config import get_settings
from app.schemas import ChecklistConfig, Conversation, EvaluationOptions, LLMCallInfo, StageResult
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
    local_model: str | None = None
    cloud_model: str | None = None


def model_name(mode: str, local_display: str | None, cloud_model: str | None) -> str:
    if mode == "local":
        return local_display if local_display and "OpenRouter" in local_display else f"{local_display} (local)"
    if mode == "cloud":
        return f"{cloud_model}"
    return f"{local_display} (local) + {cloud_model}"


def cache_key(conv: Conversation, checklist: ChecklistConfig, opts: EvaluationOptions,
              local_tag: str | None, cloud_model: str | None) -> str:
    s = get_settings()
    parts = {
        "conv": hashlib.sha256(json.dumps([t.model_dump() for t in conv.turns], ensure_ascii=False,
                                          sort_keys=True).encode()).hexdigest(),
        "channel": conv.channel, "language": conv.language,
        "checklist": criteria_hash(checklist),
        "mode": opts.mode,
        "local": local_tag if opts.mode != "cloud" else None,
        "local_think": s.local_think if opts.mode != "cloud" else None,
        "cloud": cloud_model if opts.mode != "local" else None,
        "effort": s.anthropic_effort if (opts.mode != "local" and opts.cloud_provider == "anthropic") else None,
        "strategy": opts.hybrid_strategy if opts.mode == "hybrid" else None,
        "prompts": PROMPT_VERSIONS,
    }
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
# (conversation_id, checklist_hash, model_tag, prompt_version, think) → сырой ответ локальной модели или None
LocalLookup = Callable[[str, str, str, str, bool], "str | None"]


class Pipeline:
    def __init__(self, local_factory: ClientFactory, cloud_factory: ClientFactory,
                 local_lookup: LocalLookup | None = None):
        self.local_factory = local_factory
        self.cloud_factory = cloud_factory
        self.local_lookup = local_lookup

    def _reuse_local(self, conv: Conversation, checklist: ChecklistConfig, client: LLMClient) -> StageResult | None:
        """Готовый валидный ответ локальной модели на тот же диалог, чек-лист, модель и промпт (этап 1 не повторяем)."""
        if self.local_lookup is None:
            return None
        raw = self.local_lookup(conv.conversation_id, criteria_hash(checklist), client.model,
                                PROMPT_VERSIONS["primary"], bool(get_settings().local_think))
        if not raw:
            return None
        verdicts, facts, invalid, fatal = parse_output(raw, checklist, "primary")
        if fatal or invalid or not verdicts:
            return None
        call = LLMCallInfo(provider=client.provider, model=client.model, prompt_version=PROMPT_VERSIONS["primary"],
                           stage="local", ok=True, attempts=0, raw_text=raw, raw_texts=[raw], cost_usd=0.0,
                           meta={"display_name": client.display_name, "reused": True,
                                 "note": "ответ локальной модели взят из предыдущей оценки"})
        return StageResult(verdicts=verdicts, facts=facts, call=call, valid=True)

    async def evaluate(self, conv: Conversation, checklist_full: ChecklistConfig, opts: EvaluationOptions,
                       reuse_local: bool = True) -> EvalOutcome:
        s = get_settings()
        t0 = time.perf_counter()
        checklist = checklist_full.for_channel(conv.channel)
        signals = compute_signals(conv)
        audit: dict[str, Any] = {
            "prompt_versions": PROMPT_VERSIONS,
            "checklist_hash": criteria_hash(checklist),
            "options": opts.model_dump(),
            "stages": {},
        }
        local: StageResult | None = None
        cloud: StageResult | None = None
        arbiter: StageResult | None = None
        cloud_role: str | None = None
        local_client = cloud_client = None

        t_pre = time.perf_counter()
        user = user_prompt(conv, signals)
        audit["stages"]["preprocess"] = {"duration_ms": int((time.perf_counter() - t_pre) * 1000)}

        if opts.mode in ("local", "hybrid"):
            local_client = self.local_factory(opts.local_model)
            local = self._reuse_local(conv, checklist, local_client) if reuse_local else None
            if local is None:
                local = await run_stage(local_client, stage="local", prompt_version=PROMPT_VERSIONS["primary"],
                                        system=system_prompt("primary", checklist), user=user,
                                        schema=output_schema(checklist, "primary"), checklist=checklist,
                                        kind="primary")
            audit["stages"]["local"] = local.call.model_dump() | {"facts": local.facts,
                                                                   "invalid": local.invalid_criteria}

        if opts.mode in ("cloud", "hybrid"):
            cloud_client = self.cloud_factory(opts.cloud_provider)
            if opts.mode == "cloud" or local is None or not local.valid:
                cloud_role = "primary"
                cloud = await run_stage(cloud_client, stage="cloud", prompt_version=PROMPT_VERSIONS["primary"],
                                        system=system_prompt("primary", checklist), user=user,
                                        schema=output_schema(checklist, "primary", compact=True), checklist=checklist,
                                        kind="primary")
            elif opts.hybrid_strategy == "review":
                cloud_role = "review"
                cloud = await run_stage(cloud_client, stage="cloud_review", prompt_version=PROMPT_VERSIONS["review"],
                                        system=system_prompt("review", checklist),
                                        user=review_user_prompt(conv, signals, local.verdicts, checklist),
                                        schema=output_schema(checklist, "review", compact=True), checklist=checklist, kind="review")
            else:
                cloud_role = "blind"
                cloud = await run_stage(cloud_client, stage="cloud_blind", prompt_version=PROMPT_VERSIONS["primary"],
                                        system=system_prompt("primary", checklist), user=user,
                                        schema=output_schema(checklist, "primary", compact=True), checklist=checklist,
                                        kind="primary")
                if cloud.valid:
                    disputed = [c.criterion_id for c in checklist.criteria
                                if c.criterion_id in local.verdicts and c.criterion_id in cloud.verdicts
                                and local.verdicts[c.criterion_id].value != cloud.verdicts[c.criterion_id].value]
                    audit["stages"]["blind_disputed"] = disputed
                    if disputed:
                        arbiter = await run_stage(
                            cloud_client, stage="arbiter", prompt_version=PROMPT_VERSIONS["arbiter"],
                            system=system_prompt("arbiter", checklist),
                            user=arbiter_user_prompt(conv, signals, disputed, local.verdicts, cloud.verdicts),
                            schema=output_schema(checklist, "arbiter", only=disputed, compact=True), checklist=checklist,
                            kind="arbiter", only=disputed)
                        audit["stages"]["arbiter"] = arbiter.call.model_dump() | {"invalid": arbiter.invalid_criteria}
            audit["stages"][cloud.call.stage] = cloud.call.model_dump() | {"facts": cloud.facts,
                                                                           "invalid": cloud.invalid_criteria,
                                                                           "role": cloud_role}

        t_dec = time.perf_counter()
        decision = decide(checklist, conv, signals, mode=opts.mode, cloud_role=cloud_role, local=local,
                          cloud=cloud, arbiter=arbiter, threshold=s.quote_match_threshold)
        audit["stages"]["decision"] = {"duration_ms": int((time.perf_counter() - t_dec) * 1000),
                                       "status": decision.status, "error": decision.error}

        # Полная оценка = все запущенные этапы ответили валидно. Неполные (облако упало и т.п.) не кэшируются.
        audit["complete"] = all(st.call.ok for st in (local, cloud, arbiter) if st is not None)
        costs = [st.call.cost_usd for st in (local, cloud, arbiter) if st is not None]
        cost = sum(c for c in costs if c is not None)
        audit["cost_known"] = all(c is not None for c in costs)
        local_display = local_client.display_name if local_client else None
        cloud_model = cloud_client.model if cloud_client else None
        out = EvalOutcome(
            status=decision.status, error=decision.error, signals=signals.model_dump(), audit=audit,
            cost_usd=round(cost, 6), model_name=model_name(opts.mode, local_display, cloud_model),
            local_model=local_client.model if local_client else None, cloud_model=cloud_model,
        )
        if decision.status == "ok":
            score, flag, sections = finalize_scores(decision.criteria, checklist)
            out.criteria = decision.criteria
            out.score, out.critical_flag, out.sections = score, flag, sections
            out.needs_review = any(c["needs_review"] for c in decision.criteria)
        # Время оценки = время моделей + код (ожидание очереди к локальной модели в пакете не считается).
        wall_ms = int((time.perf_counter() - t0) * 1000)
        model_ms = sum(st.call.duration_ms for st in (local, cloud, arbiter) if st is not None)
        queue_ms = sum(int(st.call.meta.get("wall_ms", st.call.duration_ms)) - st.call.duration_ms
                       for st in (local, cloud, arbiter) if st is not None)
        out.duration_ms = max(0, wall_ms - queue_ms) if model_ms else wall_ms
        audit["wall_ms"], audit["queue_ms"] = wall_ms, queue_ms
        audit["stages"]["scoring"] = {"score": out.score, "critical_flag": out.critical_flag}
        return out
