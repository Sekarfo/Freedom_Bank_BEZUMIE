"""Аналитика для руководителей: агрегаты по последней оценке каждого диалога."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Appeal, ConversationRow, Evaluation
from app.store import latest_evaluations_map

BUCKETS = [(0, 50, "0–50"), (50, 70, "50–70"), (70, 85, "70–85"), (85, 95, "85–95"), (95, 100.01, "95–100")]


def _avg(xs: list[float]) -> float | None:
    return round(mean(xs), 2) if xs else None


def summary(session: Session, dataset: str | None = None, channel: str | None = None, language: str | None = None,
            date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
    q = select(ConversationRow)
    if dataset:
        q = q.where(ConversationRow.dataset == dataset)
    if channel:
        q = q.where(ConversationRow.channel == channel)
    if language:
        q = q.where(ConversationRow.language == language)
    rows = {r.conversation_id: r for r in session.scalars(q)}
    evs = latest_evaluations_map(session, list(rows))
    if date_from or date_to:
        df = datetime.fromisoformat(date_from) if date_from else None
        dt = datetime.fromisoformat(date_to) if date_to else None

        def in_range(ev: Evaluation) -> bool:
            created = ev.created_at.replace(tzinfo=None)
            return (df is None or created >= df) and (dt is None or created <= dt)
        evs = {k: v for k, v in evs.items() if in_range(v)}

    ok = [e for e in evs.values() if e.status == "ok"]
    scores = [e.final_score for e in ok if e.final_score is not None]

    dist = []
    for lo, hi, label in BUCKETS:
        dist.append({"label": label, "count": sum(1 for s in scores if lo <= s < hi)})

    # По критериям
    per: dict[str, dict[str, Any]] = {}
    for e in ok:
        for c in e.criteria:
            d = per.setdefault(c["criterion_id"], {"criterion_id": c["criterion_id"], "title": c.get("title"),
                                                   "weight": c.get("weight"), "is_critical_flag": c.get("is_critical_flag"),
                                                   "n": 0, "ratios": [], "no": 0, "na": 0, "yes": 0,
                                                   "override": 0, "pairs": 0, "needs_review": 0, "appealed": 0})
            d["n"] += 1
            v = c.get("value")
            if v is None:
                d["na"] += 1
            elif v is False or (not isinstance(v, bool) and c.get("max_points") and c.get("points") == 0):
                d["no"] += 1
            elif v is True:
                d["yes"] += 1
            if c.get("points") is not None and c.get("max_points"):
                d["ratios"].append(c["points"] / c["max_points"])
            if c.get("local") is not None and c.get("cloud") is not None:
                d["pairs"] += 1
                if c.get("agreement") in ("override", "arbiter"):
                    d["override"] += 1
            if c.get("needs_review"):
                d["needs_review"] += 1
            if c.get("review"):
                d["appealed"] += 1
    criteria = []
    for d in per.values():
        n = d["n"] or 1
        criteria.append({
            "criterion_id": d["criterion_id"], "title": d["title"], "weight": d["weight"],
            "is_critical_flag": d["is_critical_flag"], "evaluated": d["n"],
            "avg_ratio": _avg(d["ratios"]) if d["ratios"] else None,
            "share_no": round(d["no"] / n, 3), "share_na": round(d["na"] / n, 3), "share_yes": round(d["yes"] / n, 3),
            "override_rate": round(d["override"] / d["pairs"], 3) if d["pairs"] else None,
            "needs_review_rate": round(d["needs_review"] / n, 3), "appealed": d["appealed"],
        })
    criteria.sort(key=lambda x: (x["avg_ratio"] is None, x["avg_ratio"] if x["avg_ratio"] is not None else 0))

    def group(key: str) -> list[dict[str, Any]]:
        g: dict[str, list[Evaluation]] = defaultdict(list)
        for e in ok:
            r = rows.get(e.conversation_id)
            if r is not None:
                g[getattr(r, key)].append(e)
        return [{"key": k, "count": len(v), "avg_score": _avg([e.final_score for e in v if e.final_score is not None]),
                 "critical": sum(1 for e in v if e.critical_flag), "needs_review": sum(1 for e in v if e.needs_review)}
                for k, v in sorted(g.items())]

    criticals = []
    for e in ok:
        if e.critical_flag:
            c = next((c for c in e.criteria if c.get("is_critical_flag") and c.get("value") is True), None)
            criticals.append({"conversation_id": e.conversation_id, "evaluation_id": e.id,
                              "quote": c.get("quote") if c else "", "rationale": c.get("rationale") if c else ""})

    ev_ids = [e.id for e in evs.values()]
    appeals = session.scalars(select(Appeal).where(Appeal.evaluation_id.in_(ev_ids))).all() if ev_ids else []
    changed = sum(1 for a in appeals if a.status == "resolved" and a.action == "change")
    costs = [e.cost_usd for e in evs.values() if e.cost_usd]
    agreement = Counter(c.get("agreement") for e in ok for c in e.criteria)

    return {
        "filters": {"dataset": dataset, "channel": channel, "language": language, "from": date_from, "to": date_to},
        "conversations_total": len(rows),
        "evaluated": len(evs),
        "ok": len(ok),
        "errors": sum(1 for e in evs.values() if e.status != "ok"),
        "not_evaluated": len(rows) - len(evs),
        "avg_score": _avg(scores),
        "median_score": (sorted(scores)[len(scores) // 2] if scores else None),
        "distribution": dist,
        "criteria": criteria,
        "by_language": group("language"),
        "by_channel": group("channel"),
        "critical_count": len(criticals),
        "criticals": criticals,
        "needs_review_count": sum(1 for e in ok if e.needs_review),
        "appeals": {"total": len(appeals), "open": sum(1 for a in appeals if a.status == "open"),
                    "resolved": sum(1 for a in appeals if a.status == "resolved"), "changed": changed,
                    "rate": round(len(appeals) / len(ok), 3) if ok else None},
        "agreement": dict(agreement),
        "cost": {"total_usd": round(sum(costs), 4), "avg_per_evaluation_usd": round(mean(costs), 5) if costs else None,
                 "evaluations_with_cost": len(costs)},
        "avg_duration_sec": _avg([e.duration_ms / 1000 for e in evs.values() if e.duration_ms]),
        "models": dict(Counter(e.model_name for e in evs.values())),
    }
