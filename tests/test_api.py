"""Сквозной сценарий через HTTP API (mock-модель вместо Claude)."""
import time

import pytest
from fastapi.testclient import TestClient

from conftest import EXAMPLE_VALUES, SAMPLE_RECORD, MockClient, judge_json


@pytest.fixture
def client(temp_db, monkeypatch):
    from app import jobs
    from app.main import app

    judge = MockClient(lambda s, u, sc: judge_json(EXAMPLE_VALUES), model="claude-opus-5-5")
    monkeypatch.setattr(jobs, "_client_factory", lambda provider: judge)
    with TestClient(app) as c:
        yield c


def _wait_job(client, job_id, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("задача не завершилась")


def test_full_flow(client):
    r = client.post("/api/conversations/upload", json={"dataset": "DEV", "payload": {"records": [SAMPLE_RECORD]}})
    assert r.status_code == 200 and r.json()["added"] == 1

    items = client.get("/api/conversations").json()["items"]
    assert items[0]["conversation_id"] == "test-ru-001" and items[0]["evaluation"] is None

    r = client.post("/api/evaluations", json={"conversation_ids": ["test-ru-001"]})
    assert r.status_code == 200, r.text
    job = _wait_job(client, r.json()["job_id"])
    assert job["status"] == "done" and job["failed"] == 0

    card = client.get("/api/conversations/test-ru-001").json()
    ev = card["evaluation"]
    assert ev["final_score"] == 85.38 and ev["model_name"] == "claude-opus-5-5"
    assert len(card["conversation"]["turns"]) == 8

    # апелляция → решение → пересчёт
    r = client.post("/api/appeals", json={"evaluation_id": ev["id"], "criterion_id": "consultation",
                                          "comment": "Вопрос про комиссию закрыт полностью"})
    assert r.status_code == 200
    queue = client.get("/api/appeals?status=open").json()
    assert len(queue) == 1 and queue[0]["fragment"]
    r = client.post(f"/api/appeals/{queue[0]['id']}/decision",
                    json={"action": "change", "new_value": 20, "reason": "Оба вопроса закрыты"})
    assert r.status_code == 200 and r.json()["evaluation"]["final_score"] == 93.08  # 60.5 / 65
    bad = client.post(f"/api/appeals/{queue[0]['id']}/decision", json={"action": "confirm", "reason": "x"})
    assert bad.status_code == 400

    # новая версия чек-листа с дополнительным критерием — применяется без правки кода
    versions = client.get("/api/checklists").json()[0]["versions"]
    base = client.get(f"/api/checklists/versions/{versions[0]['id']}").json()
    criteria = base["criteria"] + [{
        "criterion_id": "personal_name", "title": "Обращение по имени", "section": "Ведение и завершение диалога",
        "weight": 5, "type": "binary", "na_allowed": True, "yes_definition": "Оператор обратился по имени",
        "no_definition": "Не обратился", "na_condition": "Имя неизвестно"}]
    r = client.post("/api/checklists/1/versions", json={"criteria": criteria, "note": "Новый критерий"})
    assert r.status_code == 200, r.text
    new_ver = r.json()["id"]
    prompt = client.get(f"/api/checklists/versions/{new_ver}/prompt").json()["text"]
    assert "personal_name" in prompt
    diff = client.get(f"/api/checklists/diff/{versions[0]['id']}/{new_ver}").json()
    assert [c["criterion_id"] for c in diff["added"]] == ["personal_name"]

    # экспорт
    exp = client.get("/api/export/results?dataset=DEV").json()
    assert exp["schema_version"] == "1.0" and exp["results"][0]["score"]["final_score"] == 93.08
    val = client.get("/api/export/validate?dataset=DEV&expect=1").json()
    assert val["errors"] == []

    summary = client.get("/api/analytics/summary").json()
    assert summary["ok"] == 1 and summary["appeals"]["changed"] == 1

    st = client.get("/api/system/status").json()
    assert set(st["providers"]) == {"anthropic", "openai"}
    settings = client.get("/api/settings").json()
    assert settings["provider"] == "anthropic" and settings["model"]


def test_invalid_checklist_rejected(client):
    r = client.post("/api/checklists/1/versions", json={"criteria": [
        {"criterion_id": "x1", "title": "Шкала", "type": "scale", "scale": [{"value": 5}], "weight": 10}]})
    assert r.status_code == 422
