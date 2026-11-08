"""Наши данные ↔ формат интерфейса (React-фронтенд в frontend/assets).

Отличия форматов:
  значения критериев   True/False/None/int      ↔ "yes"/"no"/"na"/число
  реплики              idx 1..N                 ↔ id "m1".."mN"
  тип критерия         binary/scale + флаг      ↔ "boolean"/"scale"/"flag"
  канал                chat/voice               ↔ "agent chat"/"voice call"
  ID чек-листа         id версии (int)          ↔ строка
Расчёт итога остаётся нашим (scoring/calculator.py): интерфейс получает готовую разбивку по критериям и секциям.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Appeal, Checklist, ChecklistVersion, ConversationRow, Evaluation
from app.schemas import CriterionConfig

CHANNEL_TO_UI = {"chat": "agent chat", "voice": "voice call", "messenger": "agent chat", "email": "agent chat"}
RESOLUTION_LABELS = {"resolved": "вопрос решён", "escalated": "передан специалистам", "unresolved": "не решён",
                     "client_left": "клиент ушёл до решения", "no_request": "запроса не было"}
_UI_ID = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


class UIError(ValueError):
    """Ошибка входных данных интерфейса → HTTP 400/422 с {"error": ...}."""


# --------------------------------------------------------------------------- значения и реплики
def to_ui_value(v: Any) -> Any:
    if v is None:
        return "na"
    if v is True:
        return "yes"
    if v is False:
        return "no"
    return v


def from_ui_value(v: Any) -> Any:
    if v in ("yes", True):
        return True
    if v in ("no", False):
        return False
    if v in ("na", None, ""):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer():
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
        return int(v)
    raise UIError(f"Недопустимое значение {v!r}")


def msg_id(idx: int) -> str:
    return f"m{idx}"


def turn_ids(ids: list[Any]) -> list[int]:
    out = []
    for x in ids or []:
        m = re.fullmatch(r"m?(\d+)", str(x).strip())
        if m:
            out.append(int(m.group(1)))
    return sorted(set(out))


# --------------------------------------------------------------------------- чек-листы
def criterion_description(c: dict[str, Any]) -> str:
    parts = []
    if c.get("yes_definition"):
        parts.append(f"Да: {c['yes_definition']}")
    if c.get("no_definition"):
        parts.append(f"Нет: {c['no_definition']}")
    if c.get("guidance"):
        parts.append(c["guidance"])
    return "\n".join(parts) or c.get("title", "")


def criterion_to_ui(c: dict[str, Any]) -> dict[str, Any]:
    flag = bool(c.get("is_critical_flag"))
    ctype = "flag" if flag else ("scale" if c.get("type") == "scale" else "boolean")
    return {
        "id": c["criterion_id"], "title": c["title"], "section": c.get("section") or "Без секции",
        "description": criterion_description(c), "type": ctype,
        "weight": 0 if flag else c.get("weight", 0),
        "allowNA": bool(c.get("na_allowed")) and not flag, "naRule": c.get("na_condition") or "",
        "scale": [{"value": s["value"], "label": s.get("description", "")} for s in (c.get("scale") or [])]
        if ctype == "scale" else [],
    }


def checklist_to_ui(session: Session, ver: ChecklistVersion) -> dict[str, Any]:
    cl = session.get(Checklist, ver.checklist_id)
    return {"id": str(ver.id), "version": ver.version, "name": cl.name if cl else "Чек-лист",
            "description": (cl.description if cl else "") or ver.note or "",
            "createdAt": ver.created_at.isoformat(), "criteria": [criterion_to_ui(c) for c in ver.criteria]}


def checklists_ui(session: Session) -> list[dict[str, Any]]:
    """Все версии всех чек-листов, новые первыми ([0] — «Последняя», выбирается по умолчанию)."""
    vers = session.scalars(select(ChecklistVersion).order_by(ChecklistVersion.created_at.desc(),
                                                             ChecklistVersion.id.desc())).all()
    return [checklist_to_ui(session, v) for v in vers]


def criteria_from_ui(items: list[dict[str, Any]], base: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Критерии из редактора → наши. Если описание не менялось, сохраняются исходные подробные поля
    (определения «Да/Нет», пояснения, каналы): редактор показывает их одним текстом."""
    if not isinstance(items, list) or not 1 <= len(items) <= 40:
        raise UIError("В чек-листе должно быть от 1 до 40 критериев")
    by_id = {c["criterion_id"]: c for c in base}
    out, seen = [], set()
    for it in items:
        cid_raw = str(it.get("id") or "")
        if not _UI_ID.match(cid_raw):
            raise UIError(f"Некорректный код критерия {cid_raw!r}")
        cid = cid_raw.lower().replace("-", "_")
        if len(cid) < 2 or not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", cid):
            raise UIError(f"Код критерия {cid_raw!r}: латиница, цифры и «_», от 2 до 41 символа")
        if cid in seen:
            raise UIError(f"Повторяется код критерия {cid}")
        seen.add(cid)
        title = str(it.get("title") or "").strip()
        description = str(it.get("description") or "").strip()
        if not title:
            raise UIError(f"У критерия {cid} нет названия")
        if not description:
            raise UIError(f"Опишите критерий «{title}»: модель оценивает по этому описанию")
        ctype = it.get("type")
        if ctype not in ("boolean", "scale", "flag"):
            raise UIError(f"Тип критерия «{title}»: boolean | scale | flag")
        flag = ctype == "flag"
        allow_na = bool(it.get("allowNA")) and not flag
        na_rule = str(it.get("naRule") or "").strip()
        if allow_na and not na_rule:
            raise UIError(f"У критерия «{title}» разрешено N/A — опишите, когда он неприменим")
        try:
            weight = 0 if flag else float(it.get("weight") or 0)
        except (TypeError, ValueError) as exc:
            raise UIError(f"Вес критерия «{title}» — число") from exc
        if not flag and not 0 < weight <= 1000:
            raise UIError(f"Вес критерия «{title}» должен быть больше 0 и не больше 1000")
        scale = None
        if ctype == "scale":
            steps = it.get("scale") or []
            values = [s.get("value") for s in steps]
            if len(steps) < 2 or len(steps) > 30 or 0 not in values or not any(isinstance(v, (int, float)) and v > 0
                                                                                for v in values):
                raise UIError(f"Шкала критерия «{title}»: от 2 до 30 ступеней, есть 0 и положительное значение")
            scale = [{"value": int(s["value"]), "description": str(s.get("label") or "")} for s in steps]
        old = by_id.get(cid)
        same_text = old is not None and criterion_description(old) == description
        crit = {
            "criterion_id": cid, "title": title, "section": str(it.get("section") or "Без секции").strip() or "Без секции",
            "weight": weight, "type": "scale" if ctype == "scale" else "binary", "scale": scale,
            "na_allowed": allow_na, "na_condition": na_rule if allow_na else "",
            "yes_definition": old.get("yes_definition", "") if same_text else "",
            "no_definition": old.get("no_definition", "") if same_text else "",
            "guidance": old.get("guidance", "") if same_text else description,
            "is_critical_flag": flag,
            "channels": (old or {}).get("channels") or ["chat", "voice", "email", "messenger"],
        }
        out.append(CriterionConfig(**crit).model_dump())   # та же проверка, что и для остальных чек-листов
    return out


