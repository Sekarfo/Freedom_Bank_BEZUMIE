"""Причины обращений: справочник, классификация в ответе модели, хранение, аналитика, поиск новых подтем, API."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.adapters import normalize_record
from app.brain.pipeline import Pipeline, cache_key
from app.brain.prompts import system_prompt
from app.checklist.render import output_schema
from app.reasons.taxonomy import OTHER, ReasonTaxonomy, load_default_taxonomy, normalize_reason
from app.schemas import EvaluationOptions
from conftest import EXAMPLE_VALUES, SAMPLE_RECORD, MockClient, judge_json

OPTS = EvaluationOptions(provider="anthropic")


@pytest.fixture
def tax():
    return load_default_taxonomy()[0]


def with_reason(values=None, primary="fees.tariff", secondary=(), resolution="resolved", other="", overrides=None):
    data = json.loads(judge_json(values or EXAMPLE_VALUES, overrides=overrides))
    data["reason"] = {"primary": primary, "secondary": list(secondary), "other_description": other,
                      "resolution": resolution}
    return json.dumps(data, ensure_ascii=False)


# --------------------------------------------------------------------------- справочник
def test_default_taxonomy(tax):
    codes = tax.codes()
    assert OTHER in codes and "money.withdrawal" in codes and len(codes) == len(set(codes))
    assert tax.title_of("money.withdrawal") == "Деньги → Вывод средств" and tax.title_of("money") == "Деньги"


def test_taxonomy_validation():
    t = ReasonTaxonomy(topics=[{"code": "money", "title": "Деньги", "subtopics": [{"code": "deposit", "title": "П"}]}])
    assert OTHER in t.codes()                                  # other.other добавляется сам
    with pytest.raises(ValueError):
        ReasonTaxonomy(topics=[{"code": "a1", "title": "A", "subtopics": [{"code": "x1", "title": "X"},
                                                                          {"code": "x1", "title": "Y"}]}])
    with pytest.raises(ValueError):
        ReasonTaxonomy(topics=[{"code": "Bad Code", "title": "A", "subtopics": [{"code": "x1", "title": "X"}]}])
    with pytest.raises(ValueError):
        ReasonTaxonomy(topics=[{"code": "a1", "title": "A", "subtopics": []}])


@pytest.mark.parametrize("raw, primary, issues", [
    ({"primary": "money.withdrawal", "secondary": [], "other_description": "", "resolution": "resolved"},
     "money.withdrawal", 0),
    ({"primary": "withdrawal", "secondary": [], "other_description": "", "resolution": "resolved"},
     "money.withdrawal", 0),                                   # только подтема, уникальная по справочнику
    ({"primary": "money.crypto", "secondary": [], "other_description": "", "resolution": "resolved"}, OTHER, 1),
    (None, OTHER, 1),
])
def test_normalize_reason(tax, raw, primary, issues):
    r = normalize_reason(raw, tax)
    assert r["primary"] == primary and len(r["issues"]) == issues
    assert r["topic"] == primary.split(".")[0] and r["resolution"] in ("resolved", "unresolved")


def test_normalize_secondary_and_other(tax):
    r = normalize_reason({"primary": "money.withdrawal", "secondary": ["money.withdrawal", "fees.charges", "fees.charges",
                                                                         "nope.nope"],
                          "other_description": "не должно сохраниться", "resolution": "ESCALATED"}, tax)
    assert r["secondary"] == ["fees.charges"] and r["resolution"] == "escalated" and r["other_description"] == ""
    assert len(r["issues"]) == 1
    o = normalize_reason({"primary": OTHER, "secondary": [], "other_description": "  вопрос  про   крипту ",
                          "resolution": "maybe"}, tax)
    assert o["other_description"] == "вопрос про крипту" and o["resolution"] == "unresolved" and o["issues"]


# --------------------------------------------------------------------------- промпт и схема
def test_prompt_and_schema(checklist, tax):
    sp = system_prompt(checklist, tax)
    assert "# Причина обращения" in sp and "`money.withdrawal`" in sp and "`escalated`" in sp and "{{" not in sp
    plain = system_prompt(checklist)
    assert "Причина обращения" not in plain and "{{" not in plain
    schema = output_schema(checklist, tax)
    assert list(schema["properties"]) == ["facts", "reason", "criteria"]     # причина — до критериев
    assert set(schema["properties"]["reason"]["properties"]["primary"]["enum"]) == set(tax.codes())
    assert "reason" not in output_schema(checklist)["properties"]


def test_pipeline_classifies_reason(checklist, conv, tax):
    judge = MockClient([with_reason(primary="fees.tariff", secondary=["fees.charges"])])
    out = asyncio.run(Pipeline(lambda p: judge).evaluate(conv, checklist, OPTS, tax))
    assert out.status == "ok" and out.score == 85.38                      # баллы причина не меняет
    assert out.reason["primary"] == "fees.tariff" and out.reason["secondary"] == ["fees.charges"]
    assert out.reason["request_summary"] == "тариф" and out.audit["stages"]["reason"]["issues"] == []
    assert "reason" in judge.calls[0]["schema"]["properties"]


def test_bad_reason_does_not_fail_evaluation(checklist, conv, tax):
    judge = MockClient([judge_json(EXAMPLE_VALUES)])                      # блока reason нет вовсе
    out = asyncio.run(Pipeline(lambda p: judge).evaluate(conv, checklist, OPTS, tax))
    assert out.status == "ok" and out.reason["primary"] == OTHER and out.reason["issues"]
    assert len(judge.calls) == 1                                           # без повтора


def test_cache_key_depends_on_taxonomy(checklist, conv, tax):
    k0 = cache_key(conv, checklist, "anthropic", "m")
    k1 = cache_key(conv, checklist, "anthropic", "m", tax)
    changed = ReasonTaxonomy(topics=tax.topics_dump() + [{"code": "crypto", "title": "Крипто",
                                                          "subtopics": [{"code": "buy", "title": "Покупка"}]}])
    assert len({k0, k1, cache_key(conv, checklist, "anthropic", "m", changed)}) == 3


# --------------------------------------------------------------------------- хранение и аналитика
WITHDRAW = "Не могу вывести деньги, кнопка серая."
TARIFF = "Где посмотреть мой тариф? В приложении не нахожу."


def _record(i: int, text: str):
    rec = json.loads(json.dumps(SAMPLE_RECORD))
    rec["conversation_id"] = f"c-{i:02d}"
    rec["transcript"][0]["text"] = f"{text} (#{i})"            # разные транскрипты — иначе сработает кэш
    return rec


def _judge(system, user, schema):
    if "вывести деньги" in user:
        return with_reason(dict(EXAMPLE_VALUES, empathy=False), primary="money.withdrawal", resolution="escalated")
    if "#5" in user:
        return with_reason(primary=OTHER, other="вопрос о переводе акций к другому брокеру", resolution="unresolved")
    return with_reason(primary="fees.tariff", resolution="resolved")


def _seed(temp_db, n_withdraw=3, n_tariff=3):
    from app import jobs
    from app.checklist.service import latest_version
    from app.db import session_scope
    from app.store import upsert_conversations

    recs = [_record(i, WITHDRAW) for i in range(n_withdraw)] + \
           [_record(n_withdraw + i, TARIFF) for i in range(n_tariff)]
    with session_scope() as s:
        upsert_conversations(s, {"records": recs}, "DEV")
        ver_id = latest_version(s).id
    job = asyncio.run(jobs.run_job(jobs.create_job([r["conversation_id"] for r in recs], ver_id, OPTS, False),
                                   Pipeline(lambda p: MockClient(_judge))))
    assert job["failed"] == 0
    return recs


def test_reason_stored_and_analytics(temp_db):
    from app.db import session_scope
    from app.models import Evaluation
    from app.reasons.analytics import reasons_summary

    _seed(temp_db)
    with session_scope() as s:
        evs = s.query(Evaluation).all()
        assert {e.reason_code for e in evs} == {"money.withdrawal", "fees.tariff", OTHER}
        assert all(e.reason_taxonomy_version_id and e.resolution and e.reason_topic for e in evs)
        rep = reasons_summary(s, dataset="DEV")
        sub = reasons_summary(s, dataset="DEV", level="subtopic")

    assert rep["classified"] == 6 and rep["unclassified"] == 0
    assert rep["resolution"] == {"resolved": 2, "escalated": 3, "unresolved": 1, "client_left": 0, "no_request": 0}
    money = next(t for t in rep["topics"] if t["code"] == "money")
    assert money["count"] == 3 and money["share"] == 0.5 and money["escalated_rate"] == 1.0
    assert money["subtopics"][0]["code"] == "money.withdrawal"
    assert money["weakest_criteria"][0]["criterion_id"] == "empathy" and money["weakest_criteria"][0]["avg_ratio"] == 0
    # матрица: эмпатия по деньгам 0, по тарифам 1
    rows = {r["code"]: r for r in rep["matrix"]["rows"]}
    assert rows["money"]["cells"]["empathy"]["avg"] == 0 and rows["fees"]["cells"]["empathy"]["avg"] == 1
    assert {r["code"] for r in sub["matrix"]["rows"]} == {"money.withdrawal", "fees.tariff", OTHER}
    # находка: эмпатия по выводу средств хуже средней; по теме не дублируется
    f = rep["findings"]
    assert len(f) == 1 and f[0]["criterion_id"] == "empathy" and f[0]["reason"] == "money.withdrawal"
    assert f[0]["baseline"] == 0.5 and f[0]["gap"] == -0.5 and "Вывод средств" in f[0]["text"]
    assert rep["other"]["count"] == 1 and "перевод" in rep["other"]["items"][0]["description"]


def test_findings_not_duplicated_when_criterion_only_in_one_subtopic(temp_db):
    """Тема из двух подтем, критерий применим только в одной: находка по теме = находка по подтеме, дубль не нужен."""
    from app import jobs
    from app.checklist.service import latest_version
    from app.db import session_scope
    from app.reasons.analytics import reasons_summary
    from app.store import upsert_conversations

    def judge(system, user, schema):
        if "вывести деньги" in user:      # подтема 1: идентификация провалена
            return with_reason(dict(EXAMPLE_VALUES, identification=False), primary="money.withdrawal")
        if "пополнить" in user:           # подтема 2 той же темы: идентификация N/A
            return with_reason(primary="money.deposit")
        return with_reason(dict(EXAMPLE_VALUES, identification=True), primary="fees.tariff")
    recs = ([_record(i, WITHDRAW) for i in range(3)] + [_record(3 + i, "Как пополнить счёт?") for i in range(3)]
            + [_record(6 + i, TARIFF) for i in range(3)])
    with session_scope() as s:
        upsert_conversations(s, {"records": recs}, "DEV")
        ver_id = latest_version(s).id
    asyncio.run(jobs.run_job(jobs.create_job([r["conversation_id"] for r in recs], ver_id, OPTS, False),
                             Pipeline(lambda p: MockClient(judge))))
    with session_scope() as s:
        f = [x for x in reasons_summary(s, dataset="DEV")["findings"] if x["criterion_id"] == "identification"]
    assert [x["reason"] for x in f] == ["money.withdrawal"]
    assert f[0]["conversation_ids"] == ["c-00", "c-01", "c-02"]          # диалоги с провалом


def test_copied_evaluation_keeps_reason(temp_db):
    """Тот же транскрипт под другим ID получает копию оценки — вместе с причиной."""
    from app import jobs
    from app.db import session_scope
    from app.models import Evaluation
    from app.store import upsert_conversations

    recs = _seed(temp_db, 1, 0)
    dup = dict(recs[0], conversation_id="c-dup")
    with session_scope() as s:
        upsert_conversations(s, {"records": [dup]}, "PROD")
        ver_id = s.query(Evaluation).first().checklist_version_id
    asyncio.run(jobs.run_job(jobs.create_job(["c-dup"], ver_id, OPTS, False), Pipeline(lambda p: MockClient(_judge))))
    with session_scope() as s:
        ev = s.query(Evaluation).filter_by(conversation_id="c-dup").one()
        assert ev.reason_code == "money.withdrawal" and ev.audit.get("copied_from_conversation") == "c-00"


def test_migration_adds_reason_columns(tmp_path):
    from sqlalchemy import inspect, text

    from app import db

    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    engine = db.configure(url)
    with engine.begin() as conn:
        for col in ("reason", "resolution", "reason_topic", "reason_code", "reason_taxonomy_version_id"):
            conn.execute(text(f"DROP INDEX IF EXISTS ix_evaluations_{col}"))
            conn.execute(text(f"ALTER TABLE evaluations DROP COLUMN {col}"))
    engine = db.configure(url)
    cols = {c["name"] for c in inspect(engine).get_columns("evaluations")}
    assert {"reason", "resolution", "reason_topic", "reason_code", "reason_taxonomy_version_id"} <= cols


# --------------------------------------------------------------------------- поиск новых подтем
OTHER_TEXTS = ["перевод акций к другому брокеру", "перевести бумаги к другому брокеру", "перевод ценных бумаг брокеру",
               "вопрос про криптовалюту", "открыть счёт ребёнку"]


def _seed_other(temp_db):
    from app import jobs
    from app.checklist.service import latest_version
    from app.db import session_scope
    from app.store import upsert_conversations

    recs = [_record(i, f"вопрос {i}") for i in range(len(OTHER_TEXTS))]

    def judge(system, user, schema):
        i = int(user.split("(#")[1].split(")")[0])
        return with_reason(primary=OTHER, other=OTHER_TEXTS[i], resolution="unresolved")
    with session_scope() as s:
        upsert_conversations(s, {"records": recs}, "DEV")
        ver_id = latest_version(s).id
    asyncio.run(jobs.run_job(jobs.create_job([r["conversation_id"] for r in recs], ver_id, OPTS, False),
                             Pipeline(lambda p: MockClient(judge))))


def test_discover_lexical(temp_db, tax):
    from app.db import session_scope
    from app.reasons.discovery import discover

    _seed_other(temp_db)
    with session_scope() as s:
        res = asyncio.run(discover(s, tax, dataset="DEV", min_cluster=3))
    assert res["method"] == "lexical" and res["other_total"] == 5
    assert len(res["proposals"]) == 1 and res["proposals"][0]["count"] == 3 and res["unassigned"] == 2
    assert {e["description"] for e in res["proposals"][0]["examples"]} == set(OTHER_TEXTS[:3])
    assert ReasonTaxonomy(topics=res["proposed_topics"])                   # годится для новой версии


def test_discover_llm_validated(temp_db, tax):
    from app.db import session_scope
    from app.reasons.discovery import discover

    _seed_other(temp_db)
    llm = MockClient([json.dumps({"proposals": [
        {"kind": "new_subtopic", "existing_code": "", "topic_code": "trading", "topic_title": "",
         "subtopic_code": "securities_transfer", "subtopic_title": "Перевод бумаг к другому брокеру",
         "description": "Перевод ценных бумаг между брокерами.", "item_ids": [0, 1, 2, 2, 99]},
        {"kind": "existing", "existing_code": "access.account_lifecycle", "topic_code": "", "topic_title": "",
         "subtopic_code": "", "subtopic_title": "", "description": "", "item_ids": [4]},
        {"kind": "new_subtopic", "existing_code": "", "topic_code": "crypto", "topic_title": "Крипто",
         "subtopic_code": "general", "subtopic_title": "Крипта", "description": "", "item_ids": [3]},   # < порога
        {"kind": "new_subtopic", "existing_code": "", "topic_code": "Bad Code", "topic_title": "",
         "subtopic_code": "x", "subtopic_title": "", "description": "", "item_ids": [0, 1, 2]},
    ]}, ensure_ascii=False)])
    with session_scope() as s:
        res = asyncio.run(discover(s, tax, dataset="DEV", min_cluster=3, client=llm))
    assert res["method"] == "llm" and len(llm.calls) == 1
    assert "перевод акций к другому брокеру" in llm.calls[0]["user"]
    kinds = [(p["kind"], p["code"], p["count"]) for p in res["proposals"]]
    assert kinds == [("new_subtopic", "trading.securities_transfer", 3), ("existing", "access.account_lifecycle", 1)]
    new = next(t for t in res["proposed_topics"] if t["code"] == "trading")
    assert any(s["code"] == "securities_transfer" for s in new["subtopics"])
    assert res["proposed_topics"][-1]["code"] == "other"


def test_discover_falls_back_when_llm_fails(temp_db, tax):
    from app.brain.llm_common import LLMError
    from app.db import session_scope
    from app.reasons.discovery import discover

    _seed_other(temp_db)
    with session_scope() as s:
        res = asyncio.run(discover(s, tax, dataset="DEV", client=MockClient([], fail=LLMError("нет ключа"))))
    assert res["method"] == "lexical" and "нет ключа" in res["error"] and res["proposals"]


# --------------------------------------------------------------------------- API
@pytest.fixture
def api(temp_db, monkeypatch):
    from app import jobs
    from app.main import app

    monkeypatch.setattr(jobs, "_client_factory", lambda provider, model=None: MockClient(_judge))
    with TestClient(app) as c:
        yield c


def test_api_taxonomy_versions(api, tax):
    t = api.get("/api/v1/reasons/taxonomy").json()
    assert t["version"] == 1 and t["active"] and "resolved" in t["resolutions"]
    assert api.post("/api/v1/reasons/versions", json={"topics": [{"code": "X y", "title": "", "subtopics": []}]}).status_code == 422

    topics = tax.topics_dump() + [{"code": "crypto", "title": "Крипто", "subtopics": [{"code": "buy", "title": "Покупка"}]}]
    v2 = api.post("/api/v1/reasons/versions", json={"topics": topics, "note": "крипто"}).json()
    assert v2["version"] == 2
    assert api.get("/api/v1/reasons/taxonomy").json()["version"] == 2           # без выбора в настройках — последняя
    assert api.post("/api/v1/reasons/versions/1/activate").json()["active"]
    assert api.get("/api/v1/reasons/taxonomy").json()["version"] == 1
    assert [v["active"] for v in api.get("/api/v1/reasons/versions").json()] == [False, True]
    d = api.get(f"/api/v1/reasons/diff/{t['id']}/{v2['id']}").json()
    assert [a["code"] for a in d["added"]] == ["crypto.buy"] and d["removed"] == []
    assert "`crypto.buy`" in api.get(f"/api/v1/reasons/versions/{v2['id']}/prompt").json()["text"]
    assert api.put("/api/v1/settings", json={"reason_taxonomy_version_id": 999}).status_code == 404


def test_api_analytics_and_discover(api):
    recs = [_record(i, WITHDRAW if i < 3 else TARIFF) for i in range(6)]
    api.post("/api/v1/conversations/upload", json={"dataset": "DEV", "payload": {"records": recs}})
    job_id = api.post("/api/v1/evaluations", json={"all_in_dataset": "DEV"}).json()["job_id"]
    from test_api import _wait_job

    assert _wait_job(api, job_id)["failed"] == 0
    card = api.get("/api/v1/conversations/c-00").json()["evaluation"]
    assert card["reason_code"] == "money.withdrawal" and card["reason"]["title"] == "Деньги → Вывод средств"
    item = api.get("/api/v1/conversations", params={"dataset": "DEV"}).json()["items"][0]["evaluation"]
    assert item["reason_code"] and item["resolution"]

    rep = api.get("/api/v1/analytics/reasons", params={"dataset": "DEV"}).json()
    assert rep["classified"] == 6 and rep["findings"][0]["reason"] == "money.withdrawal"
    assert api.get("/api/v1/analytics/reasons", params={"level": "x"}).status_code == 422

    res = api.post("/api/v1/reasons/discover", json={"dataset": "DEV", "use_llm": False}).json()
    assert res["method"] == "none" and res["other_total"] == 1 and "меньше порога" in res["error"]
