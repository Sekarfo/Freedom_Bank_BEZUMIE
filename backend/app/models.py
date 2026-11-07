"""Таблицы SQLAlchemy."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConversationRow(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(50), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)  # порядок в исходном наборе
    channel: Mapped[str] = mapped_column(String(20), index=True)
    language: Mapped[str] = mapped_column(String(5), index=True)
    audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    turns_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64))
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)
    normalized: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Checklist(Base):
    __tablename__ = "checklists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChecklistVersion(Base):
    __tablename__ = "checklist_versions"
    __table_args__ = (UniqueConstraint("checklist_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    checklist_id: Mapped[int] = mapped_column(ForeignKey("checklists.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text, default="")
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    criteria_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Evaluation(Base):
    __tablename__ = "evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.conversation_id"), index=True)
    checklist_version_id: Mapped[int] = mapped_column(ForeignKey("checklist_versions.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Имена столбцов в БД сохранены прежними (cloud_*), чтобы старые базы открывались без миграции данных.
    provider: Mapped[str | None] = mapped_column("cloud_provider", String(20), nullable=True)
    model: Mapped[str | None] = mapped_column("cloud_model", String(100), nullable=True)
    model_name: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(10), index=True)  # ok | error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cache_key: Mapped[str] = mapped_column(String(64), index=True)
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)      # итог исходной оценки ИИ
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)   # после апелляций
    critical_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    audit: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Appeal(Base):
    __tablename__ = "appeals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluations.id"), index=True)
    conversation_id: Mapped[str] = mapped_column(String(200), index=True)
    criterion_id: Mapped[str] = mapped_column(String(50))
    comment: Mapped[str] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)  # open | resolved
    disputed_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    score_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    action: Mapped[str | None] = mapped_column(String(10), nullable=True)  # confirm | change
    new_value: Mapped[Any] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(100), nullable=True)
    score_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default="queued")  # queued | running | done | failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    from_cache: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evaluation_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SettingRow(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
