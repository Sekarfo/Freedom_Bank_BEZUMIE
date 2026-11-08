"""Pydantic-схемы: диалог, чек-лист, сигналы, вердикты модели, API."""
from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Speaker = Literal["client", "operator", "bot", "system"]
Channel = Literal["chat", "voice", "email", "messenger"]
Language = Literal["RU", "KZ", "EN"]
Confidence = Literal["high", "medium", "low"]
Value = Union[bool, int, None]

CONFIDENCE_ORDER: tuple[str, ...] = ("low", "medium", "high")


# --------------------------------------------------------------------------- диалог
class Turn(BaseModel):
    idx: int                          # 1..N по исходному массиву
    speaker: Speaker
    text: str
    ts_raw: str                       # исходная метка как пришла
    offset_sec: float | None = None   # секунды от начала диалога
    audio_offset_sec: float | None = None  # позиция в аудиозаписи (звонки, с учётом пауз записи)


class Conversation(BaseModel):
    conversation_id: str
    dataset: str = "upload"
    channel: Channel
    language: Language
    turns: list[Turn]
    audio_url: str | None = None
    is_speech: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def n_turns(self) -> int:
        return len(self.turns)

    def turn(self, idx: int) -> Turn | None:
        if 1 <= idx <= len(self.turns):
            return self.turns[idx - 1]
        return None


# --------------------------------------------------------------------------- чек-лист
class ScaleStep(BaseModel):
    value: int
    description: str = ""


class CriterionConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    criterion_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,40}$")
    title: str
    section: str = "Без секции"
    weight: float = Field(default=0, ge=0)
    type: Literal["binary", "scale"] = "binary"
    scale: list[ScaleStep] | None = None
    na_allowed: bool = False
    yes_definition: str = ""
    no_definition: str = ""
    na_condition: str = ""
    guidance: str = ""
    is_critical_flag: bool = False
    channels: list[str] = Field(default_factory=lambda: ["chat", "voice"])

    @model_validator(mode="after")
    def _check(self) -> "CriterionConfig":
        if self.type == "scale":
            if not self.scale or len(self.scale) < 2:
                raise ValueError(f"{self.criterion_id}: шкальный критерий требует минимум 2 ступени")
            values = [s.value for s in self.scale]
            if len(set(values)) != len(values):
                raise ValueError(f"{self.criterion_id}: ступени шкалы повторяются")
            if max(values) <= 0 or min(values) < 0:
                raise ValueError(f"{self.criterion_id}: ступени шкалы должны быть ≥ 0, верхняя > 0")
            self.scale = sorted(self.scale, key=lambda s: -s.value)
        else:
            self.scale = None
        if self.is_critical_flag:
            self.weight = 0
            self.type = "binary"
            self.na_allowed = False
        if not self.na_allowed:
            self.na_condition = self.na_condition or ""
        return self

    @property
    def max_scale(self) -> int:
        return max(s.value for s in self.scale) if self.scale else 1

    def allowed_values(self) -> list[Value]:
        vals: list[Value] = [True, False] if self.type == "binary" else [s.value for s in (self.scale or [])]
        if self.na_allowed:
            vals.append(None)
        return vals

    def is_allowed(self, value: Any) -> bool:
        if value is None:
            return self.na_allowed
        if self.type == "binary":
            return isinstance(value, bool)
        return isinstance(value, int) and not isinstance(value, bool) and value in {s.value for s in self.scale or []}


class ChecklistConfig(BaseModel):
    name: str = "Чек-лист"
    criteria: list[CriterionConfig]

    @field_validator("criteria")
    @classmethod
    def _unique(cls, v: list[CriterionConfig]) -> list[CriterionConfig]:
        if not v:
            raise ValueError("Чек-лист должен содержать хотя бы один критерий")
        ids = [c.criterion_id for c in v]
        dup = {i for i in ids if ids.count(i) > 1}
        if dup:
            raise ValueError(f"Повторяющиеся criterion_id: {', '.join(sorted(dup))}")
        return v

    def by_id(self) -> dict[str, CriterionConfig]:
        return {c.criterion_id: c for c in self.criteria}

    def for_channel(self, channel: str) -> "ChecklistConfig":
        crit = [c for c in self.criteria if not c.channels or channel in c.channels]
        return ChecklistConfig(name=self.name, criteria=crit or self.criteria)


