"""Чек-лист → текст для промпта и JSON-схемы ответа моделей.

Промпты собираются из конфигурации, поэтому новый критерий попадает в оценку без правки кода.
Схема ответа — объект с ключом на каждый criterion_id: модель не может пропустить критерий,
а допустимые значения зашиты в схему (enum / null только при na_allowed).
"""
from __future__ import annotations

import hashlib
import re
import json
from pathlib import Path
from typing import Any

from app.schemas import ChecklistConfig, CriterionConfig

DEFAULT_CHECKLIST_PATH = Path(__file__).with_name("default_checklist.json")
DEFAULT_CRITERIA_CODES = ("contact", "discovery", "identification", "consultation",
                          "empathy", "dialogue", "closure", "critical")


def load_default_checklist(path: Path | None = None) -> tuple[ChecklistConfig, str]:
    data = json.loads((path or DEFAULT_CHECKLIST_PATH).read_text(encoding="utf-8"))
    if "schema_version" in data:  # подробный формат команды (eval/default_checklist.json)
        return ChecklistConfig(name=data["title"], criteria=convert_rich_criteria(data)), data.get("description", "")
    return ChecklistConfig(name=data["name"], criteria=data["criteria"]), data.get("description", "")


# --------------------------------------------------------------------------- подробный формат → CriterionConfig
_DEV_ID_RE = re.compile(r"p\d-[a-z]{2}-\d{3}(?:\s*\(реплик[^)]*\))?")
_ALL_CHANNELS = ["chat", "voice", "email", "messenger"]


