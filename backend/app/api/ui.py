"""API интерфейса (React-фронтенд в frontend/assets): /api/*.

Тонкий слой поверх той же логики, что и /api/v1 (конвейер оценки, апелляции, чек-листы, экспорт):
только перевод форматов (ui/mapping.py) и экраны, которых в v1 нет, — ручная разметка, сравнение ИИ с ней,
сдача results.json, импорт. Ошибки — {"error": "..."} (см. обработчики в main.py).
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.appeals.service import AppealError, create_appeal, decide_appeal
from app.brain import pii
from app.brain.cloud_llm import cloud_configured
from app.checklist import service as checklists
from app.config import CLAUDE_PRICES, REPO_ROOT, get_settings
from app.db import get_db
from app.export.results_json import build_results, evaluation_record, export_checklist, write_results
from app.export.validator import validate_results
from app.ingest.organizers_api import OrganizersAPIError, fetch_dataset, load_cached_dataset
from app.jobs import create_job, start_job_background
from app.models import Appeal, Checklist, ChecklistVersion, ConversationRow, Evaluation, Job
from app.schemas import EvaluationOptions
from app.settings_store import get_runtime_settings
from app.store import evaluation_dict, latest_evaluations_map, upsert_conversations
from app.ui import quality
from app.ui.mapping import (UIError, checklist_to_ui, checklists_ui, conversation_item, conversation_warnings,
                            criteria_from_ui, evaluation_ui, from_ui_value, messages_ui, turn_ids)

router = APIRouter(prefix="/api", tags=["ui"])


def _bad(exc: Exception, code: int = 422) -> HTTPException:
    return HTTPException(code, str(exc))


def _version(db: Session, checklist_id: Any) -> ChecklistVersion:
    try:
        ver = db.get(ChecklistVersion, int(checklist_id))
    except (TypeError, ValueError):
        ver = None
    if ver is None:
        raise HTTPException(404, f"Версия чек-листа {checklist_id} не найдена")
    return ver


# --------------------------------------------------------------------------- модель и состояние
def _models() -> list[str]:
    s = get_settings()
    names = [s.anthropic_model] + [m for m in CLAUDE_PRICES if m != s.anthropic_model and "haiku" not in m]
    if s.openai_model:
        names.append(s.openai_model)
    return names


def _provider_for(model: str | None) -> str:
    s = get_settings()
    if not model or model in CLAUDE_PRICES or model.startswith("claude"):
        return "anthropic"
    return "openai" if model == s.openai_model else "anthropic"


@router.get("/health")
def health(db: Session = Depends(get_db)):
    s = get_settings()
    rt = get_runtime_settings(db)
    provider = rt.get("provider") or s.default_provider
    out: dict[str, Any] = {"status": "ok", "version": __version__,
                           "aiConfigured": cloud_configured(provider, s),
                           "model": s.model_for(provider), "models": [{"name": m} for m in _models()],
                           "masking": pii.status(s)}
    # Блок setup интерфейс показывает как уведомление: здесь — только когда маскирование не готово
    # (оценки тогда завершаются ошибкой, данные наружу не уходят).
    if out["masking"]["enabled"] and not out["masking"].get("ner_ready"):
        out["setup"] = {"stage": "failed", "detail": "Маскирование персональных данных недоступно: "
                        + out["masking"].get("error", "") + ". Выполните pip install -r backend/requirements.txt."}
    return out


# --------------------------------------------------------------------------- диалоги
@router.get("/conversations")
def conversations(db: Session = Depends(get_db)):
    rows = list(db.scalars(select(ConversationRow).order_by(ConversationRow.dataset, ConversationRow.seq,
                                                            ConversationRow.conversation_id)))
    latest = latest_evaluations_map(db, [r.conversation_id for r in rows])
    ok = {}
    for e in db.scalars(select(Evaluation).where(Evaluation.status == "ok")
                        .order_by(Evaluation.last_requested_at.asc(), Evaluation.id.asc())):
        ok[e.conversation_id] = e          # последняя успешная оценка: ошибка перезапуска не прячет прежний итог
    return [conversation_item(r, ok.get(r.conversation_id) or latest.get(r.conversation_id)) for r in rows]


@router.get("/conversations/{conversation_id}")
def conversation(conversation_id: str, db: Session = Depends(get_db)):
    row = db.get(ConversationRow, conversation_id)
    if row is None:
        raise HTTPException(404, f"Диалог {conversation_id} не найден")
    evs = db.scalars(select(Evaluation).where(Evaluation.conversation_id == conversation_id, Evaluation.status == "ok")
                     .order_by(Evaluation.last_requested_at.desc(), Evaluation.id.desc())).all()
    return {"id": row.conversation_id, "externalId": row.conversation_id, "groupId": row.dataset,
            "channel": conversation_item(row, None)["channel"], "language": row.language,
            "subject": conversation_item(row, None)["subject"], "messageCount": row.turns_count,
            "importedAt": row.created_at.isoformat(),
            "audioUrl": f"/api/v1/conversations/{row.conversation_id}/audio" if row.audio_url else None,
            "warnings": conversation_warnings(row), "messages": messages_ui(row),
            "evaluations": [evaluation_ui(db, e) for e in evs]}


# --------------------------------------------------------------------------- импорт
def _import(db: Session, payload: Any, dataset: str) -> dict[str, Any]:
    try:
        res = upsert_conversations(db, payload, dataset)
    except ValueError as exc:
        raise _bad(exc, 400) from exc
    return {"imported": res["added"] + res["updated"], "skipped": res["unchanged"] + len(res["errors"]),
            "added": res["added"], "updated": res["updated"], "errors": res["errors"][:20], "groupId": dataset}


def _fetch_group(group_id: str) -> dict[str, Any]:
    try:
        return fetch_dataset(group_id)
    except OrganizersAPIError as exc:
        cached = load_cached_dataset(group_id)
        if cached is None:
            raise HTTPException(502, f"{exc}. Локальной копии в data/raw тоже нет.") from exc
        return cached


@router.post("/import")
def import_file(payload: Any = Body(...), db: Session = Depends(get_db)):
    group = "UPLOAD"
    if isinstance(payload, dict):
        group = str(payload.get("groupId") or payload.get("group_id") or (payload.get("data") or {}).get("group_id")
                    or "UPLOAD").strip().upper() or "UPLOAD"
    return _import(db, payload, group)


@router.post("/import/dev")
def import_dev(_: dict = Body(default={}), db: Session = Depends(get_db)):
    if get_settings().organizers_api_url or load_cached_dataset("DEV") is not None:
        return _import(db, _fetch_group("DEV"), "DEV")
    local = REPO_ROOT / "invoke.json"
    if local.exists():
        return _import(db, json.loads(local.read_text(encoding="utf-8")), "DEV")
    raise HTTPException(404, "DEV-набор не найден: задайте ORGANIZERS_API_URL в .env или положите invoke.json в корень")


@router.post("/import/remote")
def import_remote(body: dict = Body(...), db: Session = Depends(get_db)):
    group = str(body.get("groupId") or "DEV").strip()
    if not group:
        raise HTTPException(422, "Укажите groupId")
    return _import(db, _fetch_group(group), group.upper())


# --------------------------------------------------------------------------- чек-листы
@router.get("/checklists")
def list_checklists(db: Session = Depends(get_db)):
    return checklists_ui(db)


@router.post("/checklists")
def save_checklist(body: dict = Body(...), db: Session = Depends(get_db)):
    """Новая версия того чек-листа, из которого открыт редактор (id в теле — исходная версия)."""
    base = None
    try:
        base = db.get(ChecklistVersion, int(body.get("id")))
    except (TypeError, ValueError):
        pass
    try:
        criteria = criteria_from_ui(body.get("criteria") or [], base.criteria if base else [])
    except (UIError, ValueError) as exc:
        raise _bad(exc) from exc
    name = str(body.get("name") or "").strip() or "Чек-лист"
    note = "Изменено в интерфейсе"
    if base is not None:
        ver = checklists.create_version(db, base.checklist_id, criteria, note)
        cl = db.get(Checklist, base.checklist_id)
        if cl is not None:
            cl.name = name
            if body.get("description") is not None:
                cl.description = str(body.get("description"))
    else:
        ver = checklists.create_checklist(db, name, str(body.get("description") or ""), criteria, note)
    db.flush()
    return checklist_to_ui(db, ver)


# --------------------------------------------------------------------------- оценка и задачи
def _jobs_ui(db: Session, limit: int = 30) -> list[dict[str, Any]]:
    out = []
    for job in db.scalars(select(Job).order_by(Job.created_at.desc()).limit(limit)):
        params = job.params or {}
        done: dict[str, Evaluation] = {}
        for ev_id in job.evaluation_ids or []:
            ev = db.get(Evaluation, ev_id)
            if ev is not None:
                done[ev.conversation_id] = ev
        for cid in params.get("conversation_ids") or []:
            ev = done.get(cid)
            if ev is not None:
                status, error = ("done", None) if ev.status == "ok" else ("error", ev.error)
            elif job.status in ("failed",):
                status, error = "error", job.error or "Задача прервана"
            elif job.status == "running":
                status, error = "running", None
            elif job.status == "done":
                status, error = "error", "Оценка не сохранена"
            else:
                status, error = "queued", None
            out.append({"id": f"{job.id}:{cid}", "conversationId": cid,
                        "checklistId": str(params.get("checklist_version_id")), "status": status, "error": error,
                        "model": (params.get("options") or {}).get("model"), "createdAt": job.created_at.isoformat()})
    return out


@router.get("/jobs")
def jobs(db: Session = Depends(get_db)):
    return _jobs_ui(db)


@router.post("/evaluations")
async def start(body: dict = Body(...), db: Session = Depends(get_db)):
    ids = list(dict.fromkeys(str(i) for i in body.get("conversationIds") or []))
    if not ids:
        raise HTTPException(422, "Не выбраны диалоги для оценки")
    missing = [i for i in ids if db.get(ConversationRow, i) is None]
    if missing:
        raise HTTPException(404, f"Диалоги не найдены: {missing[:10]}")
    ver = _version(db, body.get("checklistId"))
    model = body.get("model") or None
    if model and model not in _models():
        raise HTTPException(422, f"Модель {model} не настроена; доступны: {', '.join(_models())}")
    opts = EvaluationOptions(provider=_provider_for(model), model=model)
    db.commit()
    job_id = create_job(ids, ver.id, opts, force=False)
    start_job_background(job_id)
    return [{"id": f"{job_id}:{cid}", "conversationId": cid, "checklistId": str(ver.id), "status": "queued",
             "error": None, "model": model} for cid in ids]


@router.get("/evaluations/{evaluation_id}/export")
def export_evaluation(evaluation_id: int, db: Session = Depends(get_db)):
    ev = db.get(Evaluation, evaluation_id)
    if ev is None:
        raise HTTPException(404, "Оценка не найдена")
    data = {"result": evaluation_record(ev, ev.conversation_id), "evaluation": evaluation_dict(db, ev)}
    return JSONResponse(data, headers={"Content-Disposition":
                                       f'attachment; filename="evaluation_{ev.conversation_id}_{ev.id}.json"'})


# --------------------------------------------------------------------------- апелляции
def _appeal_item(db: Session, a: Appeal) -> dict[str, Any]:
    ev = db.get(Evaluation, a.evaluation_id)
    crit = next((c for c in (ev.criteria if ev else []) if c["criterion_id"] == a.criterion_id), {})
    from app.ui.mapping import appeal_status

    return {"id": str(a.id), "conversationId": a.conversation_id, "evaluationId": str(a.evaluation_id),
            "status": appeal_status(a), "criterionId": a.criterion_id, "criterionTitle": crit.get("title", a.criterion_id),
            "comment": a.comment, "externalId": a.conversation_id, "createdAt": a.created_at.isoformat(),
            "resolution": a.reason, "scoreBefore": a.score_before, "scoreAfter": a.score_after}


@router.get("/appeals")
def appeals(db: Session = Depends(get_db)):
    return [_appeal_item(db, a) for a in db.scalars(select(Appeal).order_by(Appeal.created_at.desc(), Appeal.id.desc()))]


@router.post("/appeals")
def new_appeal(body: dict = Body(...), db: Session = Depends(get_db)):
    comment = str(body.get("comment") or "").strip()
    if len(comment) < 3:
        raise HTTPException(422, "Опишите, с чем вы не согласны (от 3 символов)")
    try:
        a = create_appeal(db, int(body.get("evaluationId")), str(body.get("criterionId") or ""), comment,
                          str(body.get("author") or "Оператор"))
    except (TypeError, ValueError) as exc:
        raise _bad(exc) from exc
    return _appeal_item(db, a)


@router.post("/appeals/{appeal_id}/resolve")
def resolve_appeal(appeal_id: int, body: dict = Body(...), db: Session = Depends(get_db)):
    resolution = str(body.get("resolution") or "").strip()
    if len(resolution) < 3:
        raise HTTPException(422, "Укажите причину решения (от 3 символов)")
    decision = body.get("decision")
    try:
        if decision:
            a = decide_appeal(db, appeal_id, "change", from_ui_value(decision.get("value")), resolution,
                              str(body.get("reviewer") or "Проверяющий ОКК"))
            # Обоснование и доказательства проверяющего — в слой review; исходные ИИ-поля остаются как были.
            ev = db.get(Evaluation, a.evaluation_id)
            criteria = [dict(c) for c in ev.criteria]
            for c in criteria:
                if c["criterion_id"] == a.criterion_id and c.get("review"):
                    c["review"] = dict(c["review"], rationale=str(decision.get("rationale") or "").strip() or resolution,
                                       evidence_turns=turn_ids(decision.get("evidence") or []) or c.get("evidence_turns"))
            ev.criteria = criteria
        else:
            a = decide_appeal(db, appeal_id, "confirm", None, resolution, str(body.get("reviewer") or "Проверяющий ОКК"))
    except (AppealError, UIError) as exc:
        raise _bad(exc) from exc
    db.flush()
    return _appeal_item(db, a)


# --------------------------------------------------------------------------- разметка и качество
@router.get("/annotations")
def annotations(db: Session = Depends(get_db)):
    return quality.list_annotations(db)


@router.post("/annotations")
def save_annotation(body: dict = Body(...), db: Session = Depends(get_db)):
    try:
        return quality.annotation_ui(quality.save_annotation(db, body))
    except UIError as exc:
        raise _bad(exc) from exc


@router.get("/experiments")
def experiments(db: Session = Depends(get_db)):
    return quality.experiments(db)


# --------------------------------------------------------------------------- сдача results.json
@router.get("/submission/config")
def submission_config(db: Session = Depends(get_db)):
    groups = sorted({d for (d,) in db.execute(select(ConversationRow.dataset).distinct())},
                    key=lambda g: (g != "DEV", g))
    return {"teamName": get_settings().team_name, "groups": [{"id": g} for g in groups]}


def _submission(db: Session, body: dict) -> tuple[dict, list[str], list[str], dict]:
    split = body.get("split")
    if split not in ("dev", "prod"):
        raise HTTPException(422, "split: dev | prod")
    group = str(body.get("groupId") or "")
    if not db.scalar(select(ConversationRow.conversation_id).where(ConversationRow.dataset == group).limit(1)):
        raise HTTPException(404, f"В группе {group!r} нет диалогов")
    ver = _version(db, body.get("checklistId"))
    data = build_results(db, group, ver.id)
    team = str(body.get("teamName") or "").strip()
    if team:
        data["team_name"] = team
    errors, warnings = validate_results(data, checklist=export_checklist(db, group, ver.id),
                                        expect=10 if split == "prod" else None)
    ids = [r["conversation_id"] for r in data["results"]]
    evs = latest_evaluations_map(db, ids, ver.id)
    stats = {"total": len(ids), "ok": sum(r["status"] == "ok" for r in data["results"]),
             "errors": sum(r["status"] != "ok" for r in data["results"]),
             "needsReview": sum(1 for e in evs.values() if e.status == "ok" and e.needs_review)}
    return data, errors, warnings, stats


@router.post("/submission/preview")
def submission_preview(body: dict = Body(...), db: Session = Depends(get_db)):
    data, errors, warnings, stats = _submission(db, body)
    return stats | {"file": data, "validationErrors": errors, "validationWarnings": warnings[:50]}


@router.post("/submission/save")
def submission_save(body: dict = Body(...), db: Session = Depends(get_db)):
    data, errors, _, stats = _submission(db, body)
    if errors:
        raise HTTPException(422, "results.json не прошёл проверку: " + "; ".join(errors[:5]))
    path = write_results(data, REPO_ROOT / "results" / body["split"] / "results.json")
    return {"path": str(path.relative_to(REPO_ROOT)).replace("\\", "/"), "ok": stats["ok"], "errors": stats["errors"]}