# --------------------------------------------------------------------------- диалоги
def subject_of(row: ConversationRow) -> str:
    turns = (row.normalized or {}).get("turns") or []
    text = next((t.get("text", "") for t in turns if t.get("speaker") == "client"), "") or \
        next((t.get("text", "") for t in turns), "")
    text = " ".join(text.split())
    return text[:117] + "…" if len(text) > 120 else text


def latest_ui(ev: Evaluation | None) -> dict[str, Any] | None:
    if ev is None or ev.status != "ok":
        return None
    return {"total": ev.final_score, "grossErrors": bool(ev.critical_flag), "needsReview": bool(ev.needs_review),
            "checklistId": str(ev.checklist_version_id)}


def conversation_item(row: ConversationRow, ev: Evaluation | None) -> dict[str, Any]:
    return {"id": row.conversation_id, "externalId": row.conversation_id, "groupId": row.dataset,
            "subject": subject_of(row), "messageCount": row.turns_count,
            "channel": CHANNEL_TO_UI.get(row.channel, "agent chat"), "language": row.language,
            "latestEvaluation": latest_ui(ev)}


def messages_ui(row: ConversationRow) -> list[dict[str, Any]]:
    out = []
    for t in (row.normalized or {}).get("turns") or []:
        offset = t.get("audio_offset_sec")
        if offset is None:
            offset = t.get("offset_sec")
        out.append({"id": msg_id(t["idx"]), "speaker": t["speaker"], "text": t.get("text", ""),
                    "timestamp": t.get("ts_raw") or "", "offsetSeconds": float(offset or 0)})
    return out


def conversation_warnings(row: ConversationRow) -> list[str]:
    w = []
    if row.channel == "voice":
        w.append("Автоматическая расшифровка звонка: возможны ошибки распознавания и неточный порядок реплик.")
    if not any(t.get("speaker") == "operator" for t in (row.normalized or {}).get("turns") or []):
        w.append("В диалоге нет реплик оператора.")
    return w


# --------------------------------------------------------------------------- оценки
def appeal_status(a: Appeal) -> str:
    if a.status == "open":
        return "open"
    return "changed" if a.action == "change" else "confirmed"


