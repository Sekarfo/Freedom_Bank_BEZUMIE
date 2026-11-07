"""decision.py: каждая строка таблицы правил финального решения."""
from app.brain.decision import decide
from app.brain.preprocess import compute_signals
from app.schemas import LLMCallInfo, StageResult, Verdict
from conftest import EXAMPLE_EVIDENCE, EXAMPLE_VALUES


def _judge(values, *, conf="high", evidence=None, valid=True, errors=None):
    evidence = evidence or EXAMPLE_EVIDENCE
    verdicts = {}
    for cid, v in values.items():
        turns, quote = evidence.get(cid, EXAMPLE_EVIDENCE[cid])
        verdicts[cid] = Verdict(criterion_id=cid, value=v, rationale=f"judge {cid}", evidence_turns=turns,
                                quote=quote, confidence=conf)
    call = LLMCallInfo(provider="mock", model="m", prompt_version="v", stage="judge", errors=errors or [])
    return StageResult(verdicts=verdicts if valid else {}, call=call, valid=valid)


def _run(checklist, conv, judge):
    return decide(checklist, conv, compute_signals(conv), judge)


def _by_id(result):
    return {c["criterion_id"]: c for c in result.criteria}


def test_verified_quote_keeps_model_confidence(checklist, conv):
    r = _run(checklist, conv, _judge(EXAMPLE_VALUES))
    c = _by_id(r)["contact"]
    assert r.status == "ok"
    assert (c["value"], c["confidence"], c["needs_review"], c["evidence_status"]) == (True, "high", False, "verified")
    assert c["ai_value"] is True and c["review"] is None


def test_quote_not_in_dialogue_goes_to_review(checklist, conv):
    ev = dict(EXAMPLE_EVIDENCE, consultation=([5], "Этой фразы в диалоге нет и не было"))
    r = _run(checklist, conv, _judge(EXAMPLE_VALUES, evidence=ev))
    c = _by_id(r)["consultation"]
    assert (c["value"], c["confidence"], c["needs_review"]) == (15, "low", True)  # значение модели не меняется
    assert any("не подтверждено" in x for x in c["review_reasons"])


def test_low_confidence_goes_to_review(checklist, conv):
    r = _run(checklist, conv, _judge(EXAMPLE_VALUES, conf="low"))
    c = _by_id(r)["empathy"]
    assert c["confidence"] == "low" and c["needs_review"] is True
    assert any("не уверена" in x for x in c["review_reasons"])


def test_signal_contradiction_lowers_confidence(checklist, conv):
    vals = dict(EXAMPLE_VALUES, contact=False)  # но в [4] есть «Здравствуйте» и «Меня зовут Алина»
    r = _run(checklist, conv, _judge(vals))
    c = _by_id(r)["contact"]
    assert c["value"] is False and c["confidence"] == "medium" and c["needs_review"] is False
    assert any("Сигналы" in x for x in c["review_reasons"])


def test_critical_no_but_rude_phrase_found_goes_to_review(checklist, conv):
    sig = compute_signals(conv).model_copy(update={"critical_pattern_turns": [7]})  # словарь сработал в [7]
    r = decide(checklist, conv, sig, _judge(EXAMPLE_VALUES))
    c = _by_id(r)["critical"]
    assert c["value"] is False and c["needs_review"] is True
    assert any("Возможное нарушение" in x for x in c["review_reasons"])


def test_missing_evidence_gets_default_checked_turns(checklist, conv):
    ev = dict(EXAMPLE_EVIDENCE, contact=([], ""))
    r = _run(checklist, conv, _judge(EXAMPLE_VALUES, evidence=ev))
    c = _by_id(r)["contact"]
    assert c["evidence_turns"] == [4]          # первая реплика оператора — проверенный участок
    assert c["evidence_status"] == "default" and c["needs_review"] is True


def test_missing_criterion_is_error(checklist, conv):
    vals = {k: v for k, v in EXAMPLE_VALUES.items() if k != "closure"}
    r = _run(checklist, conv, _judge(vals, errors=["попытка 2: closure: критерий отсутствует в ответе"]))
    assert r.status == "error" and r.criteria == [] and "closure" in r.error


def test_model_unavailable_is_error(checklist, conv):
    r = _run(checklist, conv, _judge({}, valid=False, errors=["попытка 1: Claude недоступен"]))
    assert r.status == "error" and "Claude недоступен" in r.error
