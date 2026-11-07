"""Аналитика, экспорт, статус системы и настройки режима."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.analytics.service import summary
from app.brain.cloud_llm import cloud_configured
from app.brain.local_llm import local_status
from app.brain.prompts import PROMPT_VERSIONS
from app.checklist.service import get_version
from app.config import CLAUDE_PRICES, LOCAL_MODELS, get_settings
from app.db import get_db
from app.export.results_json import build_results, export_checklist
from app.export.validator import validate_results
from app.jobs import reset_clients
from app.schemas import SettingsUpdate
from app.settings_store import get_runtime_settings, update_runtime_settings

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/analytics/summary")
def analytics(dataset: str | None = None, channel: str | None = None, language: str | None = None,
              date_from: str | None = None, date_to: str | None = None, db: Session = Depends(get_db)):
    return summary(db, dataset, channel, language, date_from, date_to)


@router.get("/export/results")
def export_results(dataset: str = "DEV", checklist_version_id: int | None = None, ai_only: bool = False,
                   download: bool = False, db: Session = Depends(get_db)):
    data = build_results(db, dataset, checklist_version_id, use_final=not ai_only)
    errors, warnings = validate_results(data, checklist=export_checklist(db, dataset, checklist_version_id))
    headers = {"X-CQA-Validation-Errors": str(len(errors)), "X-CQA-Validation-Warnings": str(len(warnings))}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="results_{dataset.lower()}.json"'
    return JSONResponse(data, headers=headers)


@router.get("/export/validate")
def export_validate(dataset: str = "DEV", checklist_version_id: int | None = None, expect: int | None = None,
                    db: Session = Depends(get_db)):
    data = build_results(db, dataset, checklist_version_id)
    errors, warnings = validate_results(data, expect=expect,
                                        checklist=export_checklist(db, dataset, checklist_version_id))
    return {"records": len(data["results"]), "errors": errors, "warnings": warnings[:50],
            "warnings_total": len(warnings)}


@router.get("/system/status")
async def system_status(db: Session = Depends(get_db)):
    s = get_settings()
    local = await local_status(s)
    key, tag, display = s.resolve_local_model(get_runtime_settings(db).get("local_model"))
    local["selected_tag"] = tag
    local["selected_available"] = any(m == tag or m == f"{tag}:latest" for m in local["models"])
    return {
        "local": local,
        "cloud": {
            "anthropic": {"configured": cloud_configured("anthropic", s), "model": s.anthropic_model,
                          "price": CLAUDE_PRICES.get(s.anthropic_model), "effort": s.anthropic_effort},
            "openai": {"configured": cloud_configured("openai", s), "model": s.openai_model or None,
                       "price": s.price_for("openai", s.openai_model)},
        },
        "organizers_api_configured": bool(s.organizers_api_url),
        "local_models": [{"key": k, "tag": s.local_tag_map().get(k, v["tag"]), "display": v["display"]}
                         for k, v in LOCAL_MODELS.items()],
        "prompt_versions": PROMPT_VERSIONS,
        "team_name": s.team_name,
    }


@router.get("/settings")
def get_settings_api(db: Session = Depends(get_db)):
    rt = get_runtime_settings(db)
    try:
        ver = get_version(db, rt.get("checklist_version_id"))
        rt["checklist_version_id_effective"] = ver.id
    except KeyError:
        rt["checklist_version_id_effective"] = None
    return rt


@router.put("/settings")
def put_settings(body: SettingsUpdate, db: Session = Depends(get_db)):
    values = body.model_dump(exclude_unset=True)
    if "checklist_version_id" in values and values["checklist_version_id"] is not None:
        try:
            get_version(db, values["checklist_version_id"])
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
    rt = update_runtime_settings(db, values)
    reset_clients()
    return rt
