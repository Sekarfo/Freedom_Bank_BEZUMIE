"""Пакетная оценка: asyncio + таблица jobs, кэш результатов, журнал каждой оценки."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.brain.cloud_llm import make_cloud_client
from app.brain.pipeline import Pipeline, cache_key
from app.checklist.service import get_version, to_config
from app.config import get_settings
from app.reasons import service as reasons
from app.db import session_scope
from app.models import ConversationRow, Evaluation, Job
from app.schemas import EvaluationOptions
from app.store import to_conversation

_BACKGROUND: set[asyncio.Task] = set()
_clients: dict[str, Any] = {}


def _client_factory(provider: str):
    if provider not in _clients:
        _clients[provider] = make_cloud_client(provider)
    return _clients[provider]


def reset_clients() -> None:
    _clients.clear()


def default_pipeline() -> Pipeline:
    return Pipeline(_client_factory)


def resolve_options(provider: str | None = None, runtime: dict[str, Any] | None = None) -> EvaluationOptions:
    rt = runtime or {}
    return EvaluationOptions(provider=provider or rt.get("provider") or get_settings().default_provider)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def evaluate_conversation(pipeline: Pipeline, conversation_id: str, version_id: int,
                                opts: EvaluationOptions, force: bool, job_id: str | None,
                                taxonomy_version_id: int | None = None) -> tuple[int | None, bool, str]:
    """→ (evaluation_id, из кэша, статус)."""
    with session_scope() as s:
        row = s.get(ConversationRow, conversation_id)
        if row is None:
            return None, False, "error"
        conv = to_conversation(row)
        ver = get_version(s, version_id)
        checklist = to_config(ver)
        tax_ver = reasons.get_version(s, taxonomy_version_id)
        tax_ver_id, taxonomy = tax_ver.id, reasons.to_taxonomy(tax_ver)
        model = get_settings().model_for(opts.provider)
        key = cache_key(conv, checklist.for_channel(conv.channel), opts.provider, model, taxonomy)
        if not force:
            candidates = s.scalars(select(Evaluation).where(Evaluation.cache_key == key, Evaluation.status == "ok",
                                                            Evaluation.checklist_version_id == ver.id)
                                   .order_by(Evaluation.id.desc())).all()
            complete = [e for e in candidates if (e.audit or {}).get("complete", True)]
            cached = next((e for e in complete if e.conversation_id == conversation_id), None)
            if cached is not None:
                cached.last_requested_at = _now()
                return cached.id, True, cached.status
            if complete:
                # Тот же транскрипт под другим ID (например, PROD повторяет диалоги DEV): копируем готовую
                # оценку на этот conversation_id, иначе в results.json диалог выгрузится как «не оценён».
                ev = _copy_evaluation(complete[0], conversation_id, job_id, checklist)
                s.add(ev)
                s.flush()
                return ev.id, True, ev.status

    outcome = await pipeline.evaluate(conv, checklist, opts, taxonomy)
    reason = outcome.reason or {}

    with session_scope() as s:
        ev = Evaluation(
            conversation_id=conversation_id, checklist_version_id=version_id, job_id=job_id,
            provider=outcome.provider or opts.provider, model=outcome.model or model,
            model_name=outcome.model_name, status=outcome.status, error=outcome.error, cache_key=key,
            ai_score=outcome.score, final_score=outcome.score, critical_flag=outcome.critical_flag,
            needs_review=outcome.needs_review, criteria=outcome.criteria, sections=outcome.sections,
            signals=outcome.signals, audit=outcome.audit, cost_usd=outcome.cost_usd, duration_ms=outcome.duration_ms,
            created_at=_now(), last_requested_at=_now(),
            reason_taxonomy_version_id=tax_ver_id if outcome.reason else None, reason=outcome.reason,
            reason_code=reason.get("primary"), reason_topic=reason.get("topic"), resolution=reason.get("resolution"),
        )
        s.add(ev)
        s.flush()
        return ev.id, False, ev.status


def _copy_evaluation(src: Evaluation, conversation_id: str, job_id: str | None, checklist) -> Evaluation:
    """Копия исходной оценки ИИ (без решений по апелляциям чужого диалога) для другого conversation_id."""
    import copy

    from app.brain.pipeline import finalize_scores

    criteria = copy.deepcopy(src.criteria or [])
    for c in criteria:
        if "ai_value" in c:
            c["value"] = c.pop("ai_value")
        c.pop("review", None)
    score, flag, sections = finalize_scores(criteria, checklist) if src.status == "ok" else (None, None, [])
    return Evaluation(
        conversation_id=conversation_id, checklist_version_id=src.checklist_version_id, job_id=job_id,
        provider=src.provider, model=src.model, model_name=src.model_name, status=src.status, error=src.error,
        cache_key=src.cache_key, ai_score=score, final_score=score, critical_flag=flag,
        needs_review=any(c.get("needs_review") for c in criteria), criteria=criteria, sections=sections,
        signals=copy.deepcopy(src.signals or {}),
        audit=copy.deepcopy(src.audit or {}) | {"copied_from_evaluation": src.id,
                                                  "copied_from_conversation": src.conversation_id},
        cost_usd=0.0, duration_ms=0, created_at=_now(), last_requested_at=_now(),
        reason_taxonomy_version_id=src.reason_taxonomy_version_id, reason=copy.deepcopy(src.reason),
        reason_code=src.reason_code, reason_topic=src.reason_topic, resolution=src.resolution,
    )


def create_job(conversation_ids: list[str], version_id: int, opts: EvaluationOptions, force: bool,
               taxonomy_version_id: int | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    with session_scope() as s:
        # Версия справочника причин фиксируется при создании задачи: смена по ходу не перемешает версии.
        tax_id = reasons.get_version(s, taxonomy_version_id).id
        s.add(Job(id=job_id, status="queued", total=len(conversation_ids),
                  params={"conversation_ids": conversation_ids, "checklist_version_id": version_id,
                          "reason_taxonomy_version_id": tax_id, "options": opts.model_dump(), "force": force}))
    return job_id


async def run_job(job_id: str, pipeline: Pipeline | None = None, on_progress=None) -> dict[str, Any]:  # noqa: ANN001
    pipeline = pipeline or default_pipeline()
    with session_scope() as s:
        job = s.get(Job, job_id)
        params = dict(job.params)
        job.status = "running"
    opts = EvaluationOptions(**params["options"])
    ids: list[str] = params["conversation_ids"]
    version_id: int = params["checklist_version_id"]
    force: bool = params.get("force", False)
    tax_id: int | None = params.get("reason_taxonomy_version_id")
    gate = asyncio.Semaphore(max(2, get_settings().cloud_concurrency))
    lock = asyncio.Lock()

    async def one(cid: str) -> None:
        async with gate:
            try:
                ev_id, from_cache, status = await evaluate_conversation(pipeline, cid, version_id, opts, force, job_id, tax_id)
            except Exception as exc:  # noqa: BLE001  — запись не пропадает: фиксируем ошибку
                ev_id, from_cache, status = None, False, "error"
                with session_scope() as s:
                    ev = Evaluation(conversation_id=cid, checklist_version_id=version_id, job_id=job_id,
                                    provider=opts.provider, status="error", error=f"{type(exc).__name__}: {exc}",
                                    cache_key="", criteria=[], sections=[], audit={}, signals={})
                    s.add(ev)
                    s.flush()
                    ev_id = ev.id
        async with lock:
            with session_scope() as s:
                job = s.get(Job, job_id)
                job.done += 1
                if status != "ok":
                    job.failed += 1
                if from_cache:
                    job.from_cache += 1
                if ev_id is not None:
                    job.evaluation_ids = list(job.evaluation_ids or []) + [ev_id]
                    ev = s.get(Evaluation, ev_id)
                    if ev is not None and not from_cache:
                        job.cost_usd = round((job.cost_usd or 0) + (ev.cost_usd or 0), 6)
                done, total = job.done, job.total
            if on_progress:
                on_progress(done, total, cid, status, from_cache)

    try:
        await asyncio.gather(*(one(cid) for cid in ids))
        final_status = "done"
        err = None
    except Exception as exc:  # noqa: BLE001
        final_status, err = "failed", f"{type(exc).__name__}: {exc}"
    with session_scope() as s:
        job = s.get(Job, job_id)
        job.status = final_status
        job.error = err
        job.finished_at = _now()
        return job_dict(job)


def start_job_background(job_id: str) -> None:
    task = asyncio.create_task(run_job(job_id))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


def job_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id, "status": job.status, "total": job.total, "done": job.done, "failed": job.failed,
        "from_cache": job.from_cache, "evaluation_ids": job.evaluation_ids, "error": job.error,
        "cost_usd": job.cost_usd, "params": job.params, "created_at": job.created_at.isoformat(),
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


def mark_stale_jobs() -> None:
    """После перезапуска сервера незавершённые задачи помечаются как прерванные."""
    with session_scope() as s:
        for job in s.scalars(select(Job).where(Job.status.in_(("queued", "running")))):
            job.status = "failed"
            job.error = "Сервер был перезапущен во время выполнения"
            job.finished_at = _now()
