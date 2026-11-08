"""Поиск новых подтем: описания из «Прочего» группируются в кластеры, крупные кластеры предлагаются как подтемы.

Справочник автоматически не меняется: результат — предложения и готовый список тем (`proposed_topics`) для новой
версии (POST /api/reasons/versions) после просмотра человеком.

Два способа:
  llm     — один вызов модели на все описания (стоит центы): группирует по смыслу на любом языке и
            замечает обращения, которые на самом деле подходят под существующую подтему;
  lexical — без модели: жадная группировка по общим основам слов. Запасной путь, если модель недоступна.
Описания пишет модель-судья по замаскированному тексту, поэтому персональных данных в них нет.
"""
from __future__ import annotations

import copy
import json
import re
from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.reasons.analytics import _filtered_evaluations
from app.reasons.taxonomy import OTHER, ReasonTaxonomy

_CODE = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_STOP = {"клиент", "клиента", "хочет", "просит", "вопрос", "вопросы", "узнать", "уточнить", "можно", "нужно", "почему",
         "который", "какой", "какие", "через", "после", "чтобы", "своей", "свой", "своего", "есть", "нет", "client",
         "wants", "asks", "about", "with", "from", "that", "this", "клиентке"}


# --------------------------------------------------------------------------- сбор
def collect_other(session: Session, dataset: str | None, limit: int) -> list[dict[str, Any]]:
    _, evs = _filtered_evaluations(session, dataset, None, None, None, None)
    out = []
    for e in sorted(evs, key=lambda e: e.id, reverse=True):
        if e.reason_code != OTHER:
            continue
        r = e.reason or {}
        text = (r.get("other_description") or r.get("request_summary") or "").strip()
        if text:
            out.append({"conversation_id": e.conversation_id, "evaluation_id": e.id, "description": text})
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- без модели
def _tokens(text: str) -> set[str]:
    """Основы слов: первые 5 букв («перевод», «перевести» → «перев»; «бумаги», «бумаг» → «бумаг»)."""
    words = re.findall(r"[^\W\d_]{4,}", text.lower().replace("ё", "е"))
    return {w[:5] for w in words if w not in _STOP}


def lexical_clusters(items: list[dict[str, Any]], min_cluster: int, threshold: float = 0.3) -> list[list[int]]:
    """Связывание пар с похожими основами (Жаккар ≥ порога) через union-find: результат не зависит от порядка."""
    toks = [_tokens(it["description"]) for it in items]
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(items)):
        if not toks[i]:
            continue
        for j in range(i + 1, len(items)):
            if toks[j] and len(toks[i] & toks[j]) / len(toks[i] | toks[j]) >= threshold:
                parent[find(j)] = find(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(items)):
        if toks[i]:
            groups.setdefault(find(i), []).append(i)
    return sorted((g for g in groups.values() if len(g) >= min_cluster), key=len, reverse=True)


def _lexical_proposals(items: list[dict[str, Any]], min_cluster: int) -> list[dict[str, Any]]:
    out = []
    for n, members in enumerate(lexical_clusters(items, min_cluster), start=1):
        words = Counter(w for i in members for w in re.findall(r"[^\W\d_]{4,}", items[i]["description"].lower())
                        if w not in _STOP)
        title = ", ".join(w for w, _ in words.most_common(3))
        out.append({"kind": "new_subtopic", "topic_code": "other", "topic_title": "Прочее",
                    "subtopic_code": f"auto_{n}", "subtopic_title": title.capitalize() or f"Кластер {n}",
                    "description": "Предложено автоматически по общим словам — переименуйте перед сохранением.",
                    "item_ids": members})
    return out


# --------------------------------------------------------------------------- модель
def _llm_schema(tax: ReasonTaxonomy) -> dict[str, Any]:
    item = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["new_subtopic", "existing"]},
            "existing_code": {"type": "string"},
            "topic_code": {"type": "string"},
            "topic_title": {"type": "string"},
            "subtopic_code": {"type": "string"},
            "subtopic_title": {"type": "string"},
            "description": {"type": "string"},
            "item_ids": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["kind", "existing_code", "topic_code", "topic_title", "subtopic_code", "subtopic_title",
                     "description", "item_ids"],
        "additionalProperties": False,
    }
    return {"type": "object", "properties": {"proposals": {"type": "array", "items": item}},
            "required": ["proposals"], "additionalProperties": False}


def _llm_prompts(tax: ReasonTaxonomy, items: list[dict[str, Any]], min_cluster: int) -> tuple[str, str]:
    codes = "\n".join(f"- `{t.code}.{s.code}` — {t.title} → {s.title}" + (f": {s.description}" if s.description else "")
                      for t in tax.topics for s in t.subtopics if f"{t.code}.{s.code}" != OTHER)
    system = (
        "Ты аналитик службы поддержки брокерской компании. Ведётся справочник причин обращений (код «тема.подтема»). "
        "Тебе дают описания обращений, которые классификатор отнёс в «Прочее». Сгруппируй их по смыслу.\n"
        f"- Группа из {min_cluster} и более похожих обращений, которой нет в справочнике → kind=new_subtopic: "
        "topic_code — код существующей темы, если подтема логично в неё входит, иначе новый код темы; "
        "subtopic_code — новый код (латиница, snake_case, до 40 символов); названия и description — по-русски, "
        "коротко и в стиле справочника; existing_code — пустая строка.\n"
        "- Обращения, которые на самом деле подходят под существующую подтему → kind=existing, existing_code — её код, "
        "остальные поля названий — пустые строки.\n"
        "- Одиночные непохожие обращения не включай никуда. Каждый номер — не больше чем в одной группе.\n"
        "- item_ids — номера описаний из списка.\n\n# Справочник\n" + codes)
    user = "# Описания обращений из «Прочего»\n" + "\n".join(f"{i}. {it['description']}" for i, it in enumerate(items))
    return system, user


