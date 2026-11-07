"""Калькулятор итога (раздел 5): обязательные случаи."""
import pytest

from app.scoring.calculator import (compute_critical_flag, compute_score, compute_sections, criterion_points,
                                    redistributed_weights)
from conftest import EXAMPLE_VALUES


def test_appendix_example_85_38(checklist):
    assert compute_score(EXAMPLE_VALUES, checklist.criteria) == 85.38  # 55.5 / 65 × 100


def test_all_yes_and_max_scales_is_100(checklist):
    values = {"contact": True, "discovery": True, "identification": True, "consultation": 20,
              "empathy": True, "dialogue": 10, "closure": True, "critical": False}
    assert compute_score(values, checklist.criteria) == 100.0


def test_all_weighted_na_returns_none(checklist):
    # Теоретический случай: все взвешенные критерии N/A (используем чек-лист, где N/A разрешён всем).
    for c in checklist.criteria:
        if not c.is_critical_flag:
            c.na_allowed = True
    values = {c.criterion_id: None for c in checklist.criteria}
    values["critical"] = False
    assert compute_score(values, checklist.criteria) is None


def test_dialogue_4_with_weight_15_gives_6_points(checklist):
    dialogue = checklist.by_id()["dialogue"]
    assert criterion_points(dialogue, 4) == 6.0
    assert criterion_points(checklist.by_id()["consultation"], 15) == 15.0
    assert criterion_points(dialogue, 7) == 10.5


def test_critical_true_does_not_change_score(checklist):
    with_flag = dict(EXAMPLE_VALUES, critical=True)
    assert compute_score(with_flag, checklist.criteria) == compute_score(EXAMPLE_VALUES, checklist.criteria)
    assert compute_critical_flag(with_flag, checklist.criteria) is True
    assert compute_critical_flag(EXAMPLE_VALUES, checklist.criteria) is False


def test_no_gives_zero_and_redistribution(checklist):
    values = dict(EXAMPLE_VALUES, contact=False)
    # 45.5 / 65 × 100 = 70.0
    assert compute_score(values, checklist.criteria) == 70.0
    eff = redistributed_weights(EXAMPLE_VALUES, checklist.criteria)
    assert eff["discovery"] is None and eff["critical"] is None
    assert round(sum(v for v in eff.values() if v is not None), 1) == 100.0


def test_invalid_scale_value_rejected(checklist):
    with pytest.raises(ValueError):
        compute_score(dict(EXAMPLE_VALUES, dialogue=5), checklist.criteria)
    with pytest.raises(ValueError):
        compute_score(dict(EXAMPLE_VALUES, contact=1), checklist.criteria)


def test_missing_criterion_raises(checklist):
    values = dict(EXAMPLE_VALUES)
    values.pop("empathy")
    with pytest.raises(KeyError):
        compute_score(values, checklist.criteria)


def test_rounding_half_up(checklist):
    # contact=No, discovery=Yes, identification=N/A, consultation=5, empathy=No, dialogue=4, closure=N/A
    values = {"contact": False, "discovery": True, "identification": None, "consultation": 5,
              "empathy": False, "dialogue": 4, "closure": None, "critical": False}
    # (0 + 15 + 5 + 0 + 6) / (10+15+20+15+15) = 26/75 = 34.666… → 34.67
    assert compute_score(values, checklist.criteria) == 34.67


def test_sections(checklist):
    sections = compute_sections(EXAMPLE_VALUES, checklist.criteria)
    by = {s["section"]: s for s in sections}
    assert by["Начало и понимание запроса"]["score"] == 100.0      # contact 10/10, discovery N/A
    assert by["Безопасность и решение"]["score"] == 75.0           # consultation 15/20, identification N/A
    assert by["Ведение и завершение диалога"]["score"] == 87.14     # (15 + 10.5 + 5) / 35
    assert by["Грубые нарушения"]["is_flag_section"] is True
