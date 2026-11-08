"""API интерфейса (/api/*, React-фронтенд): сквозной сценарий так, как его выполняет интерфейс."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from conftest import EXAMPLE_VALUES, SAMPLE_RECORD, MockClient, judge_json


def _judge_text(system, user, schema):
    data = json.loads(judge_json(EXAMPLE_VALUES))
    data["reason"] = {"primary": "fees.tariff", "secondary": [], "other_description": "", "resolution": "resolved"}
    return json.dumps(data, ensure_ascii=False)


@pytest.fixture
def ui(temp_db, monkeypatch, tmp_path):
    from app import jobs
    from app.api import ui as ui_api
    from app.main import app

    monkeypatch.setattr(jobs, "_client_factory", lambda provider, model=None: MockClient(_judge_text, model=model or "mock"))
    monkeypatch.setattr(ui_api, "REPO_ROOT", tmp_path)          # results/ пишется во временную папку
    with TestClient(app) as c:
        yield c


def _wait(c, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        jobs = c.get("/api/jobs").json()
        if jobs and all(j["status"] in ("done", "error") for j in jobs):
            return jobs
        time.sleep(0.05)
    raise AssertionError("оценка не завершилась")


def _imported(c, records=None):
    r = c.post("/api/import", json={"data": {"records": records or [SAMPLE_RECORD]}, "groupId": "DEV"})
    assert r.status_code == 200, r.text
    return r.json()


def test_health_and_errors(ui):
    h = ui.get("/api/health").json()
    assert isinstance(h["aiConfigured"], bool) and h["model"] and {"name": h["model"]} in h["models"]
    assert h["masking"]["enabled"] is True and "setup" not in h            # NER загружен → уведомления нет
    r = ui.get("/api/conversations/nope")
    assert r.status_code == 404 and "не найден" in r.json()["error"]
    assert ui.post("/api/evaluations", json={"conversationIds": []}).json()["error"]
    r = ui.post("/api/import/remote", json={"groupId": "DEV"})               # URL организаторов не задан
    assert r.status_code == 502 and r.json()["error"]


def test_full_ui_flow(ui):
    assert _imported(ui) == {"imported": 1, "skipped": 0, "added": 1, "updated": 0, "errors": [], "groupId": "DEV"}
    assert _imported(ui)["skipped"] == 1                                   # повторный импорт — без изменений

    items = ui.get("/api/conversations").json()
    assert isinstance(items, list) and items[0] | {"subject": None} == {
        "id": "test-ru-001", "externalId": "test-ru-001", "groupId": "DEV", "subject": None, "messageCount": 8,
        "channel": "agent chat", "language": "RU", "latestEvaluation": None}
    assert items[0]["subject"].startswith("Где посмотреть мой тариф")

    cls = ui.get("/api/checklists").json()
    cl = cls[0]
    assert isinstance(cl["id"], str) and cl["version"] == 1 and len(cl["criteria"]) == 8
    types = {c["id"]: c["type"] for c in cl["criteria"]}
    assert types["critical"] == "flag" and types["consultation"] == "scale" and types["contact"] == "boolean"
    assert all(c["description"] for c in cl["criteria"])

    jobs = ui.post("/api/evaluations", json={"conversationIds": ["test-ru-001"], "checklistId": cl["id"],
                                             "model": "claude-sonnet-5-5"}).json()
    assert [j["status"] for j in jobs] == ["queued"] and jobs[0]["conversationId"] == "test-ru-001"
    done = _wait(ui)
    assert done[0]["status"] == "done" and done[0]["checklistId"] == cl["id"]

    item = ui.get("/api/conversations").json()[0]["latestEvaluation"]
    assert item == {"total": 85.38, "grossErrors": False, "needsReview": False, "checklistId": cl["id"]}

    conv = ui.get("/api/conversations/test-ru-001").json()
    assert [m["id"] for m in conv["messages"]] == [f"m{i}" for i in range(1, 9)]
    assert conv["messages"][3] == {"id": "m4", "speaker": "operator", "timestamp": "01:15", "offsetSeconds": 75.0,
                                   "text": "Здравствуйте! Меня зовут Алина. Понимаю, что искать неудобно."}
    assert conv["warnings"] == [] and conv["audioUrl"] is None
    ev = conv["evaluations"][0]
    assert ev["model"] == "claude-sonnet-5-5" and ev["checklist"]["id"] == cl["id"] and ev["appeals"] == []
    assert ev["score"]["total"] == 85.38 and ev["finalScore"]["total"] == 85.38
    assert "Тарифы и комиссии → Тарифный план" in ev["summary"] and "имя, фамилия или отчество — 1" in ev["summary"]
    rows = {r["criterionId"]: r for r in ev["finalScore"]["rows"]}
    assert rows["contact"]["value"] == "yes" and rows["discovery"]["value"] == "na"
    assert rows["consultation"]["value"] == 15 and rows["consultation"]["fraction"] == 0.75
    assert rows["critical"]["weight"] == 0 and rows["critical"]["fraction"] is None
    assert round(sum(r["weightedPoints"] or 0 for r in rows.values()), 2) == 85.38   # разбивка сходится с итогом
    assert all(e in {m["id"] for m in conv["messages"]} for r in rows.values() for e in r["evidence"])
    assert [s["name"] for s in ev["finalScore"]["sections"]]
    r = ui.get(f"/api/evaluations/{ev['id']}/export")
    assert "attachment" in r.headers["content-disposition"] and r.json()["result"]["score"]["final_score"] == 85.38

    # апелляция → изменение оценки → пересчёт; исходная оценка ИИ сохраняется
    ui.post("/api/appeals", json={"evaluationId": ev["id"], "criterionId": "empathy", "comment": "Сочувствия не было"})
    ui.post("/api/appeals", json={"evaluationId": ev["id"], "criterionId": "contact", "comment": "Не согласен"})
    appeals = ui.get("/api/appeals").json()
    assert [a["status"] for a in appeals] == ["open", "open"] and appeals[1]["criterionTitle"]
    emp = next(a for a in appeals if a["criterionId"] == "empathy")
    decision = {"criterionId": "empathy", "value": "no", "rationale": "Шаблонная вежливость без сочувствия.",
                "evidence": ["m4"], "checkedRange": None, "needsReview": False, "reviewReason": None}
    assert ui.post(f"/api/appeals/{emp['id']}/resolve", json={"resolution": "Согласен с оператором",
                                                              "decision": decision}).status_code == 200
    con = next(a for a in appeals if a["criterionId"] == "contact")
    ui.post(f"/api/appeals/{con['id']}/resolve", json={"resolution": "Оценка верна", "decision": None})
    statuses = {a["criterionId"]: a["status"] for a in ui.get("/api/appeals").json()}
    assert statuses == {"empathy": "changed", "contact": "confirmed"}
    ev2 = ui.get("/api/conversations/test-ru-001").json()["evaluations"][0]
    rows2 = {r["criterionId"]: r for r in ev2["finalScore"]["rows"]}
    assert ev2["score"]["total"] == 85.38 and ev2["finalScore"]["total"] < 85.38
    assert rows2["empathy"]["value"] == "no" and rows2["empathy"]["rationale"].startswith("Шаблонная")
    assert {a["status"] for a in ev2["appeals"]} == {"changed", "confirmed"}

    # ручная разметка → сравнение ИИ с ней
    human = [{"criterionId": cid, "value": {True: "yes", False: "no", None: "na"}.get(v, v), "rationale": "Разметка.",
              "evidence": ["m4"], "checkedRange": None, "needsReview": False, "reviewReason": None}
             for cid, v in dict(EXAMPLE_VALUES, empathy=False).items()]
    bad = ui.post("/api/annotations", json={"conversationId": "test-ru-001", "checklistId": cl["id"],
                                            "status": "approved", "split": "validation", "scenarioGroup": "тарифы",
                                            "reviewer": "ОКК", "notes": "", "decisions": human[:3]})
    assert bad.status_code == 422 and "разметьте все критерии" in bad.json()["error"]
    assert ui.post("/api/annotations", json={"conversationId": "test-ru-001", "checklistId": cl["id"],
                                             "status": "approved", "split": "validation", "scenarioGroup": "тарифы",
                                             "reviewer": "ОКК", "notes": "", "decisions": human}).status_code == 200
    ann = ui.get("/api/annotations").json()
    assert len(ann) == 1 and ann[0]["status"] == "approved" and ann[0]["checklistId"] == cl["id"]
    exp = ui.get("/api/experiments").json()
    m = exp[0]["models"][0]
    assert m["model"] == "claude-sonnet-5-5" and m["approved"] == 1 and m["succeeded"] == 1
    assert m["criterionAccuracy"] == 0.875 and m["perCriterion"]["empathy"] == {"correct": 0, "total": 1}
    assert m["perLanguage"]["RU"] == {"correct": 7, "total": 8} and m["totalScoreMAE"] > 0
    assert exp[0]["disagreements"] == [{"externalId": "test-ru-001", "criterionId": "empathy",
                                        "values": [{"model": "Разметка (эталон)", "value": "no"},
                                                   {"model": "claude-sonnet-5-5", "value": "yes"}]}]

    # сдача results.json
    assert ui.get("/api/submission/config").json() == {"teamName": "Freedom_Bank_BEZUMIE", "groups": [{"id": "DEV"}]}
    body = {"teamName": "Freedom_Bank_BEZUMIE", "split": "dev", "groupId": "DEV", "model": "claude-sonnet-5-5",
            "checklistId": cl["id"]}
    pv = ui.post("/api/submission/preview", json=body).json()
    assert (pv["total"], pv["ok"], pv["errors"]) == (1, 1, 0) and pv["validationErrors"] == []
    assert pv["file"]["results"][0]["score"]["final_score"] == ev2["finalScore"]["total"]   # с решением апелляции
    sv = ui.post("/api/submission/save", json=body).json()
    assert sv == {"path": "results/dev/results.json", "ok": 1, "errors": 0}
    prod = ui.post("/api/submission/save", json=dict(body, split="prod"))
    assert prod.status_code == 422 and "10" in prod.json()["error"]           # PROD — ровно 10 записей


def test_checklist_edit_from_ui(ui):
    cl = ui.get("/api/checklists").json()[0]
    edited = json.loads(json.dumps(cl))
    for c in edited["criteria"]:
        if c["id"] == "empathy":
            c["weight"] = 15
        if c["id"] == "dialogue":
            c["description"] = "Новое описание критерия."
    edited["criteria"].append({"id": "custom_1731000000000", "title": "Раскрыты риски", "section": "Комплаенс",
                               "description": "Оператор предупредил о рисках инвестиций.", "type": "boolean",
                               "weight": 5, "allowNA": True, "naRule": "Не было разговора об инвестициях.",
                               "scale": []})
    new = ui.post("/api/checklists", json=edited).json()
    assert new["version"] == 2 and new["id"] != cl["id"] and len(new["criteria"]) == 9
    assert ui.get("/api/checklists").json()[0]["id"] == new["id"]               # новая версия — первой
    v1 = {c["criterion_id"]: c for c in ui.get(f"/api/v1/checklists/versions/{cl['id']}").json()["criteria"]}
    v2 = {c["criterion_id"]: c for c in ui.get(f"/api/v1/checklists/versions/{new['id']}").json()["criteria"]}
    assert v2["empathy"]["weight"] == 15 and v2["empathy"]["yes_definition"] == v1["empathy"]["yes_definition"]
    assert v2["dialogue"]["guidance"] == "Новое описание критерия." and v2["dialogue"]["yes_definition"] == ""
    assert v2["custom_1731000000000"]["na_allowed"] and v2["custom_1731000000000"]["section"] == "Комплаенс"

    bad = json.loads(json.dumps(cl))
    bad["criteria"][0]["description"] = ""
    r = ui.post("/api/checklists", json=bad)
    assert r.status_code == 422 and "Опишите критерий" in r.json()["error"]


def test_legacy_api_still_available(ui):
    _imported(ui)
    assert ui.get("/api/v1/health").json()["status"] == "ok"
    assert ui.get("/api/v1/conversations").json()["total"] == 1
    assert ui.get("/api/v1/reasons/taxonomy").json()["version"] == 1
