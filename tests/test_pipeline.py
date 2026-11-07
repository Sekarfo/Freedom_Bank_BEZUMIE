"""Конвейер целиком с mock-моделями (без сети), задачи, кэш, апелляции, экспорт."""
import asyncio

from app.brain.llm_common import LLMError
from app.brain.pipeline import Pipeline
from app.schemas import EvaluationOptions
from conftest import EXAMPLE_VALUES, SAMPLE_RECORD, MockClient, primary_json, review_json


def _pipeline(local: MockClient, cloud: MockClient) -> Pipeline:
    return Pipeline(lambda key: local, lambda provider: cloud)


def run(coro):
    return asyncio.run(coro)


def test_local_mode(checklist, conv):
    local = MockClient([primary_json(EXAMPLE_VALUES)])
    out = run(_pipeline(local, MockClient([], kind="cloud")).evaluate(conv, checklist, EvaluationOptions(mode="local")))
    assert out.status == "ok" and out.score == 85.38 and out.critical_flag is False
    assert out.model_name == "mock-model (local)"
    assert len(local.calls) == 1
    # схема требует все критерии и допустимые значения
    schema = local.calls[0]["schema"]
    assert set(schema["properties"]["criteria"]["required"]) == set(EXAMPLE_VALUES)


def test_hybrid_review_override(checklist, conv):
    cloud_vals = dict(EXAMPLE_VALUES, consultation=20)
    local = MockClient([primary_json(EXAMPLE_VALUES)])
    cloud = MockClient([review_json(EXAMPLE_VALUES, cloud_vals)], kind="cloud", model="claude-sonnet-5-5")
    out = run(_pipeline(local, cloud).evaluate(conv, checklist, EvaluationOptions(mode="hybrid")))
    assert out.status == "ok"
    by = {c["criterion_id"]: c for c in out.criteria}
    assert by["consultation"]["value"] == 20 and by["consultation"]["agreement"] == "override"
    assert out.score == 93.08  # 60.5 / 65
    assert out.model_name == "mock-model (local) + claude-sonnet-5-5"
    assert "Вердикты младшего оценщика" in cloud.calls[0]["user"]
    assert out.cost_usd == 0.001


def test_schema_error_retried_once(checklist, conv):
    bad = primary_json(dict(EXAMPLE_VALUES, dialogue=5))  # 5 нет в шкале
    local = MockClient([bad, primary_json(EXAMPLE_VALUES)])
    out = run(_pipeline(local, MockClient([], kind="cloud")).evaluate(conv, checklist, EvaluationOptions(mode="local")))
    assert out.status == "ok" and len(local.calls) == 2
    assert "Ошибка предыдущего ответа" in local.calls[1]["user"] and "dialogue" in local.calls[1]["user"]


def test_invalid_twice_then_error(checklist, conv):
    local = MockClient(["это не JSON", "{\"criteria\": 5}"])
    out = run(_pipeline(local, MockClient([], kind="cloud")).evaluate(conv, checklist, EvaluationOptions(mode="local")))
    assert out.status == "error" and out.criteria == [] and out.score is None
    assert len(local.calls) == 2


def test_cloud_compact_array_response(checklist, conv):
    """Облако получает компактную схему (массив) и отвечает массивом; недопустимое значение → один повтор."""
    import json

    def as_list(text, patch=None):
        data = json.loads(text)
        items = [dict(criterion_id=k, **v) for k, v in data["criteria"].items()]
        for it in items:
            it.update((patch or {}).get(it["criterion_id"], {}))
        data["criteria"] = items
        return json.dumps(data, ensure_ascii=False)

    good = as_list(review_json(EXAMPLE_VALUES, EXAMPLE_VALUES))
    bad = as_list(review_json(EXAMPLE_VALUES, EXAMPLE_VALUES), {"dialogue": {"value": 5}})
    cloud = MockClient([bad, good], kind="cloud")
    out = run(_pipeline(MockClient([primary_json(EXAMPLE_VALUES)]), cloud).evaluate(
        conv, checklist, EvaluationOptions(mode="hybrid")))
    assert out.status == "ok" and len(cloud.calls) == 2 and out.score == 85.38
    assert cloud.calls[0]["schema"]["properties"]["criteria"]["type"] == "array"
    assert all(c["agreement"] == "agree" for c in out.criteria)


def test_hybrid_local_down_cloud_becomes_primary(checklist, conv):
    local = MockClient([], fail=LLMError("Ollama недоступна"))
    cloud = MockClient([primary_json(EXAMPLE_VALUES)], kind="cloud")
    out = run(_pipeline(local, cloud).evaluate(conv, checklist, EvaluationOptions(mode="hybrid")))
    assert out.status == "ok" and out.score == 85.38
    assert all(c["agreement"] == "cloud_only" for c in out.criteria)
    assert "Вердикты младшего оценщика" not in cloud.calls[0]["user"]