def score_rows(ev: Evaluation) -> tuple[list[dict[str, Any]], float]:
    rows, applicable = [], 0.0
    for c in ev.criteria or []:
        flag = bool(c.get("is_critical_flag"))
        value = c.get("value")
        weight = 0 if flag else float(c.get("weight") or 0)
        fraction = None
        if not flag and value is not None and c.get("max_points"):
            fraction = (c.get("points") or 0) / c["max_points"]
            applicable += weight
        eff = float(c.get("effective_weight") or 0) if fraction is not None else 0.0
        review = c.get("review") or {}
        reasons = [r for r in c.get("review_reasons") or [] if r]
        needs = bool(c.get("needs_review"))
        rows.append({
            "criterionId": c["criterion_id"], "title": c.get("title", ""), "section": c.get("section", ""),
            "value": to_ui_value(value),
            "rationale": review.get("rationale") or c.get("rationale") or "",
            "evidence": [msg_id(t) for t in (review.get("evidence_turns") or c.get("evidence_turns") or [])],
            "checkedRange": None, "needsReview": needs,
            "reviewReason": ("; ".join(reasons) or "Требует проверки человеком") if needs else None,
            "weight": weight, "fraction": fraction, "effectiveWeight": round(eff, 4),
            "points": round(weight * fraction, 4) if fraction is not None else None,
            "weightedPoints": round(eff * fraction, 4) if fraction is not None else None,
            "quote": c.get("quote") or "", "evidenceStatus": c.get("evidence_status"),
        })
    return rows, applicable


def score_breakdown(ev: Evaluation) -> dict[str, Any]:
    rows, applicable = score_rows(ev)
    sections = []
    for s in ev.sections or []:
        if s.get("is_flag_section"):
            continue
        in_sec = [r for r in rows if r["criterionId"] in (s.get("criteria") or [])]
        sections.append({"name": s["section"], "score": s.get("score"),
                         "contribution": round(sum(r["weightedPoints"] or 0 for r in in_sec), 2),
                         "maxContribution": round(sum(r["effectiveWeight"] for r in in_sec if r["fraction"] is not None), 2)})
    return {"total": ev.final_score, "applicableWeight": applicable, "grossErrors": bool(ev.critical_flag),
            "sections": sections, "rows": rows}


def evaluation_summary(ev: Evaluation) -> str:
    parts = []
    r = ev.reason or {}
    if r.get("title"):
        parts.append(f"Причина обращения: {r['title']} ({RESOLUTION_LABELS.get(r.get('resolution'), r.get('resolution', ''))})")
        if r.get("secondary"):
            parts.append("Также: " + ", ".join(r["secondary"]))
        if r.get("request_summary"):
            parts.append(f"Запрос: {r['request_summary']}")
    masking = (ev.audit or {}).get("masking") or {}
    if masking.get("replacements"):
        from app.brain.pii import LABELS

        parts.append("Персональные данные скрыты перед отправкой в модель: "
                     + ", ".join(f"{LABELS.get(k, k).split(' (')[0]} — {v}" for k, v in masking["replacements"].items()))
    if ev.needs_review:
        n = sum(1 for c in ev.criteria or [] if c.get("needs_review"))
        parts.append(f"На проверку человеку: {n} крит.")
    return ". ".join(parts)


def evaluation_ui(session: Session, ev: Evaluation) -> dict[str, Any]:
    audit = ev.audit or {}
    judge = (audit.get("stages") or {}).get("judge") or {}
    usage = judge.get("usage") or {}
    ver = session.get(ChecklistVersion, ev.checklist_version_id)
    appeals = session.scalars(select(Appeal).where(Appeal.evaluation_id == ev.id).order_by(Appeal.id)).all()
    return {
        "id": str(ev.id), "createdAt": ev.created_at.isoformat(), "model": ev.model_name or ev.model or "",
        "provider": ev.provider or "anthropic",
        "promptVersion": (audit.get("prompt_versions") or {}).get("judge", ""),
        "reasoningEffort": (judge.get("meta") or {}).get("effort") or "",
        "inputHash": ev.cache_key or "", "durationMs": int(ev.duration_ms or 0),
        "summary": evaluation_summary(ev),
        "usage": {"estimatedUsd": ev.cost_usd if audit.get("cost_known", True) else None,
                  "inputTokens": int(usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                                     + usage.get("cache_creation_input_tokens", 0)),
                  "outputTokens": int(usage.get("output_tokens", 0)), "reasoningTokens": 0},
        "checklist": checklist_to_ui(session, ver) if ver else None,
        "score": {"total": ev.ai_score},
        "finalScore": score_breakdown(ev),
        "appeals": [{"id": str(a.id), "criterionId": a.criterion_id, "status": appeal_status(a), "comment": a.comment,
                     "resolution": a.reason} for a in appeals],
        "reason": ev.reason,
    }
