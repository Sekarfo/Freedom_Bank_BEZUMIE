"""Сравнение results.json с размеченным эталоном (формат 1.0) — точность по критериям и итогу.

Запуск из корня репозитория:
  python eval/score_vs_gold.py --gold eval/synt_dataset/synt_results.json \
      --run sonnet=eval/runs/.../results_claude-sonnet-5-5.json --run opus=eval/runs/.../results_claude-opus-5-5.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

CRITERIA = ["contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical"]


def load(path: str) -> dict[str, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {r["conversation_id"]: r for r in data["results"]}


def values(rec: dict) -> dict:
    return {c["criterion_id"]: c["value"] for c in rec.get("criteria") or []}


def same(a, b) -> bool:
    return a == b and type(a) is type(b)


def lang(cid: str) -> str:
    parts = cid.split("-")
    return parts[1].upper() if len(parts) > 2 else "?"


def score_run(gold: dict, pred: dict) -> dict:
    per_crit, per_lang, confusion = defaultdict(list), defaultdict(list), defaultdict(Counter)
    mae, errors, misses = [], 0, []
    tp = fp = fn = 0
    for cid, g in gold.items():
        p = pred.get(cid)
        if p is None or p.get("status") != "ok":
            errors += 1
            continue
        gv, pv = values(g), values(p)
        for k in CRITERIA:
            if k not in gv:
                continue
            ok = same(pv.get(k), gv[k])
            per_crit[k].append(ok)
            per_lang[lang(cid)].append(ok)
            if not ok:
                confusion[k][(json.dumps(gv[k]), json.dumps(pv.get(k)))] += 1
                misses.append((cid, k, gv[k], pv.get(k)))
        if gv.get("critical") is True or pv.get("critical") is True:
            tp += int(gv.get("critical") is True and pv.get("critical") is True)
            fp += int(gv.get("critical") is not True and pv.get("critical") is True)
            fn += int(gv.get("critical") is True and pv.get("critical") is not True)
        gs, ps = (g.get("score") or {}).get("final_score"), (p.get("score") or {}).get("final_score")
        if gs is not None and ps is not None:
            mae.append(abs(gs - ps))
    allv = [x for v in per_crit.values() for x in v]
    return {
        "n": len(gold), "errors": errors,
        "accuracy": sum(allv) / len(allv) if allv else None,
        "per_criterion": {k: sum(v) / len(v) for k, v in per_crit.items()},
        "per_language": {k: sum(v) / len(v) for k, v in per_lang.items()},
        "mae": sum(mae) / len(mae) if mae else None,
        "within_5": sum(1 for x in mae if x <= 5) / len(mae) if mae else None,
        "critical": {"tp": tp, "fp": fp, "fn": fn},
        "confusion": {k: {f"{a}→{b}": n for (a, b), n in v.items()} for k, v in confusion.items()},
        "misses": misses,
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--run", action="append", required=True, help="метка=путь к results.json")
    ap.add_argument("--json-out")
    args = ap.parse_args()
    gold = load(args.gold)
    runs = {}
    for spec in args.run:
        label, path = spec.split("=", 1)
        runs[label] = score_run(gold, load(path))
    labels = list(runs)
    print(f"{'метрика':22s}" + "".join(f"{l:>14s}" for l in labels))
    def row(name, fn):
        print(f"{name:22s}" + "".join(f"{fn(runs[l]):>14s}" for l in labels))
    pct = lambda x: "—" if x is None else f"{x * 100:.1f}%"
    row("ошибок status", lambda r: str(r["errors"]))
    row("точность (все)", lambda r: pct(r["accuracy"]))
    for k in CRITERIA:
        row(f"  {k}", lambda r, k=k: pct(r["per_criterion"].get(k)))
    for lg in ("RU", "KZ", "EN"):
        row(f"  язык {lg}", lambda r, lg=lg: pct(r["per_language"].get(lg)))
    row("MAE итога, баллы", lambda r: "—" if r["mae"] is None else f"{r['mae']:.2f}")
    row("итог в пределах ±5", lambda r: pct(r["within_5"]))
    row("critical TP/FP/FN", lambda r: "{tp}/{fp}/{fn}".format(**r["critical"]))
    for l in labels:
        print(f"\n[{l}] расхождения по критериям (эталон → модель: число):")
        for k, conf in runs[l]["confusion"].items():
            print(f"  {k}: " + ", ".join(f"{ab}: {n}" for ab, n in sorted(conf.items(), key=lambda x: -x[1])))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(runs, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
