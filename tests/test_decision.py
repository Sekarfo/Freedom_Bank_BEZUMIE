"""decision.py: каждая строка таблицы 6.6 и особые правила."""
from app.brain.decision import decide
from app.brain.preprocess import compute_signals
from app.schemas import LLMCallInfo, StageResult, Verdict
from conftest import EXAMPLE_EVIDENCE, EXAMPLE_VALUES


def _stage(values, *, stage="local", conf="high", evidence=None, decisions=None, valid=True, errors=None):
    evidence = evidence or EXAMPLE_EVIDENCE
    verdicts = {}
    for cid, v in values.items():
        turns, quote = evidence.get(cid, EXAMPLE_EVIDENCE[cid])
        verdicts[cid] = Verdict(criterion_id=cid, value=v, rationale=f"{stage} {cid}", evidence_turns=turns,
                                quote=quote, confidence=conf, decision=(decisions or {}).get(cid))
    call = LLMCallInfo(provider="mock", model="m", prompt_version="v", stage=stage, errors=errors or [])
    return StageResult(verdicts=verdicts if valid else {}, call=call, valid=valid)


def _by_id(result):
    return {c["criterion_id"]: c for c in result.criteria}


def _run(checklist, conv, local, cloud, role="review", arbiter=None, mode="hybrid"):
    return decide(checklist, conv, compute_signals(conv), mode=mode, cloud_role=role, local=local, cloud=cloud,
                  arbiter=arbiter)


def test_row1_cloud_confirms_with_verified_evidence(checklist, conv):
    r = _run(checklist, conv, _stage(EXAMPLE_VALUES), _stage(EXAMPLE_VALUES, stage="cloud"))
    c = _by_id(r)["contact"]
    assert r.status == "ok"
    assert (c["value"], c["agreement"], c["confidence"], c["needs_review"]) == (True, "agree", "high", False)


def test_row2_cloud_overrides_with_verified_evidence(checklist, conv):
    cloud_vals = dict(EXAMPLE_VALUES, consultation=10)
    r = _run(checklist, conv, _stage(EXAMPLE_VALUES), _stage(cloud_vals, stage="cloud"))
    c = _by_id(r)["consultation"]
    assert (c["value"], c["agreement"], c["confidence"], c["needs_review"]) == (10, "override", "medium", False)
    assert c["local"]["value"] == 15 and c["cloud"]["value"] == 10


def test_row3_cloud_overrides_without_evidence(checklist, conv):
    cloud_vals = dict(EXAMPLE_VALUES, consultation=10)
    ev = dict(EXAMPLE_EVIDENCE, consultation=([5], "Этой фразы в диалоге нет и не было"))
    r = _run(checklist, conv, _stage(EXAMPLE_VALUES), _stage(cloud_vals, stage="cloud", evidence=ev))
    c = _by_id(r)["consultation"]
    assert (c["value"], c["agreement"], c["confidence"], c["needs_review"]) == (10, "override", "low", True)


def test_row4_cloud_unavailable_uses_local(checklist, conv):
    cloud = _stage({}, stage="cloud", valid=False, errors=["попытка 1: Claude недоступен"])
    r = _run(checklist, conv, _stage(EXAMPLE_VALUES), cloud)
    for c in r.criteria:
        assert c["agreement"] == "local_only" and c["confidence"] == "low" and c["needs_review"] is True
    assert _by_id(r)["consultation"]["value"] == 15


def test_row5_both_invalid_is_error(checklist, conv):
    local = _stage({}, valid=False, errors=["попытка 2: невалидный JSON"])
    cloud = _stage({}, stage="cloud", valid=False, errors=["попытка 1: таймаут"])
    r = _run(checklist, conv, local, cloud)
    assert r.status == "error" and r.criteria == [] and "невалидный JSON" in r.error


def test_row6_cloud_low_confidence_needs_review(checklist, conv):
    r = _run(checklist, conv, _stage(EXAMPLE_VALUES), _stage(EXAMPLE_VALUES, stage="cloud", conf="low"))
    c = _by_id(r)["empathy"]
    assert c["confidence"] == "low" and c["needs_review"] is True and c["agreement"] == "agree"


def test_critical_possible_violation_flagged(checklist, conv):
    local_vals = dict(EXAMPLE_VALUES, critical=True)
    r = _run(checklist, conv, _stage(local_vals), _stage(EXAMPLE_VALUES, stage="cloud"))
    c = _by_id(r)["critical"]
    assert c["value"] is False  # финал — облака
    assert c["needs_review"] is True and any("Возможное нарушение" in x for x in c["review_reasons"])


def test_na_vs_no_disagreement_flagged(checklist, conv):
    local_vals = dict(EXAMPLE_VALUES, identification=False)
    r = _run(checklist, conv, _stage(local_vals), _stage(EXAMPLE_VALUES, stage="cloud"))
    c = _by_id(r)["identification"]
    assert c["value"] is None and c["needs_review"] is True
    assert any("N/A против" in x for x in c["review_reasons"])


def test_signal_contradiction_lowers_confidence(checklist, conv):
    vals = dict(EXAMPLE_VALUES, contact=False)  # но в [4] есть «Здравствуйте» и «Меня зовут Алина»
    r = _run(checklist, conv, _stage(vals), _stage(vals, stage="cloud"))
    c = _by_id(r)["contact"]
    assert c["agreement"] == "agree" and c["confidence"] == "medium"
    assert any("Сигналы" in x for x in c["review_reasons"])


def test_local_mode_single_model(checklist, conv):
    r = decide(checklist, conv, compute_signals(conv), mode="local", cloud_role=None,
               local=_stage(EXAMPLE_VALUES), cloud=None)
    c = _by_id(r)["contact"]
    assert c["agreement"] == "single" and c["source"] == "local" and c["confidence"] == "high"


def test_blind_disagreement_resolved_by_arbiter(checklist, conv):
    cloud_vals = dict(EXAMPLE_VALUES, dialogue=4)
    arbiter = _stage({"dialogue": 7}, stage="arbiter", conf="high")
    r = _run(checklist, conv, _stage(EXAMPLE_VALUES), _stage(cloud_vals, stage="cloud"), role="blind",
             arbiter=arbiter)
    c = _by_id(r)["dialogue"]
    assert (c["value"], c["agreement"], c["source"], c["confidence"]) == (7, "arbiter", "arbiter", "medium")
    assert _by_id(r)["contact"]["agreement"] == "agree"


def test_missing_evidence_gets_default_checked_turns(checklist, conv):
    ev = dict(EXAMPLE_EVIDENCE, contact=([], ""))
    r = _run(checklist, conv, _stage(EXAMPLE_VALUES, evidence=ev), _stage(EXAMPLE_VALUES, stage="cloud", evidence=ev))
    c = _by_id(r)["contact"]
    assert c["evidence_turns"] == [4]          # первая реплика оператора — проверенный участок
    assert c["evidence_status"] == "default" and c["needs_review"] is True
