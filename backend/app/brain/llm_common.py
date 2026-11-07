"""Общий слой вызова LLM: протокол клиента, разбор и валидация JSON, один повтор при ошибке схемы."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.schemas import ChecklistConfig, CriterionConfig, LLMCallInfo, StageResult, Verdict


class LLMError(RuntimeError):
    """Модель недоступна или вернула неиспользуемый ответ (отказ, обрезка, сетевой сбой)."""


@dataclass
class LLMResponse:
    text: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float | None = 0.0
    duration_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


class LLMClient(Protocol):
    provider: str      # anthropic | openai | mock
    model: str         # фактический ID модели
    display_name: str  # для model.name в results.json

    async def complete_json(self, system: str, user: str, schema: dict[str, Any], schema_name: str) -> LLMResponse: ...


# --------------------------------------------------------------------------- разбор
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I | re.M)


def parse_json_text(text: str) -> dict[str, Any]:
    t = _FENCE.sub("", (text or "").strip()).strip()
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("ответ не содержит JSON-объекта")
        data = json.loads(t[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("корень ответа должен быть JSON-объектом")
    return data


_TRUE = {"true", "yes", "да", "иә", "1"}
_FALSE = {"false", "no", "нет", "жоқ", "0"}
_NULL = {"null", "none", "n/a", "na", "н/д", "нет данных", ""}


def coerce_value(c: CriterionConfig, raw: Any) -> tuple[Any, str | None]:
    """Приводит значение к допустимому набору критерия. → (значение, ошибка)."""
    v = raw
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _NULL:
            v = None
        elif c.type == "binary" and s in _TRUE:
            v = True
        elif c.type == "binary" and s in _FALSE:
            v = False
        elif re.fullmatch(r"-?\d+(?:\.0+)?", s):
            v = int(float(s))
        else:
            return None, f"значение {raw!r} не распознано"
    if c.type == "binary" and isinstance(v, int) and not isinstance(v, bool) and v in (0, 1):
        v = bool(v)
    if c.type == "scale" and isinstance(v, float) and v.is_integer():
        v = int(v)
    if not c.is_allowed(v):
        allowed = ", ".join("null" if a is None else str(a).lower() for a in c.allowed_values())
        if v is None:
            return None, f"N/A (null) для критерия {c.criterion_id} запрещён; допустимо: {allowed}"
        return None, f"значение {raw!r} недопустимо; допустимо: {allowed}"
    return v, None


def coerce_turns(raw: Any) -> list[int]:
    out: list[int] = []
    items = raw if isinstance(raw, list) else ([raw] if raw is not None else [])
    for x in items:
        if isinstance(x, bool):
            continue
        if isinstance(x, (int, float)) and float(x).is_integer():
            out.append(int(x))
        elif isinstance(x, str):
            out += [int(m) for m in re.findall(r"\d+", x)]
    return out


def parse_output(text: str, checklist: ChecklistConfig
                 ) -> tuple[dict[str, Verdict], dict[str, Any], dict[str, str], str | None]:
    """→ (вердикты, факты, ошибки по критериям, фатальная ошибка)."""
    try:
        data = parse_json_text(text)
    except (ValueError, json.JSONDecodeError) as exc:
        return {}, {}, {}, f"невалидный JSON: {exc}"
    crit = data.get("criteria")
    if isinstance(crit, list):
        crit = {str(i.get("criterion_id")): i for i in crit if isinstance(i, dict)}
    if not isinstance(crit, dict):
        return {}, {}, {}, "в ответе нет объекта criteria"

    verdicts: dict[str, Verdict] = {}
    invalid: dict[str, str] = {}
    for c in checklist.criteria:
        item = crit.get(c.criterion_id)
        if not isinstance(item, dict):
            invalid[c.criterion_id] = "критерий отсутствует в ответе"
            continue
        if "value" not in item:
            invalid[c.criterion_id] = "нет поля value"
            continue
        value, err = coerce_value(c, item.get("value"))
        if err:
            invalid[c.criterion_id] = err
            continue
        conf = str(item.get("confidence") or "medium").strip().lower()
        verdicts[c.criterion_id] = Verdict(
            criterion_id=c.criterion_id,
            value=value,
            rationale=str(item.get("rationale") or "").strip(),
            evidence_turns=coerce_turns(item.get("evidence_turns")),
            quote=str(item.get("quote") or "").strip(),
            confidence=conf if conf in ("high", "medium", "low") else "medium",
        )
    facts = data.get("facts") if isinstance(data.get("facts"), dict) else {}
    return verdicts, facts, invalid, None


def _retry_note(fatal: str | None, invalid: dict[str, str]) -> str:
    lines = ["", "", "# Ошибка предыдущего ответа",
             "Предыдущий ответ не прошёл проверку схемы. Исправь ошибки и верни JSON целиком заново."]
    if fatal:
        lines.append(f"- {fatal}")
    lines += [f"- {cid}: {msg}" for cid, msg in invalid.items()]
    return "\n".join(lines)


async def call_judge(client: LLMClient, *, prompt_version: str, system: str, user: str,
                     schema: dict[str, Any], checklist: ChecklistConfig) -> StageResult:
    """Вызов модели-судьи + валидация. При ошибке схемы — ровно один повтор с текстом ошибки."""
    info = LLMCallInfo(provider=client.provider, model=client.model, prompt_version=prompt_version, stage="judge",
                       meta={"display_name": client.display_name})
    verdicts: dict[str, Verdict] = {}
    facts: dict[str, Any] = {}
    invalid: dict[str, str] = {}
    usage_total: dict[str, int] = {}
    cost_total: float | None = 0.0
    model_ms = 0
    t0 = time.perf_counter()
    note = ""
    for attempt in (1, 2):
        info.attempts = attempt
        try:
            resp = await client.complete_json(system, user + note, schema, "cqa_evaluation")
        except LLMError as exc:
            info.errors.append(f"попытка {attempt}: {exc}")
            break
        except Exception as exc:  # noqa: BLE001  сетевые и прочие сбои SDK — это status:error, а не падение
            info.errors.append(f"попытка {attempt}: {type(exc).__name__}: {exc}")
            break
        info.raw_texts.append(resp.text)
        model_ms += int(resp.duration_ms or 0)
        for k, v in resp.usage.items():
            usage_total[k] = usage_total.get(k, 0) + int(v or 0)
        cost_total = None if (cost_total is None or resp.cost_usd is None) else cost_total + resp.cost_usd
        info.meta.update(resp.meta)
        v, f, inv, fatal = parse_output(resp.text, checklist)
        # Объединяем: валидные вердикты повтора заменяют прежние, прежние валидные сохраняются.
        verdicts.update(v)
        facts = f or facts
        invalid = {cid: msg for cid, msg in {**invalid, **inv}.items() if cid not in verdicts}
        if fatal:
            info.errors.append(f"попытка {attempt}: {fatal}")
        elif inv:
            info.errors.append(f"попытка {attempt}: " + "; ".join(f"{k}: {m}" for k, m in inv.items()))
        if not fatal and not invalid:
            break
        note = _retry_note(fatal, invalid)
    # duration_ms — чистое время модели (без ожидания в очереди параллельных запросов), wall_ms — полное.
    wall_ms = int((time.perf_counter() - t0) * 1000)
    info.duration_ms = model_ms or wall_ms
    info.meta["wall_ms"] = wall_ms
    info.usage = usage_total
    info.cost_usd = round(cost_total, 6) if cost_total is not None else None
    info.raw_text = info.raw_texts[-1] if info.raw_texts else None
    info.ok = bool(verdicts) and not invalid
    return StageResult(verdicts=verdicts, facts=facts, call=info, valid=bool(verdicts), invalid_criteria=invalid)


def cost_from_usage(price: dict[str, float] | None, usage: dict[str, int]) -> float | None:
    if price is None:
        return None
    return (usage.get("input_tokens", 0) * price["input"]
            + usage.get("output_tokens", 0) * price["output"]
            + usage.get("cache_read_input_tokens", 0) * price.get("cache_read", price["input"])
            + usage.get("cache_creation_input_tokens", 0) * price.get("cache_write", price["input"])) / 1_000_000
