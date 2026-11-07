"""Создаёт шаблон мини-эталона eval/gold/dev_gold.json (формат results.json 1.0).

Выборка по разделу 9.1: 10 RU-чатов, 8 KZ, 7 EN и 3 звонка — равномерно по номерам.
Значения критериев = "TODO": их заполняют люди (true/false/null или ступень шкалы), второй человек проверяет.
compare.py пропускает критерии со значением "TODO".

Запуск из корня репозитория:  python eval/make_gold_template.py [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODES = ["contact", "discovery", "identification", "consultation", "empathy", "dialogue", "closure", "critical"]


def pick(ids: list[str], k: int) -> list[str]:
    if len(ids) <= k:
        return ids
    step = len(ids) / k
    return [ids[int(i * step)] for i in range(k)]


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("--dataset-file", default=str(ROOT / "data" / "raw" / "dev_dataset.json"))
    p.add_argument("--out", default=str(ROOT / "eval" / "gold" / "dev_gold.json"))
    p.add_argument("--force", action="store_true", help="перезаписать существующий файл")
    args = p.parse_args()

    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"{out} уже существует — разметка не перезаписана (используйте --force)")
        return 1
    records = json.loads(Path(args.dataset_file).read_text(encoding="utf-8"))["data"]["records"]
    chats = {lang: [r["conversation_id"] for r in records if r["channel"] == "agent chat" and r["language"] == lang]
             for lang in ("RU", "KZ", "EN")}
    calls = [r["conversation_id"] for r in records if r["channel"] == "voice call"]
    selected = pick(chats["RU"], 10) + pick(chats["KZ"], 8) + pick(chats["EN"], 7) + pick(sorted(calls), 3)

    data = {
        "schema_version": "1.0",
        "team_name": "gold",
        "model": {"mode": "hybrid", "name": "human"},
        "_instructions": "Заполните value (true/false/null или ступень шкалы), rationale и evidence_turns. "
                         "Каждую разметку проверяет второй человек (поле reviewed_by).",
        "results": [{
            "conversation_id": cid, "status": "ok", "annotator": "", "reviewed_by": "",
            "criteria": [{"criterion_id": c, "value": "TODO", "rationale": "", "evidence_turns": []} for c in CODES],
            "score": None,
        } for cid in selected],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Шаблон на {len(selected)} диалогов: {out}")
    print(", ".join(selected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
