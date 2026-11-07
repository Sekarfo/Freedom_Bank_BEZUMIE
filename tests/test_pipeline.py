"""Конвейер целиком с mock-моделью (без сети), задачи, кэш, апелляции, экспорт."""
import asyncio

from app.brain.llm_common import LLMError
from app.brain.pipeline import Pipeline
from app.schemas import EvaluationOptions
from conftest import EXAMPLE_VALUES, SAMPLE_RECORD, MockClient, judge_json

OPTS = EvaluationOptions(provider="anthropic")


def _pipeline(client: MockClient) -> Pipeline:
    return Pipeline(lambda provider: client)


def run(coro):
    return asyncio.run(coro)


def test_judge_ok(checklist, conv):
    judge = MockClient([judge_json(EXAMPLE_VALUES)], model="claude-opus-5-5")
    out = run(_pipeline(judge).evaluate(conv, checklist, OPTS))
    assert out.status == "ok" and out.score == 85.38 and out.critical_flag is False
    assert out.model_name == "claude-opus-5-5" and out.cost_usd == 0.001
    assert len(judge.calls) == 1
    # один вызов: чек-лист в системном промпте, транскрипт в пользовательском, схема — массив критериев
    call = judge.calls[0]
    assert "contact" in call["system"] and "Где посмотреть мой тариф?" in call["user"]
    items = call["schema"]["properties"]["criteria"]
    assert items["type"] == "array"
    assert set(items["items"]["properties"]["criterion_id"]["enum"]) == set(EXAMPLE_VALUES)
    by = {c["criterion_id"]: c for c in out.criteria}
    assert by["contact"]["evidence_status"] == "verified" and by["contact"]["needs_review"] is False
    assert set(out.audit["stages"]) == {"preprocess", "judge", "decision", "scoring"}


def test_invalid_value_retried_once(checklist, conv):
    bad = judge_json(dict(EXAMPLE_VALUES, dialogue=5))  # 5 нет в шкале
    judge = MockClient([bad, judge_json(EXAMPLE_VALUES)])
    out = run(_pipeline(judge).evaluate(conv, checklist, OPTS))
    assert out.status == "ok" and len(judge.calls) == 2 and out.score == 85.38
    assert "Ошибка предыдущего ответа" in judge.calls[1]["user"] and "dialogue" in judge.calls[1]["user"]


def test_invalid_twice_then_error(checklist, conv):
    judge = MockClient(["это не JSON", "{\"criteria\": 5}"])
    out = run(_pipeline(judge).evaluate(conv, checklist, OPTS))
    assert out.status == "error" and out.criteria == [] and out.score is None
    assert len(judge.calls) == 2


def test_model_unavailable_gives_error(checklist, conv):
    judge = MockClient([], fail=LLMError("нет ключа"))
    out = run(_pipeline(judge).evaluate(conv, checklist, OPTS))
    assert out.status == "error" and "нет ключа" in out.error and out.score is None


def test_job_cache_appeal_and_export(temp_db):
    """Сквозной сценарий: загрузка → оценка (задача) → кэш → апелляция → пересчёт → экспорт → валидатор."""
    from app import jobs
    from app.appeals.service import create_appeal, decide_appeal
    from app.checklist.service import latest_version
    from app.db import session_scope
    from app.export.results_json import build_results
    from app.export.validator import validate_results
    from app.models import Evaluation
    from app.store import upsert_conversations

    error_record = dict(SAMPLE_RECORD, conversation_id="test-ru-002")
    with session_scope() as s:
        res = upsert_conversations(s, {"data": {"records": [SAMPLE_RECORD, error_record]}}, "DEV")
        ver_id = latest_version(s).id
    assert res["added"] == 2

    judge = MockClient(lambda system, user, schema: judge_json(EXAMPLE_VALUES), model="claude-opus-5-5")
    pipeline = _pipeline(judge)

    job_id = jobs.create_job(["test-ru-001"], ver_id, OPTS, force=False)
    job = run(jobs.run_job(job_id, pipeline))
    assert job["status"] == "done" and job["done"] == 1 and job["failed"] == 0

    # повторный запуск с тем же ключом — из кэша, модель не вызывается
    calls_before = len(judge.calls)
    job2 = run(jobs.run_job(jobs.create_job(["test-ru-001"], ver_id, OPTS, force=False), pipeline))
    assert job2["from_cache"] == 1 and len(judge.calls) == calls_before

    # второй диалог — модель падает → status error, запись не пропадает
    # (force: транскрипт тот же, что у test-ru-001, без него оценка была бы скопирована из кэша)
    failing = _pipeline(MockClient([], fail=LLMError("Claude недоступен")))
    run(jobs.run_job(jobs.create_job(["test-ru-002"], ver_id, OPTS, force=True), failing))

    with session_scope() as s:
        ev = s.query(Evaluation).filter_by(conversation_id="test-ru-001").first()
        assert ev.final_score == 85.38 and ev.model == "claude-opus-5-5"
        appeal = create_appeal(s, ev.id, "dialogue", "Сбоя не было", "Оператор")
        appeal_id, ev_id = appeal.id, ev.id
    with session_scope() as s:
        decide_appeal(s, appeal_id, "change", 10, "Ответы ясные, переспрашивать не пришлось", "ОКК")
    with session_scope() as s:
        ev = s.get(Evaluation, ev_id)
        crit = next(c for c in ev.criteria if c["criterion_id"] == "dialogue")
        assert crit["ai_value"] == 7 and crit["value"] == 10 and crit["review"]["reason"]
        assert ev.ai_score == 85.38 and ev.final_score == 92.31  # (55.5 + 4.5) / 65
        data = build_results(s, "DEV")
        data_ai = build_results(s, "DEV", use_final=False)

    errors, warnings = validate_results(data, expect=2)
    assert errors == []
    recs = {r["conversation_id"]: r for r in data["results"]}
    assert recs["test-ru-001"]["score"] == {"final_score": 92.31, "critical_flag": False}
    assert recs["test-ru-002"]["status"] == "error" and recs["test-ru-002"]["criteria"] == []
    assert recs["test-ru-002"]["score"] is None and recs["test-ru-002"]["error"]
    ai = {r["conversation_id"]: r for r in data_ai["results"]}
    assert ai["test-ru-001"]["score"]["final_score"] == 85.38
    assert data["model"]["mode"] == "external_api" and "claude-opus-5-5" in data["model"]["name"]


