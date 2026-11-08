"""Справочник причин обращений: схема, текст для промпта, фрагмент JSON-схемы ответа, проверка ответа модели.

Справочник — данные, как чек-лист: новая тема или подтема попадает в классификацию без правки кода.
Код причины — «тема.подтема» (money.withdrawal). Тема other.other обязательна: туда попадает всё, что не подошло,
с коротким описанием — из таких описаний discovery.py предлагает новые подтемы.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_TAXONOMY_PATH = Path(__file__).with_name("default_taxonomy.json")
OTHER = "other.other"
_CODE = r"^[a-z][a-z0-9_]{1,40}$"

RESOLUTIONS: dict[str, str] = {
    "resolved": "вопрос решён: оператор дал ответ или решение, с которым клиент может действовать",
    "escalated": "передан дальше: заявка специалистам или в отдел, обещан ответ позже, клиента перевели",
    "unresolved": "не решён: оператор не помог или дал неверный/неполный ответ, клиент остался без решения",
    "client_left": "диалог оборвался до решения: клиент ушёл, связь прервалась, чат закрыт без ответа",
    "no_request": "запроса нет: приветствие без вопроса, ошибочное обращение, спам",
}


class ReasonSubtopic(BaseModel):
    code: str = Field(pattern=_CODE)
    title: str = Field(min_length=1)
    description: str = ""


class ReasonTopic(BaseModel):
    code: str = Field(pattern=_CODE)
    title: str = Field(min_length=1)
    description: str = ""
    subtopics: list[ReasonSubtopic]

    @field_validator("subtopics")
    @classmethod
    def _subs(cls, v: list[ReasonSubtopic]) -> list[ReasonSubtopic]:
        if not v:
            raise ValueError("у темы должна быть хотя бы одна подтема")
        codes = [s.code for s in v]
        dup = sorted({c for c in codes if codes.count(c) > 1})
        if dup:
            raise ValueError(f"повторяющиеся коды подтем: {', '.join(dup)}")
        return v


class ReasonTaxonomy(BaseModel):
    name: str = "Причины обращений"
    topics: list[ReasonTopic]

    @model_validator(mode="after")
    def _check(self) -> "ReasonTaxonomy":
        codes = [t.code for t in self.topics]
        dup = sorted({c for c in codes if codes.count(c) > 1})
        if dup:
            raise ValueError(f"повторяющиеся коды тем: {', '.join(dup)}")
        other = next((t for t in self.topics if t.code == "other"), None)
        if other is None:
            self.topics.append(ReasonTopic(code="other", title="Прочее", subtopics=[
                ReasonSubtopic(code="other", title="Не подходит ни одна подтема")]))
        elif not any(s.code == "other" for s in other.subtopics):
            other.subtopics.append(ReasonSubtopic(code="other", title="Не подходит ни одна подтема"))
        return self

    # ------------------------------------------------------------------ справочные методы
    def codes(self) -> list[str]:
        return [f"{t.code}.{s.code}" for t in self.topics for s in t.subtopics]

    def index(self) -> dict[str, tuple[ReasonTopic, ReasonSubtopic]]:
        return {f"{t.code}.{s.code}": (t, s) for t in self.topics for s in t.subtopics}

    def title_of(self, code: str) -> str:
        if "." in code:
            hit = self.index().get(code)
            return f"{hit[0].title} → {hit[1].title}" if hit else code
        t = next((t for t in self.topics if t.code == code), None)
        return t.title if t else code

    def topics_dump(self) -> list[dict[str, Any]]:
        return [t.model_dump() for t in self.topics]


def taxonomy_hash(topics: list[dict[str, Any]] | ReasonTaxonomy) -> str:
    if isinstance(topics, ReasonTaxonomy):
        topics = topics.topics_dump()
    return hashlib.sha256(json.dumps(topics, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def _default_raw() -> str:
    return DEFAULT_TAXONOMY_PATH.read_text(encoding="utf-8")


def load_default_taxonomy() -> tuple[ReasonTaxonomy, str]:
    data = json.loads(_default_raw())
    return ReasonTaxonomy(name=data["name"], topics=data["topics"]), data.get("description", "")


# --------------------------------------------------------------------------- промпт и схема
def render_taxonomy(tax: ReasonTaxonomy) -> str:
    lines = ["Выбери ровно одну основную причину обращения (`primary`) — код «тема.подтема» из списка. Основная причина — "
             "вопрос, ради которого клиент обратился (обычно первый содержательный запрос; если бот уже ответил "
             "на него и клиент пришёл к оператору с другим вопросом — этот другой вопрос). Остальные вопросы клиента — "
             "в `secondary` (без повторов основной, пустой список, если их нет). Если ни одна подтема не подходит — "
             f"`{OTHER}` и 3–10 слов сути в `other_description` на русском (без персональных данных); иначе "
             "`other_description` — пустая строка.", ""]
    for t in tax.topics:
        lines.append(f"- {t.title}" + (f" — {t.description}" if t.description else ""))
        for s in t.subtopics:
            lines.append(f"  - `{t.code}.{s.code}` — {s.title}" + (f": {s.description}" if s.description else ""))
    lines += ["", "`resolution` — чем закончилось обращение по основной причине (оценивай по тексту, а не по намерениям):"]
    lines += [f"- `{k}` — {v}" for k, v in RESOLUTIONS.items()]
    return "\n".join(lines)


def reason_schema(tax: ReasonTaxonomy) -> dict[str, Any]:
    codes = tax.codes()
    return {
        "type": "object",
        "properties": {
            "primary": {"type": "string", "enum": codes},
            "secondary": {"type": "array", "items": {"type": "string", "enum": codes}},
            "other_description": {"type": "string"},
            "resolution": {"type": "string", "enum": list(RESOLUTIONS)},
        },
        "required": ["primary", "secondary", "other_description", "resolution"],
        "additionalProperties": False,
    }


# --------------------------------------------------------------------------- проверка ответа
def normalize_reason(raw: Any, tax: ReasonTaxonomy) -> dict[str, Any]:
    """Ответ модели → проверенная причина. Причина не влияет на баллы, поэтому ошибка здесь не делает оценку
    status=error: неизвестный код заменяется на other.other, а проблема записывается в issues."""
    issues: list[str] = []
    data = raw if isinstance(raw, dict) else {}
    if not isinstance(raw, dict):
        issues.append("в ответе нет объекта reason")
    idx = tax.index()

    def norm_code(x: Any) -> str | None:
        s = str(x or "").strip().lower()
        if s in idx:
            return s
        # частая неточность: прислана только подтема, уникальная по справочнику
        matches = [c for c in idx if c.split(".", 1)[1] == s]
        return matches[0] if len(matches) == 1 else None

    primary = norm_code(data.get("primary"))
    if primary is None:
        if data.get("primary"):
            issues.append(f"неизвестный код причины {data.get('primary')!r}")
        primary = OTHER
    secondary: list[str] = []
    for x in data.get("secondary") or []:
        c = norm_code(x)
        if c is None:
            issues.append(f"неизвестный код доп. причины {x!r}")
        elif c != primary and c not in secondary:
            secondary.append(c)
    resolution = str(data.get("resolution") or "").strip().lower()
    if resolution not in RESOLUTIONS:
        if resolution:
            issues.append(f"неизвестный исход {resolution!r}")
        resolution = "no_request" if primary == "general.operator_request" else "unresolved"
    other = " ".join(str(data.get("other_description") or "").split())[:200] if primary == OTHER else ""
    topic, sub = idx[primary]
    return {
        "primary": primary, "topic": topic.code, "subtopic": sub.code,
        "title": f"{topic.title} → {sub.title}", "secondary": secondary,
        "other_description": other, "resolution": resolution, "issues": issues,
    }