async def _llm_proposals(client: Any, tax: ReasonTaxonomy, items: list[dict[str, Any]],
                         min_cluster: int) -> tuple[list[dict[str, Any]], float]:
    from app.brain.llm_common import parse_json_text

    system, user = _llm_prompts(tax, items, min_cluster)
    resp = await client.complete_json(system, user, _llm_schema(tax), "reason_discovery")
    data = parse_json_text(resp.text)
    return list(data.get("proposals") or []), float(resp.cost_usd or 0.0)


# --------------------------------------------------------------------------- проверка и сборка ответа
def _validate(raw: list[dict[str, Any]], tax: ReasonTaxonomy, items: list[dict[str, Any]],
              min_cluster: int) -> list[dict[str, Any]]:
    idx = tax.index()
    topics = {t.code: t for t in tax.topics}
    used: set[int] = set()
    out: list[dict[str, Any]] = []
    for p in raw:
        ids = [i for i in dict.fromkeys(p.get("item_ids") or [])
               if isinstance(i, int) and 0 <= i < len(items) and i not in used]
        kind = p.get("kind")
        if kind == "existing":
            code = str(p.get("existing_code") or "").strip().lower()
            if code not in idx or code == OTHER or not ids:
                continue
            used.update(ids)
            out.append({"kind": "existing", "code": code, "title": tax.title_of(code), "item_ids": ids})
            continue
        tcode = str(p.get("topic_code") or "").strip().lower()
        scode = str(p.get("subtopic_code") or "").strip().lower()
        if kind != "new_subtopic" or not _CODE.match(tcode) or not _CODE.match(scode) or len(ids) < min_cluster:
            continue
        if f"{tcode}.{scode}" in idx:      # такая подтема уже есть — это переклассификация
            used.update(ids)
            out.append({"kind": "existing", "code": f"{tcode}.{scode}", "title": tax.title_of(f"{tcode}.{scode}"),
                        "item_ids": ids})
            continue
        used.update(ids)
        out.append({"kind": "new_subtopic", "code": f"{tcode}.{scode}", "topic_code": tcode,
                    "topic_title": topics[tcode].title if tcode in topics else (p.get("topic_title") or tcode),
                    "new_topic": tcode not in topics, "subtopic_code": scode,
                    "title": (p.get("subtopic_title") or scode).strip(), "description": (p.get("description") or "").strip(),
                    "item_ids": ids})
    return out


def _proposed_topics(tax: ReasonTaxonomy, proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    topics = copy.deepcopy(tax.topics_dump())
    by_code = {t["code"]: t for t in topics}
    for p in proposals:
        if p["kind"] != "new_subtopic":
            continue
        t = by_code.get(p["topic_code"])
        if t is None:
            t = {"code": p["topic_code"], "title": p["topic_title"], "description": "", "subtopics": []}
            topics.insert(len(topics) - 1 if topics and topics[-1]["code"] == "other" else len(topics), t)
            by_code[t["code"]] = t
        if not any(s["code"] == p["subtopic_code"] for s in t["subtopics"]):
            t["subtopics"].append({"code": p["subtopic_code"], "title": p["title"], "description": p["description"]})
    return topics


async def discover(session: Session, tax: ReasonTaxonomy, *, dataset: str | None = None, min_cluster: int = 3,
                   limit: int = 300, client: Any = None) -> dict[str, Any]:
    items = collect_other(session, dataset, limit)
    result: dict[str, Any] = {"other_total": len(items), "analyzed": len(items), "min_cluster": min_cluster,
                              "method": None, "cost_usd": 0.0, "proposals": [], "unassigned": len(items),
                              "proposed_topics": None, "error": None}
    if len(items) < min_cluster:
        result["method"] = "none"
        result["error"] = f"В «Прочем» {len(items)} обращений — меньше порога {min_cluster}, предлагать нечего."
        return result
    raw: list[dict[str, Any]] = []
    if client is not None:
        try:
            raw, cost = await _llm_proposals(client, tax, items, min_cluster)
            result["method"], result["cost_usd"] = "llm", round(cost, 6)
        except Exception as exc:  # noqa: BLE001  модель недоступна → запасной путь, а не ошибка запроса
            result["error"] = f"Модель недоступна ({type(exc).__name__}: {exc}), использована группировка по словам."
    if result["method"] is None:
        raw, result["method"] = _lexical_proposals(items, min_cluster), "lexical"
    proposals = _validate(raw, tax, items, min_cluster)
    for p in proposals:
        p["count"] = len(p["item_ids"])
        p["examples"] = [{"conversation_id": items[i]["conversation_id"], "description": items[i]["description"]}
                         for i in p["item_ids"][:5]]
        p["conversation_ids"] = [items[i]["conversation_id"] for i in p["item_ids"]]
        del p["item_ids"]
    proposals.sort(key=lambda p: (p["kind"] != "new_subtopic", -p["count"]))
    result["proposals"] = proposals
    result["unassigned"] = len(items) - sum(p["count"] for p in proposals)
    if any(p["kind"] == "new_subtopic" for p in proposals):
        result["proposed_topics"] = _proposed_topics(tax, proposals)
    return result


def as_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)