# --------------------------------------------------------------------------- сигналы
class Signals(BaseModel):
    first_operator_idx: int | None = None
    last_operator_idx: int | None = None
    operator_turns: list[int] = Field(default_factory=list)
    client_lang: str | None = None
    operator_lang_first_turn: str | None = None
    greeting_turns: list[int] = Field(default_factory=list)
    intro_turns: list[int] = Field(default_factory=list)
    farewell_turns: list[int] = Field(default_factory=list)
    help_offer_turns: list[int] = Field(default_factory=list)
    critical_pattern_turns: list[int] = Field(default_factory=list)
    critical_pattern_hits: list[str] = Field(default_factory=list)
    verification_system_turns: list[int] = Field(default_factory=list)
    wait_warning_turns: list[int] = Field(default_factory=list)
    long_gaps: list[dict[str, Any]] = Field(default_factory=list)  # [{"after_idx", "reply_idx", "seconds"}]
    chat_ended_abruptly: bool = False
    abrupt_end_turns: list[int] = Field(default_factory=list)
    operator_closed_turns: list[int] = Field(default_factory=list)
    no_operator: bool = False


# --------------------------------------------------------------------------- вердикты моделей
class Verdict(BaseModel):
    criterion_id: str
    value: Value = None
    rationale: str = ""
    evidence_turns: list[int] = Field(default_factory=list)
    quote: str = ""
    confidence: Confidence = "medium"


class LLMCallInfo(BaseModel):
    provider: str
    model: str
    prompt_version: str
    stage: str
    ok: bool = False
    attempts: int = 0
    errors: list[str] = Field(default_factory=list)
    raw_text: str | None = None
    raw_texts: list[str] = Field(default_factory=list)
    usage: dict[str, int] = Field(default_factory=dict)
    cost_usd: float | None = None
    duration_ms: int = 0
    meta: dict[str, Any] = Field(default_factory=dict)


class StageResult(BaseModel):
    """Разобранный ответ модели-судьи."""
    verdicts: dict[str, Verdict] = Field(default_factory=dict)
    facts: dict[str, Any] = Field(default_factory=dict)
    reason: dict[str, Any] | None = None   # сырой блок reason (причина обращения), проверяется отдельно
    call: LLMCallInfo
    valid: bool = False             # ответ разобран и содержит хотя бы один валидный критерий
    invalid_criteria: dict[str, str] = Field(default_factory=dict)


# --------------------------------------------------------------------------- API
class EvaluationOptions(BaseModel):
    provider: Literal["anthropic", "openai"] = "anthropic"
    model: str | None = None   # None — модель провайдера из .env; иначе — выбранная для этой задачи


class EvaluationRequest(BaseModel):
    conversation_ids: list[str] = Field(default_factory=list)
    all_in_dataset: str | None = None
    only_unevaluated: bool = False
    checklist_version_id: int | None = None
    provider: Literal["anthropic", "openai"] | None = None
    force: bool = False


class FetchRequest(BaseModel):
    group_id: str
    dataset: str | None = None


class UploadRequest(BaseModel):
    dataset: str = "upload"
    payload: Any


class ChecklistCreate(BaseModel):
    name: str
    description: str = ""
    criteria: list[CriterionConfig]
    note: str = ""


class ChecklistVersionCreate(BaseModel):
    criteria: list[CriterionConfig]
    note: str = ""


class AppealCreate(BaseModel):
    evaluation_id: int
    criterion_id: str
    comment: str = Field(min_length=1)
    author: str = "Оператор"


class AppealDecision(BaseModel):
    action: Literal["confirm", "change"]
    new_value: Value = None
    reason: str = Field(min_length=1)
    reviewer: str = "Проверяющий ОКК"


class SettingsUpdate(BaseModel):
    provider: Literal["anthropic", "openai"] | None = None
    checklist_version_id: int | None = None
    reason_taxonomy_version_id: int | None = None


class ReasonTaxonomyCreate(BaseModel):
    topics: list[dict[str, Any]]
    note: str = ""
    name: str = ""
    activate: bool = False   # сразу сделать версией по умолчанию для новых оценок


class ReasonDiscoverRequest(BaseModel):
    dataset: str | None = None
    min_cluster: int = Field(default=3, ge=2, le=100)
    limit: int = Field(default=300, ge=1, le=2000)
    use_llm: bool = True
    provider: Literal["anthropic", "openai"] | None = None
