"""FastAPI-приложение: API + раздача frontend как статики."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import appeals, checklists, conversations, evaluations, system
from app.config import get_settings
from app.db import configure
from app.jobs import mark_stale_jobs

log = logging.getLogger("cqa")


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure(get_settings().database_url)
    mark_stale_jobs()
    yield


app = FastAPI(title="CQA — Conversation Quality Analytics", version=__version__, lifespan=lifespan)

# Интерфейс может открываться как веб-ресурс Dynamics 365 с другого домена.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["X-CQA-Validation-Errors", "X-CQA-Validation-Warnings"])


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    log.exception("Необработанная ошибка")
    return JSONResponse(status_code=500, content={"detail": f"Внутренняя ошибка сервера: {type(exc).__name__}: {exc}"})


@app.get("/api/health")
def health():
    return {"status": "ok", "version": __version__}


for module in (conversations, checklists, evaluations, appeals, system):
    app.include_router(module.router)

_frontend = Path(get_settings().frontend_dir)
if _frontend.exists():
    app.mount("/", StaticFiles(directory=str(_frontend), html=True), name="frontend")
