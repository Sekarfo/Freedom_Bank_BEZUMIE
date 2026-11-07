"""Проверка results.json перед push (раздел 9.5 спецификации и приложение к гайду)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.checklist.render import load_default_checklist
from app.schemas import ChecklistConfig


def validate_results(data: Any, expect: int | None = None, checklist: ChecklistConfig | None = None,
                     expected_ids: list[str] | None = None) -> tuple[list[str], list[str]]:
    """→ (ошибки, предупреждения). Пустой список ошибок = файл можно пушить."""
    errors: list[str] = []
    warnings: list[str] = []
    checklist = checklist or load_default_checklist()[0]
    spec = checklist.by_id()

    if not isinstance(data, dict):
        return ["Корень JSON должен быть объектом"], warnings
    if data.get("schema_version") != "1.0":
        errors.append(f"schema_version должен быть \"1.0\", получено {data.get('schema_version')!r}")
    if not str(data.get("team_name") or "").strip():
        errors.append("Не заполнен team_name")
    model = data.get("model")
    if not isinstance(model, dict):
        errors.append("Нет объекта model")
    else:
        if model.get("mode") not in ("local", "external_api", "hybrid"):
            errors.append(f"model.mode должен быть local | external_api | hybrid, получено {model.get('mode')!r}")
        if not str(model.get("name") or "").strip():
            errors.append("Не заполнен model.name")
    results = data.get("results")
    if not isinstance(results, list):
        return errors + ["Нет массива results"], warnings

    ids = [r.get("conversation_id") for r in results if isinstance(r, dict)]
    dups = sorted({i for i in ids if ids.count(i) > 1})
    if dups:
        errors.append(f"Повторяющиеся conversation_id: {dups}")
    if expect is not None and len(results) != expect:
        errors.append(f"Ожидалось {expect} записей, в файле {len(results)}")
    if expected_ids is not None:
        missing = sorted(set(expected_ids) - set(ids))
        extra = sorted(set(ids) - set(expected_ids))
        if missing:
            errors.append(f"Нет записей для ID из API: {missing}")
        if extra:
            errors.append(f"ID отсутствуют в API: {extra}")

    for n, r in enumerate(results, start=1):
        where = f"results[{n}] ({r.get('conversation_id') if isinstance(r, dict) else '?'})"
        if not isinstance(r, dict):
            errors.append(f"{where}: запись должна быть объектом")
            continue
        if not r.get("conversation_id"):
            errors.append(f"{where}: пустой conversation_id")
        status = r.get("status")
        if status == "error":
            if r.get("criteria") != []:
                errors.append(f"{where}: при status=error criteria должен быть []")
            if r.get("score") is not None:
                errors.append(f"{where}: при status=error score должен быть null")
            if not str(r.get("error") or "").strip():
                errors.append(f"{where}: при status=error нужно поле error с причиной")
            else:
                warnings.insert(0, f"{where}: status=error — {r.get('error')}")
            continue
        if status != "ok":
            errors.append(f"{where}: status должен быть ok или error")
            continue
        crits = r.get("criteria")
        if not isinstance(crits, list):
            errors.append(f"{where}: criteria должен быть массивом")
            continue
        got = [c.get("criterion_id") for c in crits if isinstance(c, dict)]
        for cid in spec:
            if got.count(cid) != 1:
                errors.append(f"{where}: критерий {cid} должен встречаться ровно один раз (встречается {got.count(cid)})")
        for cid in set(got) - set(spec):
            errors.append(f"{where}: неизвестный критерий {cid}")
        crit_value = None
        for c in crits:
            if not isinstance(c, dict) or c.get("criterion_id") not in spec:
                continue
            cfg = spec[c["criterion_id"]]
            v = c.get("value")
            if not cfg.is_allowed(v):
                errors.append(f"{where}: {cfg.criterion_id}={v!r} вне допустимого набора {cfg.allowed_values()}")
            if not isinstance(c.get("rationale"), str) or not c["rationale"].strip():
                warnings.append(f"{where}: {cfg.criterion_id} без rationale")
            turns = c.get("evidence_turns")
            if not isinstance(turns, list) or any(not isinstance(t, int) or isinstance(t, bool) or t < 1 for t in turns):
                errors.append(f"{where}: {cfg.criterion_id}.evidence_turns — список целых ≥ 1")
            elif not turns:
                warnings.append(f"{where}: {cfg.criterion_id} без evidence_turns")
            if cfg.is_critical_flag:
                crit_value = v
        score = r.get("score")
        if not isinstance(score, dict):
            errors.append(f"{where}: score должен быть объектом")
            continue
        fs = score.get("final_score")
        if fs is not None:
            if not isinstance(fs, (int, float)) or isinstance(fs, bool) or not (0 <= fs <= 100):
                errors.append(f"{where}: final_score должен быть числом 0..100")
            elif round(float(fs), 2) != float(fs):
                errors.append(f"{where}: final_score округляется до двух знаков")
        else:
            warnings.append(f"{where}: final_score = null (нет применимых критериев с весом)")
        if score.get("critical_flag") is not bool(crit_value):
            errors.append(f"{where}: critical_flag ({score.get('critical_flag')!r}) не равен value критерия critical ({crit_value!r})")
    return errors, warnings


def validate_file(path: str | Path, expect: int | None = None, expected_ids: list[str] | None = None,
                  checklist: ChecklistConfig | None = None) -> tuple[list[str], list[str]]:
    p = Path(path)
    raw = p.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ["Файл не в UTF-8"], []
    if text.startswith("﻿"):
        return ["Файл начинается с BOM — сохраните как UTF-8 без BOM"], []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [f"Невалидный JSON: {exc}"], []
    return validate_results(data, expect=expect, expected_ids=expected_ids, checklist=checklist)