def test_hybrid_cloud_down_local_only(checklist, conv):
    local = MockClient([primary_json(EXAMPLE_VALUES)])
    cloud = MockClient([], kind="cloud", fail=LLMError("Claude недоступен"))
    out = run(_pipeline(local, cloud).evaluate(conv, checklist, EvaluationOptions(mode="hybrid")))
    assert out.status == "ok" and out.needs_review is True
    assert all(c["agreement"] == "local_only" for c in out.criteria)


def test_blind_strategy_calls_arbiter_only_for_disputed(checklist, conv):
    cloud_vals = dict(EXAMPLE_VALUES, dialogue=4)

    def cloud_fn(system, user, schema):
        if "арбитр" in system:  # облачная схема — компактный массив, в enum только спорный критерий
            assert schema["properties"]["criteria"]["items"]["properties"]["criterion_id"]["enum"] == ["dialogue"]
            return primary_json({"dialogue": 7}).replace('"facts"', '"_facts"')
        return primary_json(cloud_vals)

    local = MockClient([primary_json(EXAMPLE_VALUES)])
    cloud = MockClient(cloud_fn, kind="cloud")
    out = run(_pipeline(local, cloud).evaluate(conv, checklist,
                                               EvaluationOptions(mode="hybrid", hybrid_strategy="blind")))
    assert out.status == "ok" and len(cloud.calls) == 2
    by = {c["criterion_id"]: c for c in out.criteria}
    assert by["dialogue"]["value"] == 7 and by["dialogue"]["agreement"] == "arbiter"


def test_cloud_mode_both_models_fail_gives_error(checklist, conv):
    cloud = MockClient([], kind="cloud", fail=LLMError("нет ключа"))
    out = run(_pipeline(MockClient([]), cloud).evaluate(conv, checklist, EvaluationOptions(mode="cloud")))
    assert out.status == "error" and "нет ключа" in out.error


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

    def local_fn(system, user, schema):
        return primary_json(EXAMPLE_VALUES)

    local = MockClient(local_fn)
    pipeline = _pipeline(local, MockClient([], kind="cloud"))
    opts = EvaluationOptions(mode="local")

    job_id = jobs.create_job(["test-ru-001"], ver_id, opts, force=False)
    job = run(jobs.run_job(job_id, pipeline))
    assert job["status"] == "done" and job["done"] == 1 and job["failed"] == 0

    # повторный запуск с тем же ключом — из кэша, модель не вызывается
    calls_before = len(local.calls)
    job2 = run(jobs.run_job(jobs.create_job(["test-ru-001"], ver_id, opts, force=False), pipeline))
    assert job2["from_cache"] == 1 and len(local.calls) == calls_before

    # второй диалог — модель падает → status error, запись не пропадает
    # (force: транскрипт тот же, что у test-ru-001, без него оценка была бы скопирована из кэша)
    failing = _pipeline(MockClient([], fail=LLMError("Ollama недоступна")), MockClient([], kind="cloud"))
    run(jobs.run_job(jobs.create_job(["test-ru-002"], ver_id, opts, force=True), failing))

    with session_scope() as s:
        ev = s.query(Evaluation).filter_by(conversation_id="test-ru-001").first()
        assert ev.final_score == 85.38
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
    assert data["model"] == {"mode": "local", "name": "mock-model (local)"}


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
    local = MockClient(lambda system, user, schema: primary_json(EXAMPLE_VALUES))
    pipeline = _pipeline(local, MockClient([], kind="cloud"))
    opts = EvaluationOptions(mode="local")
    run(jobs.run_job(jobs.create_job(["test-ru-001"], ver_id, opts, force=False), pipeline))
    with session_scope() as s:
        ev = s.query(Evaluation).filter_by(conversation_id="test-ru-001").one()
        appeal_id = create_appeal(s, ev.id, "dialogue", "Сбоя не было", "Оператор").id
    with session_scope() as s:
        decide_appeal(s, appeal_id, "change", 10, "Переспрашивать не пришлось", "ОКК")

    calls_before = len(local.calls)
    job = run(jobs.run_job(jobs.create_job(["prod-001"], ver_id, opts, force=False), pipeline))
    assert job["from_cache"] == 1 and job["failed"] == 0 and len(local.calls) == calls_before
    with session_scope() as s:
        copy = s.query(Evaluation).filter_by(conversation_id="prod-001").one()
        assert copy.status == "ok" and copy.cost_usd == 0.0
        assert copy.final_score == 85.38  # исходная оценка ИИ, а не 92.31 после чужой апелляции
        assert copy.audit["copied_from_conversation"] == "test-ru-001"
        assert all("review" not in c for c in copy.criteria)
        data = build_results(s, "PROD")
    assert [r["status"] for r in data["results"]] == ["ok"]
    assert data["results"][0]["score"]["final_score"] == 85.38
