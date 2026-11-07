"""Общие фикстуры: изолированная БД, синтетический диалог, mock-модели (без сети)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.adapters import normalize_record  # noqa: E402
from app.checklist.render import load_default_checklist  # noqa: E402

SAMPLE_RECORD: dict[str, Any] = {
    "conversation_id": "test-ru-001",
    "channel": "agent chat",
    "language": "RU",
    "audio_url": None,
    "transcript": [
        {"timestamp": "00:00", "speaker": "client", "text": "Где посмотреть мой тариф? В приложении не нахожу."},
        {"timestamp": "00:02", "speaker": "bot", "text": "Здравствуйте! Я AIM, AI-ассистент Freedom Broker."},
        {"timestamp": "00:38", "speaker": "system", "text": "Эскалация на оператора."},
        {"timestamp": "01:15", "speaker": "operator", "text": "Здравствуйте! Меня зовут Алина. Понимаю, что искать неудобно."},
        {"timestamp": "01:40", "speaker": "operator", "text": "В веб-версии откройте вкладку «Кабинет». Название тарифа будет под вашим ФИО."},
        {"timestamp": "02:05", "speaker": "client", "text": "Нашла, спасибо. А комиссия где?"},
        {"timestamp": "02:30", "speaker": "operator", "text": "Комиссия указана в разделе «Тарифы» на сайте."},
        {"timestamp": "02:50", "speaker": "operator", "text": "Если появятся вопросы, пишите. Хорошего вечера!"},
    ],
}

# Значения из примера в приложении к гайду → итог 85.38
EXAMPLE_VALUES: dict[str, Any] = {
    "contact": True, "discovery": None, "identification": None, "consultation": 15,
    "empathy": True, "dialogue": 7, "closure": True, "critical": False,
}
EXAMPLE_EVIDENCE: dict[str, tuple[list[int], str]] = {
    "contact": ([4], "Здравствуйте! Меня зовут Алина."),
    "discovery": ([1], "Где посмотреть мой тариф?"),
    "identification": ([1], "Где посмотреть мой тариф?"),
    "consultation": ([5, 7], "откройте вкладку «Кабинет»"),
    "empathy": ([4], "Понимаю, что искать неудобно."),
    "dialogue": ([5, 7], "Комиссия указана в разделе «Тарифы»"),
    "closure": ([8], "Если появятся вопросы, пишите. Хорошего вечера!"),
    "critical": ([4, 5, 7, 8], "Хорошего вечера!"),
}


@pytest.fixture
def checklist():
    return load_default_checklist()[0]


@pytest.fixture
def conv():
    return normalize_record(json.loads(json.dumps(SAMPLE_RECORD)), "TEST")


def primary_json(values: dict[str, Any], evidence: dict[str, tuple[list[int], str]] | None = None,
                 confidence: str = "high", overrides: dict[str, dict[str, Any]] | None = None) -> str:
    evidence = evidence or EXAMPLE_EVIDENCE
    crit = {}
    for cid, v in values.items():
        turns, quote = evidence.get(cid, ([1], ""))
        crit[cid] = {"evidence_turns": turns, "quote": quote, "rationale": f"Обоснование {cid}.",
                     "value": v, "confidence": confidence}
        crit[cid].update((overrides or {}).get(cid, {}))
    return json.dumps({"facts": {"client_request_turn": 1, "client_request_summary": "тариф",
                                 "protected_action_requested": False, "account_data_disclosed_turn": None,
                                 "verification_confirmed_turn": None, "complaint_present": False},
                       "criteria": crit}, ensure_ascii=False)


def review_json(local_values: dict[str, Any], final_values: dict[str, Any],
                evidence: dict[str, tuple[list[int], str]] | None = None, confidence: str = "high") -> str:
    evidence = evidence or EXAMPLE_EVIDENCE
    crit = {}
    for cid, v in final_values.items():
        turns, quote = evidence.get(cid, ([1], ""))
        same = local_values.get(cid) == v
        crit[cid] = {"decision": "confirm" if same else "override", "evidence_turns": turns, "quote": quote,
                     "rationale": f"Проверено {cid}.", "override_reason": "" if same else "Младший ошибся.",
                     "value": v, "confidence": confidence}
    return json.dumps({"criteria": crit}, ensure_ascii=False)


class MockClient:
    """Mock LLM: отдаёт заранее заданные ответы (по очереди) или вызывает функцию(system, user, schema)."""

    def __init__(self, responses: list[str] | Callable[[str, str, dict], str], kind: str = "local",
                 provider: str = "mock", model: str = "mock-model", fail: Exception | None = None):
        self.responses = responses
        self.kind, self.provider, self.model, self.display_name = kind, provider, model, model
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def complete_json(self, system: str, user: str, schema: dict, schema_name: str):
        from app.brain.llm_common import LLMResponse

        self.calls.append({"system": system, "user": user, "schema": schema, "schema_name": schema_name})
        if self.fail is not None:
            raise self.fail
        if callable(self.responses):
            text = self.responses(system, user, schema)
        else:
            text = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        return LLMResponse(text=text, model=self.model, usage={"input_tokens": 100, "output_tokens": 50},
                           cost_usd=0.001 if self.kind == "cloud" else 0.0)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Отдельная SQLite-база на тест."""
    from app import config, db

    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("RAW_DATA_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("ORGANIZERS_API_URL", "")
    config.get_settings.cache_clear()
    db.configure(url)
    yield url
    config.get_settings.cache_clear()
