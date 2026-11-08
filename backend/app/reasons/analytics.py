"""Аналитика «причина × качество» по последней оценке каждого диалога.

- по каждой теме и подтеме: объём, доля, средний балл, исходы (решён / передан / не решён / ушёл), грубые нарушения,
  «на проверку», самые слабые критерии;
- матрица «причина × критерий»: средняя доля баллов (для флага critical — доля нарушений);
- находки: где критерий по причине заметно хуже, чем в среднем по всем диалогам — готовая задача на обучение;
- доп. причины и описания из «Прочего» (материал для поиска новых подтем).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConversationRow, Evaluation, ReasonTaxonomyVersion
from app.reasons.taxonomy import OTHER, RESOLUTIONS, ReasonTaxonomy
from app.store import latest_evaluations_map

MIN_N = 3          # минимум диалогов в ячейке, чтобы делать выводы
MIN_GAP = 0.15     # «заметно хуже»: на 15 п.п. ниже среднего


def _avg(xs: list[float], nd: int = 3) -> float | None:
    return round(mean(xs), nd) if xs else None


def criterion_quality(c: dict[str, Any]) -> float | None:
    """Доля от максимума (0..1) для взвешенных критериев; для флага — 1, если нарушение было, иначе 0. N/A → None."""
    if c.get("is_critical_flag"):
        v = c.get("value")
        return None if v is None else (1.0 if v is True else 0.0)
    if c.get("points") is None or not c.get("max_points"):
        return None
    return c["points"] / c["max_points"]


def _filtered_evaluations(session: Session, dataset: str | None, channel: str | None, language: str | None,
                          date_from: str | None, date_to: str | None) -> tuple[dict[str, ConversationRow], list[Evaluation]]:
    q = select(ConversationRow)
    if dataset:
        q = q.where(ConversationRow.dataset == dataset)
    if channel:
        q = q.where(ConversationRow.channel == channel)
    if language:
        q = q.where(ConversationRow.language == language)
    rows = {r.conversation_id: r for r in session.scalars(q)}
    evs = [e for e in latest_evaluations_map(session, list(rows)).values() if e.status == "ok"]
    df = datetime.fromisoformat(date_from) if date_from else None
    dt = datetime.fromisoformat(date_to) if date_to else None
    evs = [e for e in evs if (df is None or e.created_at.replace(tzinfo=None) >= df)
           and (dt is None or e.created_at.replace(tzinfo=None) <= dt)]
    return rows, evs


class _Bucket:
    def __init__(self) -> None:
        self.evs: list[Evaluation] = []

    def add(self, e: Evaluation) -> None:
        self.evs.append(e)

    def criteria(self) -> dict[str, dict[str, Any]]:
        per: dict[str, dict[str, Any]] = {}
        for e in self.evs:
            for c in e.criteria or []:
                q = criterion_quality(c)
                d = per.setdefault(c["criterion_id"], {"title": c.get("title"), "flag": bool(c.get("is_critical_flag")),
                                                       "q": [], "ids": [], "no": 0, "na": 0, "n": 0})
                d["n"] += 1
                if c.get("value") is None:
                    d["na"] += 1
                elif q is not None:
                    d["q"].append(q)
                    d["ids"].append((q, e.conversation_id))   # где критерий оценивался (для находок)
                    if (q == 0.0 and not d["flag"]) or (d["flag"] and q == 1.0):
                        d["no"] += 1
        return per

    def summary(self, total: int, baseline: dict[str, float | None]) -> dict[str, Any]:
        n = len(self.evs)
        scores = [e.final_score for e in self.evs if e.final_score is not None]
        res = Counter(e.resolution or "unknown" for e in self.evs)
        crit = self.criteria()
        weakest = []
        for cid, d in crit.items():
            if d["flag"] or not d["q"]:
                continue
            avg = mean(d["q"])
            weakest.append({"criterion_id": cid, "title": d["title"], "avg_ratio": round(avg, 3), "evaluated": len(d["q"]),
                            "share_failed": round(d["no"] / len(d["q"]), 3),
                            "baseline": baseline.get(cid), "gap": round(avg - baseline[cid], 3)
                            if baseline.get(cid) is not None else None})
        weakest.sort(key=lambda x: x["avg_ratio"])
        return {
            "count": n, "share": round(n / total, 3) if total else None,
            "avg_score": round(mean(scores), 2) if scores else None,
            "resolution": {k: res.get(k, 0) for k in (*RESOLUTIONS, "unknown") if res.get(k, 0) or k in RESOLUTIONS},
            "resolved_rate": round(res.get("resolved", 0) / n, 3) if n else None,
            "escalated_rate": round(res.get("escalated", 0) / n, 3) if n else None,
            "unresolved_rate": round((res.get("unresolved", 0) + res.get("client_left", 0)) / n, 3) if n else None,
            "critical": sum(1 for e in self.evs if e.critical_flag),
            "needs_review": sum(1 for e in self.evs if e.needs_review),
            "weakest_criteria": weakest[:3],
            "conversation_ids": [e.conversation_id for e in self.evs][:50],
        }


def reasons_summary(session: Session, dataset: str | None = None, channel: str | None = None,
                    language: str | None = None, date_from: str | None = None, date_to: str | None = None,
                    taxonomy_version_id: int | None = None, level: str = "topic") -> dict[str, Any]:
    from app.reasons.service import get_version, to_taxonomy

    rows, evs = _filtered_evaluations(session, dataset, channel, language, date_from, date_to)
    with_reason = [e for e in evs if e.reason_code]
    if taxonomy_version_id is not None:
        with_reason = [e for e in with_reason if e.reason_taxonomy_version_id == taxonomy_version_id]
    # Справочник для названий: указанный, иначе тот, по которому сделано больше всего оценок, иначе активный.
    used = Counter(e.reason_taxonomy_version_id for e in with_reason)
    tax_id = taxonomy_version_id or (used.most_common(1)[0][0] if used else None)
    tax_ver = session.get(ReasonTaxonomyVersion, tax_id) if tax_id else get_version(session)
    tax: ReasonTaxonomy = to_taxonomy(tax_ver)
    total = len(with_reason)

    baseline_bucket = _Bucket()
    for e in with_reason:
        baseline_bucket.add(e)
    base_crit = baseline_bucket.criteria()
    baseline = {cid: (round(mean(d["q"]), 3) if d["q"] else None) for cid, d in base_crit.items()}

    by_topic: dict[str, _Bucket] = defaultdict(_Bucket)
    by_code: dict[str, _Bucket] = defaultdict(_Bucket)
    secondary = Counter()
    for e in with_reason:
        by_topic[e.reason_topic or e.reason_code.split(".", 1)[0]].add(e)
        by_code[e.reason_code].add(e)
        secondary.update((e.reason or {}).get("secondary") or [])

    topics_out = []
    for t in tax.topics:
        b = by_topic.get(t.code)
        if b is None:
            continue
        item = {"code": t.code, "title": t.title, **b.summary(total, baseline), "subtopics": []}
        for s in t.subtopics:
            code = f"{t.code}.{s.code}"
            if code in by_code:
                item["subtopics"].append({"code": code, "title": s.title, **by_code[code].summary(total, baseline)})
        item["subtopics"].sort(key=lambda x: -x["count"])
        topics_out.append(item)
    # Коды, которых нет в выбранном справочнике (оценки по другой версии)
    known = set(tax.codes())
    for code, b in by_code.items():
        if code not in known:
            topics_out.append({"code": code, "title": f"{code} (нет в справочнике v{tax_ver.version})",
                               **b.summary(total, baseline), "subtopics": []})
    topics_out.sort(key=lambda x: -x["count"])

    buckets = by_topic if level == "topic" else by_code
    matrix_rows = []
    for key, b in sorted(buckets.items(), key=lambda kv: -len(kv[1].evs)):
        cells = {}
        for cid, d in b.criteria().items():
            cells[cid] = {"avg": round(mean(d["q"]), 3) if d["q"] else None, "evaluated": len(d["q"]), "na": d["na"]}
        matrix_rows.append({"code": key, "title": tax.title_of(key), "count": len(b.evs), "cells": cells})

    return {
        "filters": {"dataset": dataset, "channel": channel, "language": language, "from": date_from, "to": date_to,
                    "level": level},
        "taxonomy": {"id": tax_ver.id, "version": tax_ver.version, "name": tax_ver.name},
        "evaluated": len(evs),
        "classified": total,
        "unclassified": len(evs) - len([e for e in evs if e.reason_code]),
        "resolution": {k: sum(1 for e in with_reason if e.resolution == k) for k in RESOLUTIONS},
        "baseline": baseline,
        "topics": topics_out,
        "matrix": {"level": level, "criteria": [{"criterion_id": cid, "title": d["title"], "is_critical_flag": d["flag"]}
                                                for cid, d in base_crit.items()], "rows": matrix_rows},
        "findings": _merge_findings(findings(by_code, tax, baseline), findings(by_topic, tax, baseline)),
        "secondary": [{"code": c, "title": tax.title_of(c), "count": n} for c, n in secondary.most_common()],
        "other": other_items(with_reason),
    }


def findings(buckets: dict[str, _Bucket], tax: ReasonTaxonomy, baseline: dict[str, float | None]) -> list[dict[str, Any]]:
    """Причина, по которой критерий проседает сильнее среднего: «по выводу средств — идентификация 40% против 85%»."""
    out = []
    for key, b in buckets.items():
        for cid, d in b.criteria().items():
            base = baseline.get(cid)
            if d["flag"] or base is None or len(d["q"]) < MIN_N:
                continue
            avg = mean(d["q"])
            gap = avg - base
            if gap <= -MIN_GAP:
                out.append({
                    "reason": key, "reason_title": tax.title_of(key), "criterion_id": cid, "criterion_title": d["title"],
                    "avg_ratio": round(avg, 3), "baseline": base, "gap": round(gap, 3), "evaluated": len(d["q"]),
                    "impact": round(-gap * len(d["q"]), 2),
                    "text": f"«{tax.title_of(key)}»: «{d['title']}» — {avg:.0%} против {base:.0%} в среднем "
                            f"({len(d['q'])} диал.)",
                    # диалоги, где критерий провален или снижен, — сначала худшие (материал для разбора с оператором)
                    "conversation_ids": [cid for q, cid in sorted(d["ids"]) if q < 1][:20],
                    "_evaluated": frozenset(cid for _, cid in d["ids"]),
                })
    out.sort(key=lambda x: -x["impact"])
    return out


def _merge_findings(sub: list[dict[str, Any]], top: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Находка по теме не дублирует находку по подтеме, если критерий оценивался в тех же диалогах."""
    seen = {(f["criterion_id"], f["_evaluated"]) for f in sub}
    out = sub + [f for f in top if (f["criterion_id"], f["_evaluated"]) not in seen]
    for f in out:
        del f["_evaluated"]
    return sorted(out, key=lambda x: -x["impact"])


def other_items(evs: Iterable[Evaluation]) -> dict[str, Any]:
    items = [{"conversation_id": e.conversation_id, "evaluation_id": e.id,
              "description": (e.reason or {}).get("other_description") or (e.reason or {}).get("request_summary") or ""}
             for e in evs if e.reason_code == OTHER]
    return {"count": len(items), "items": items[:100]}
