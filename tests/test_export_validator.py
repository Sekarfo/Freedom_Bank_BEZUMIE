"""Валидатор results.json на фиктивном наборе, включая запись с ошибкой."""
import copy
import json

from app.export.validator import validate_file, validate_results
from conftest import EXAMPLE_VALUES

GOOD = {
    "schema_version": "1.0",
    "team_name": "Команда",
    "model": {"mode": "external_api", "name": "claude-opus-5-5"},
    "results": [
        {"conversation_id": "demo-001", "status": "ok",
         "criteria": [{"criterion_id": k, "value": v, "rationale": "ok", "evidence_turns": [1]}
                      for k, v in EXAMPLE_VALUES.items()],
         "score": {"final_score": 85.38, "critical_flag": False}},
        {"conversation_id": "demo-002", "status": "error", "criteria": [], "score": None,
         "error": "Не удалось получить ответ модели."},
    ],
}


def test_good_file_passes():
    errors, _ = validate_results(GOOD, expect=2)
    assert errors == []


def test_detects_problems():
    bad = copy.deepcopy(GOOD)
    bad["results"][0]["criteria"][5]["value"] = 5               # dialogue вне шкалы
    bad["results"][0]["criteria"].pop(0)                         # нет contact
    bad["results"][0]["score"]["critical_flag"] = True           # не равен critical.value
    bad["results"][1]["score"] = 0                               # ошибка подменена нулём
    bad["results"].append(copy.deepcopy(bad["results"][1]))      # дубликат ID
    errors, _ = validate_results(bad, expect=2)
    text = "\n".join(errors)
    assert "dialogue=5" in text and "contact" in text and "critical_flag" in text
    assert "score должен быть null" in text and "Повторяющиеся" in text and "Ожидалось 2" in text


def test_na_only_where_allowed():
    bad = copy.deepcopy(GOOD)
    bad["results"][0]["criteria"][0]["value"] = None  # contact N/A запрещён
    errors, _ = validate_results(bad)
    assert any("contact=None" in e for e in errors)


def test_file_checks(tmp_path):
    p = tmp_path / "results.json"
    p.write_text(json.dumps(GOOD, ensure_ascii=False), encoding="utf-8")
    assert validate_file(p, expect=2)[0] == []
    p.write_text(json.dumps(GOOD, ensure_ascii=False) + ",", encoding="utf-8")
    assert "Невалидный JSON" in validate_file(p)[0][0]
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps(GOOD).encode())
    assert "BOM" in validate_file(p)[0][0]