def _clean(text: str) -> str:
    """Убираем ссылки на DEV-диалоги (p1-ru-014 и т.п.) — модели они ничего не говорят; цитаты-примеры оставляем."""
    t = _DEV_ID_RE.sub("", text or "")
    t = re.sub(r"\s*(?:Вопрос о critical — )?(?:см\.|Решение —)\s*(?:также\s*)?D\d+\.?", "", t)  # ссылки на team_decisions
    t = re.sub(r"\(\s*\)?\s*(?=\d\))", "", t)                       # «null ( 3)» после удаления «(см. D2)»
    t = re.sub(r"\(\s*(?:[,;]\s*)*\)", "", t)                     # пустые скобки «(, , )»
    t = re.sub(r"(?:,\s*){2,}", ", ", t)                           # «, ,» после удаления ID
    t = re.sub(r"(Примеры?)\s*:\s*[,;]?\s*", lambda m: m.group(1) + ": ", t)
    t = re.sub(r"\s*Примеры?:\s*[,;]?\s*(?=[.|]|$)\.?", "", t)     # «Пример: .» без содержания
    t = re.sub(r"\s+([,.)])", lambda m: m.group(1), t)
    t = re.sub(r",\s*([.|])", lambda m: m.group(1), t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _rich_guidance(c: dict[str, Any]) -> str:
    parts: list[str] = []
    if c.get("decision_procedure"):
        parts.append("Порядок решения: " + " ".join(f"{i}) {_clean(s)}" for i, s in
                                                    enumerate(c["decision_procedure"], 1)))
    pa = c.get("protected_actions")
    if pa:
        parts.append("Защищённые действия: " + " ".join(_clean(s) for s in pa.get("items", [])))
        if pa.get("not_protected"):
            parts.append("НЕ защищённые: " + " ".join(_clean(s) for s in pa["not_protected"]))
    ve = (c.get("verification_evidence") or {}).get("guidance")
    if ve:
        parts.append("Подтверждение личности: " + " ".join(_clean(s) for s in ve))
    cs = (c.get("complaint_signals") or {}).get("items")
    if cs:
        parts.append("Сигналы недовольства/неудобства: " + " ".join(_clean(s) for s in cs))
    ew = c.get("explicit_wait")
    if ew:
        parts.append("Явное ожидание: " + _clean(ew.get("definition", ""))
                     + (" Предупреждение: " + ", ".join(ew.get("warning_examples", [])) + "." if ew.get("warning_examples") else ""))
    for vt in c.get("violation_types") or []:
        parts.append(f"{vt['title']}: {_clean(vt.get('guidance', ''))}")
    edges = [f"«{_clean(e['case'])}» → {_clean(e['resolution'])}" for e in c.get("edge_cases") or []]
    if edges:
        parts.append("Пограничные случаи: " + " | ".join(edges))
    if c.get("voice_notes"):
        parts.append("Звонки: " + _clean(c["voice_notes"]))
    if c.get("common_errors"):
        parts.append("Частые ошибки оценщика: " + " ".join(_clean(s) for s in c["common_errors"]))
    return "\n".join(p for p in parts if p)


def convert_rich_criteria(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Подробный чек-лист (official/definitions/edge_cases/...) → поля CriterionConfig для промпта и расчёта."""
    sections = {s["section_id"]: s["title"] for s in data.get("sections", [])}
    out = []
    for c in data["criteria"]:
        is_flag = c.get("type") == "flag"
        defs = c.get("definitions") or {}
        off = c.get("official") or {}
        out.append({
            "criterion_id": c["criterion_id"],
            "title": c["title"],
            "section": sections.get(c.get("section_id"), c.get("section_id") or "Без секции"),
            "weight": c.get("weight", 0),
            "type": "scale" if c.get("type") == "scale" else "binary",
            "scale": [{"value": s["value"], "description": s["official"]} for s in c["scale"]] if c.get("scale") else None,
            "na_allowed": bool(c.get("na_allowed")),
            "yes_definition": defs.get("true") or off.get("yes", ""),
            "no_definition": defs.get("false") or off.get("no", ""),
            "na_condition": (defs.get("null") or c.get("na_condition") or "") if c.get("na_allowed") else "",
            "guidance": _rich_guidance(c),
            "is_critical_flag": is_flag,
            "channels": _ALL_CHANNELS,
        })
    return out


def criteria_hash(criteria: list[dict[str, Any]] | ChecklistConfig) -> str:
    if isinstance(criteria, ChecklistConfig):
        criteria = [c.model_dump() for c in criteria.criteria]
    blob = json.dumps(criteria, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _fmt_value(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def render_criterion(c: CriterionConfig, n: int) -> str:
    lines = [f"### {n}. {c.criterion_id} — {c.title}"]
    lines.append(f"Секция: {c.section}. " + ("Флаг без баллов." if c.is_critical_flag else f"Вес: {c.weight:g}."))
    allowed = ", ".join(_fmt_value(v) for v in c.allowed_values())
    lines.append(f"Допустимые значения value: {allowed}.")
    if c.type == "binary":
        if c.yes_definition:
            lines.append(f"- true («Да»): {c.yes_definition}")
        if c.no_definition:
            lines.append(f"- false («Нет»): {c.no_definition}")
    else:
        lines.append("Шкала (выбери ровно одну ступень):")
        for s in c.scale or []:
            lines.append(f"- {s.value}: {s.description}")
    if c.na_allowed:
        lines.append(f"- null (N/A): {c.na_condition or 'критерий неприменим по условию.'}")
    else:
        lines.append("- N/A (null) для этого критерия ЗАПРЕЩЁН.")
    if c.guidance:
        lines.append(f"Пояснения: {c.guidance}")
    return "\n".join(lines)


def render_checklist(checklist: ChecklistConfig) -> str:
    return "\n\n".join(render_criterion(c, i) for i, c in enumerate(checklist.criteria, start=1))


# --------------------------------------------------------------------------- JSON-схемы
def value_schema(c: CriterionConfig) -> dict[str, Any]:
    if c.type == "binary":
        base: dict[str, Any] = {"type": "boolean"}
    else:
        base = {"type": "integer", "enum": [s.value for s in c.scale or []]}
    if c.na_allowed:
        return {"anyOf": [base, {"type": "null"}]}
    return base


def criterion_schema(c: CriterionConfig, kind: str) -> dict[str, Any]:
    props: dict[str, Any] = {}
    if kind == "review":
        props["decision"] = {"type": "string", "enum": ["confirm", "override"]}
    # Порядок полей важен для локальной модели: сначала доказательство и рассуждение, потом значение.
    props["evidence_turns"] = {"type": "array", "items": {"type": "integer"}}
    props["quote"] = {"type": "string"}
    props["rationale"] = {"type": "string"}
    if kind == "review":
        props["override_reason"] = {"type": "string"}
    props["value"] = value_schema(c)
    props["confidence"] = {"type": "string", "enum": ["high", "medium", "low"]}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


FACTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "client_request_turn": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "client_request_summary": {"type": "string"},
        "protected_action_requested": {"type": "boolean"},
        "account_data_disclosed_turn": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "verification_confirmed_turn": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "complaint_present": {"type": "boolean"},
    },
    "required": ["client_request_turn", "client_request_summary", "protected_action_requested",
                 "account_data_disclosed_turn", "verification_confirmed_turn", "complaint_present"],
    "additionalProperties": False,
}


def compact_criteria_schema(crits: list[CriterionConfig], kind: str) -> dict[str, Any]:
    """Массив критериев с одной общей схемой элемента — маленькая грамматика для облачных API
    (у Claude полная схема с отдельным объектом на критерий превышает лимит грамматики).
    Допустимые значения конкретного критерия проверяет код (llm_common.coerce_value) + один повтор."""
    props: dict[str, Any] = {"criterion_id": {"type": "string", "enum": [c.criterion_id for c in crits]}}
    if kind == "review":
        props["decision"] = {"type": "string", "enum": ["confirm", "override"]}
    props["evidence_turns"] = {"type": "array", "items": {"type": "integer"}}
    props["quote"] = {"type": "string"}
    props["rationale"] = {"type": "string"}
    if kind == "review":
        props["override_reason"] = {"type": "string"}
    props["value"] = {"anyOf": [{"type": "boolean"}, {"type": "integer"}, {"type": "null"}]}
    props["confidence"] = {"type": "string", "enum": ["high", "medium", "low"]}
    item = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    return {"type": "array", "items": item}


def output_schema(checklist: ChecklistConfig, kind: str, only: list[str] | None = None,
                  compact: bool = False) -> dict[str, Any]:
    """kind: primary (этап 1 / облако-судья), review (проверка этапа 1), arbiter (спорные критерии).

    compact=False — объект с ключом на каждый критерий и точными enum (для локальной модели: constrained
    decoding гарантирует все критерии и допустимые значения); compact=True — массив (для облачных API)."""
    crits = [c for c in checklist.criteria if only is None or c.criterion_id in only]
    if compact:
        criteria_obj = compact_criteria_schema(crits, "review" if kind == "review" else "primary")
    else:
        crit_props = {c.criterion_id: criterion_schema(c, "review" if kind == "review" else "primary") for c in crits}
        criteria_obj = {"type": "object", "properties": crit_props, "required": list(crit_props),
                        "additionalProperties": False}
    props: dict[str, Any] = {}
    if kind == "primary":
        props["facts"] = FACTS_SCHEMA
    props["criteria"] = criteria_obj
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