def test_appeal_confirm_keeps_value(temp_db):
    """Подтверждение оценки проверяющим: значение то же, итог тот же, апелляция закрыта."""
    from app import jobs
    from app.appeals.service import create_appeal, decide_appeal
    from app.checklist.service import latest_version
    from app.db import session_scope
    from app.models import Evaluation
    from app.store import upsert_conversations

    with session_scope() as s:
        upsert_conversations(s, {"data": {"records": [SAMPLE_RECORD]}}, "DEV")
        ver_id = latest_version(s).id
    pipeline = _pipeline(MockClient(lambda system, user, schema: judge_json(EXAMPLE_VALUES)))
    run(jobs.run_job(jobs.create_job(["test-ru-001"], ver_id, OPTS, force=False), pipeline))
    with session_scope() as s:
        ev = s.query(Evaluation).filter_by(conversation_id="test-ru-001").one()
        appeal_id, ev_id = create_appeal(s, ev.id, "empathy", "Не согласен", "Оператор").id, ev.id
    with session_scope() as s:
        decide_appeal(s, appeal_id, "confirm", None, "Признание неудобства есть в [4]", "ОКК")
    with session_scope() as s:
        ev = s.get(Evaluation, ev_id)
        crit = next(c for c in ev.criteria if c["criterion_id"] == "empathy")
        assert crit["value"] is True and crit["review"]["action"] == "confirm" and crit["appeal_status"] == "resolved"
        assert ev.final_score == 85.38


def test_cache_hit_from_other_conversation_creates_own_evaluation(temp_db):
    """Тот же транскрипт под другим ID (PROD повторяет DEV): оценка копируется на новый ID без вызова модели,
    без чужих решений по апелляциям, и в results.json диалог выгружается как ok, а не «не оценён»."""
    from app import jobs
    from app.appeals.service import create_appeal, decide_appeal
    from app.checklist.service import latest_version
    from app.db import session_scope
    from app.export.results_json import build_results
    from app.models import Evaluation
    from app.store import upsert_conversations

    with session_scope() as s:
        upsert_conversations(s, {"data": {"records": [SAMPLE_RECORD]}}, "DEV")
        upsert_conversations(s, {"data": {"records": [dict(SAMPLE_RECORD, conversation_id="prod-001")]}}, "PROD")
        ver_id = latest_version(s).id
    judge = MockClient(lambda system, user, schema: judge_json(EXAMPLE_VALUES))
    pipeline = _pipeline(judge)
    run(jobs.run_job(jobs.create_job(["test-ru-001"], ver_id, OPTS, force=False), pipeline))
    with session_scope() as s:
        ev = s.query(Evaluation).filter_by(conversation_id="test-ru-001").one()
        appeal_id = create_appeal(s, ev.id, "dialogue", "Сбоя не было", "Оператор").id
    with session_scope() as s:
        decide_appeal(s, appeal_id, "change", 10, "Переспрашивать не пришлось", "ОКК")

    calls_before = len(judge.calls)
    job = run(jobs.run_job(jobs.create_job(["prod-001"], ver_id, OPTS, force=False), pipeline))
    assert job["from_cache"] == 1 and job["failed"] == 0 and len(judge.calls) == calls_before
    with session_scope() as s:
        copy = s.query(Evaluation).filter_by(conversation_id="prod-001").one()
        assert copy.status == "ok" and copy.cost_usd == 0.0
        assert copy.final_score == 85.38  # исходная оценка ИИ, а не 92.31 после чужой апелляции
        assert copy.audit["copied_from_conversation"] == "test-ru-001"
        assert all("review" not in c for c in copy.criteria)
        data = build_results(s, "PROD")
    assert [r["status"] for r in data["results"]] == ["ok"]
    assert data["results"][0]["score"]["final_score"] == 85.38
