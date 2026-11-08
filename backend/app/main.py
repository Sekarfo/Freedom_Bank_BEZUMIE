"""FastAPI-приложение: API + раздача frontend как статики."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__
from app.api import appeals, checklists, conversations, evaluations, reasons, system, ui
from app.brain import pii
from app.config import get_settings
from app.db import configure
from app.jobs import mark_stale_jobs

log = logging.getLogger("cqa")


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure(get_settings().database_url)
    mark_stale_jobs()
    # Загружаем NER маскирования заранее: первая оценка не ждёт загрузки, а проблема видна сразу в логе.
    st = pii.status(get_settings())
    if st["enabled"] and not st.get("ner_ready"):
        log.error("Маскирование персональных данных недоступно, оценки будут завершаться ошибкой: %s", st.get("error"))
    yield


app = FastAPI(title="CQA — Conversation Quality Analytics", version=__version__, lifespan=lifespan)

# Интерфейс может открываться как веб-ресурс Dynamics 365 с другого домена.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-CQA-Validation-Errors", "X-CQA-Validation-Warnings"])


# Ошибки — всегда JSON с полем error (его показывает интерфейс) и detail (как раньше в /api/v1).
@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    msg = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    if exc.status_code == 404 and msg == "Not Found":
        msg = "Не найдено"
    return JSONResponse(status_code=exc.status_code, content={"error": msg, "detail": exc.detail},
                        headers=getattr(exc, "headers", None))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    errs = exc.errors()
    first = errs[0] if errs else {}
    where = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    msg = f"Некорректный запрос: {where + ' — ' if where else ''}{first.get('msg', '')}".strip()
    return JSONResponse(status_code=422, content={"error": msg, "detail": jsonable_encoder(errs)})


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.exception("Необработанная ошибка")
    msg = f"Внутренняя ошибка сервера: {type(exc).__name__}: {exc}"
    return JSONResponse(status_code=500, content={"error": msg, "detail": msg})


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "version": __version__}


# /api — интерфейс (frontend/assets), /api/v1 — прежний API (скрипты, D365, тесты).
for module in (ui, conversations, checklists, evaluations, appeals, reasons, system):
    app.include_router(module.router)

_frontend = Path(get_settings().frontend_dir)
if _frontend.exists():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
