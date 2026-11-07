"""Сравнение режимов и провайдеров с мини-эталоном (раздел 9 спецификации).

Источник прогнозов — БД (все оценки диалогов из эталона, сгруппированные по конфигурации:
режим + локальная модель + облачная модель + стратегия + версия чек-листа). Дополнительно можно сравнить
готовые файлы results.json через --results метка=путь (только метрики точности).

Запуск из корня репозитория:
  python eval/compare.py --gold eval/gold/dev_gold.json [--out eval/report.md] [--results claude=results/dev/results.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.checklist.render import load_default_checklist  # noqa: E402
from app.scoring.calculator import compute_score  # noqa: E402

TODO = "TODO"


def load_gold(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    checklist = load_default_checklist()[0]
    gold = {}
    for r in data.get("results", []):
        values = {c["criterion_id"]: c["value"] for c in r.get("criteria", []) if c.get("value") != TODO}
        if not values:
            continue
        score = None
        if all(c.criterion_id in values for c in checklist.criteria):
            score = compute_score(values, checklist.criteria)
        gold[r["conversation_id"]] = {"values": values, "score": score}
    return gold


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def _num(x: float | None, d: int = 2) -> str:
    return "—" if x is None else f"{x:.{d}f}"


def metrics(preds: list[dict[str, Any]], gold: dict[str, dict[str, Any]], languages: dict[str, str]) -> dict[str, Any]:
    """preds: [{conversation_id, status, values, final_score, criteria(full|None), cost, duration_ms, json_first_try}]"""
    per_crit: dict[str, list[bool]] = defaultdict(list)
    na_hits, crit_tp, crit_fp, crit_fn = [], 0, 0, 0
    mae, mae_kz, acc_kz = [], [], []
    overrides = helpful = harmful = pairs = 0
    review_crit, review_dialog, costs, times, json_ok = [], [], [], [], []
    errors = 0
    for p in preds:
        g = gold.get(p["conversation_id"])
        if g is None:
            continue
        if p["status"] != "ok":
            errors += 1
            continue
        is_kz = languages.get(p["conversation_id"]) == "KZ"
        for cid, gv in g["values"].items():
            if cid not in p["values"]:
                continue
            pv = p["values"][cid]
            ok = pv == gv and type(pv) is type(gv)
            per_crit[cid].append(ok)
            if is_kz:
                acc_kz.append(ok)
            if (gv is None) or (pv is None):
                na_hits.append((gv is None) == (pv is None))
            if cid == "critical":
                crit_tp += int(pv is True and gv is True)
                crit_fp += int(pv is True and gv is False)
                crit_fn += int(pv is False and gv is True)
        if g["score"] is not None and p["final_score"] is not None:
            mae.append(abs(p["final_score"] - g["score"]))
            if is_kz:
                mae_kz.append(abs(p["final_score"] - g["score"]))
        for c in p.get("criteria") or []:
            review_crit.append(bool(c.get("needs_review")))
            if c.get("local") is not None and c.get("cloud") is not None:
                pairs += 1
                if c.get("agreement") in ("override", "arbiter"):
                    overrides += 1
                    gv = g["values"].get(c["criterion_id"], TODO)
                    if gv != TODO:
                        lv, fv = c["local"]["value"], c.get("ai_value", c["value"])
                        helpful += int(fv == gv and lv != gv)
                        harmful += int(fv != gv and lv == gv)
        if p.get("criteria") is not None:
            review_dialog.append(any(c.get("needs_review") for c in p["criteria"]))
        if p.get("cost") is not None:
            costs.append(p["cost"])
        if p.get("duration_ms"):
            times.append(p["duration_ms"] / 1000)
        if p.get("json_first_try") is not None:
            json_ok.append(p["json_first_try"])
    all_hits = [x for v in per_crit.values() for x in v]
    return {
        "n": sum(1 for p in preds if p["conversation_id"] in gold), "errors": errors,
        "json_first_try": mean(json_ok) if json_ok else None,
        "accuracy": mean(all_hits) if all_hits else None,
        "per_criterion": {k: mean(v) for k, v in per_crit.items()},
        "na_accuracy": mean(na_hits) if na_hits else None,
        "mae": mean(mae) if mae else None,
        "critical_precision": crit_tp / (crit_tp + crit_fp) if (crit_tp + crit_fp) else None,
        "critical_recall": crit_tp / (crit_tp + crit_fn) if (crit_tp + crit_fn) else None,
        "override_rate": overrides / pairs if pairs else None,
        "helpful_override": helpful / overrides if overrides else None,
        "harmful_override": harmful / overrides if overrides else None,
        "needs_review_criteria": mean(review_crit) if review_crit else None,
        "needs_review_dialogs": mean(review_dialog) if review_dialog else None,
        "cost_avg": mean(costs) if costs else None,
        "time_avg": mean(times) if times else None,
        "time_max": max(times) if times else None,
        "kz_accuracy": mean(acc_kz) if acc_kz else None,
        "kz_mae": mean(mae_kz) if mae_kz else None,
    }


def from_db(gold_ids: list[str]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, float], dict[str, str]]:
    from app.config import get_settings
    from app.db import configure, session_scope
    from app.models import ConversationRow, Evaluation

    configure(get_settings().database_url)
    groups: dict[str, dict[str, Evaluation]] = defaultdict(dict)
    repro_groups: dict[str, list[Evaluation]] = defaultdict(list)
    with session_scope() as s:
        languages = {r.conversation_id: r.language for r in s.query(ConversationRow).filter(
            ConversationRow.conversation_id.in_(gold_ids))}
        evs = s.query(Evaluation).filter(Evaluation.conversation_id.in_(gold_ids)).order_by(Evaluation.id).all()
        for e in evs:
            label = f"{e.mode} · {e.model_name}" + (f" · {e.hybrid_strategy}" if e.hybrid_strategy else "") + f" · чек-лист {e.checklist_version_id}"
            groups[label][e.conversation_id] = e  # последняя оценка конфигурации
            if e.status == "ok" and e.cache_key:
                repro_groups[label + e.cache_key].append(e)
        out: dict[str, list[dict[str, Any]]] = {}
        for label, by_conv in groups.items():
            rows = []
            for e in by_conv.values():
                stages = (e.audit or {}).get("stages", {})
                first = [st.get("attempts") == 1 and st.get("ok") for name, st in stages.items()
                         if isinstance(st, dict) and "attempts" in st]
                rows.append({
                    "conversation_id": e.conversation_id, "status": e.status,
                    "values": {c["criterion_id"]: c.get("ai_value", c["value"]) for c in e.criteria},
                    "final_score": e.ai_score, "criteria": e.criteria, "cost": e.cost_usd,
                    "duration_ms": e.duration_ms, "json_first_try": (all(first) if first else None),
                })
            out[label] = rows
        # Воспроизводимость: оценки с одинаковым ключом (прогон с --force дважды)
        repro: dict[str, float] = {}
        per_label: dict[str, list[bool]] = defaultdict(list)
        for key, items in repro_groups.items():
            if len(items) < 2:
                continue
            label = next(lbl for lbl in groups if key.startswith(lbl))
            base = [c.get("ai_value", c["value"]) for c in items[0].criteria]
            for other in items[1:]:
                per_label[label].append(base == [c.get("ai_value", c["value"]) for c in other.criteria])
        for label, v in per_label.items():
            repro[label] = mean(v)
    return out, repro, languages


def from_results_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for r in data["results"]:
        rows.append({"conversation_id": r["conversation_id"], "status": r["status"],
                     "values": {c["criterion_id"]: c["value"] for c in r.get("criteria", [])},
                     "final_score": (r.get("score") or {}).get("final_score"), "criteria": None})
    return rows


def render(results: dict[str, dict[str, Any]], repro: dict[str, float], gold_n: int) -> str:
    lines = [f"# Сравнение конфигураций с мини-эталоном ({gold_n} диалогов)", ""]
    head = ["Конфигурация", "N", "Ошибок", "JSON с 1-го раза", "Совпадение критериев", "Точность N/A", "MAE итога",
            "critical P / R", "override", "полезные / вредные", "needs_review (диалоги)", "$ / оценка", "сек (ср / макс)",
            "KZ: совпадение / MAE", "Воспроизводимость"]
    lines.append("| " + " | ".join(head) + " |")
    lines.append("|" + "---|" * len(head))
    for label, m in results.items():
        lines.append("| " + " | ".join([
            label, str(m["n"]), str(m["errors"]), _pct(m["json_first_try"]), _pct(m["accuracy"]), _pct(m["na_accuracy"]),
            _num(m["mae"]), f"{_pct(m['critical_precision'])} / {_pct(m['critical_recall'])}", _pct(m["override_rate"]),
            f"{_pct(m['helpful_override'])} / {_pct(m['harmful_override'])}", _pct(m["needs_review_dialogs"]),
            _num(m["cost_avg"], 4), f"{_num(m['time_avg'], 1)} / {_num(m['time_max'], 1)}",
            f"{_pct(m['kz_accuracy'])} / {_num(m['kz_mae'])}", _pct(repro.get(label)),
        ]) + " |")
    crits = sorted({c for m in results.values() for c in m["per_criterion"]})
    if crits:
        lines += ["", "## Совпадение по критериям", "", "| Конфигурация | " + " | ".join(crits) + " |",
                  "|" + "---|" * (len(crits) + 1)]
        for label, m in results.items():
            lines.append(f"| {label} | " + " | ".join(_pct(m["per_criterion"].get(c)) for c in crits) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--gold", default=str(ROOT / "eval" / "gold" / "dev_gold.json"))
    p.add_argument("--results", action="append", default=[], help="метка=путь к results.json")
    p.add_argument("--no-db", action="store_true", help="не читать оценки из БД")
    p.add_argument("--out", help="сохранить отчёт в Markdown")
    args = p.parse_args()

    gold = load_gold(Path(args.gold))
    if not gold:
        print("В эталоне нет размеченных диалогов (все значения TODO). Заполните eval/gold/dev_gold.json.")
        return 1
    results: dict[str, dict[str, Any]] = {}
    repro: dict[str, float] = {}
    languages: dict[str, str] = {}
    if not args.no_db:
        db_preds, repro, languages = from_db(list(gold))
        for label, rows in db_preds.items():
            results[label] = metrics(rows, gold, languages)
    for spec in args.results:
        label, _, path = spec.partition("=")
        results[f"файл: {label}"] = metrics(from_results_file(Path(path)), gold, languages)
    report = render(results, repro, len(gold))
    print(report)
    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"Отчёт сохранён: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
