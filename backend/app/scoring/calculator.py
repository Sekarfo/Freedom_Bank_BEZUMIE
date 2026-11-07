"""Расчёт итога — только код, LLM баллы не считает.

Правила чек-листа:
1. N/A ставится только там, где разрешён; критерий с N/A исключается.
2. «Да» = полный вес, «Нет» = 0; шкала — пропорционально, верхняя ступень = полный вес.
3. Веса N/A пропорционально перераспределяются: итог = заработано / применимый вес × 100.
4. Округление до двух знаков (half-up). Нет применимых критериев с весом — итог не рассчитывается (None).
5. Грубая ошибка — отдельный флаг, итог не меняет.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any, Iterable

from app.schemas import CriterionConfig


def round2(x: Fraction | float) -> float:
    if isinstance(x, Fraction):
        d = Decimal(x.numerator) / Decimal(x.denominator)
    else:
        d = Decimal(repr(x))
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _frac(x: float | int) -> Fraction:
    return Fraction(str(x)) if isinstance(x, float) else Fraction(x)


def criterion_points_exact(c: CriterionConfig, value: Any) -> Fraction | None:
    """Баллы критерия до перераспределения. None — критерий не участвует (N/A или флаг)."""
    if c.is_critical_flag or value is None:
        return None
    weight = _frac(c.weight)
    if c.type == "binary":
        if not isinstance(value, bool):
            raise ValueError(f"{c.criterion_id}: ожидалось true/false, получено {value!r}")
        return weight if value else Fraction(0)
    if isinstance(value, bool) or not isinstance(value, int) or not c.is_allowed(value):
        raise ValueError(f"{c.criterion_id}: недопустимое значение шкалы {value!r}")
    return Fraction(value, c.max_scale) * weight


def criterion_points(c: CriterionConfig, value: Any) -> float | None:
    p = criterion_points_exact(c, value)
    return None if p is None else round2(p)


def compute_score(results: dict[str, Any], criteria: Iterable[CriterionConfig]) -> float | None:
    earned, applicable = Fraction(0), Fraction(0)
    for c in criteria:
        if c.is_critical_flag:
            continue
        if c.criterion_id not in results:
            raise KeyError(f"Нет значения критерия {c.criterion_id}")
        p = criterion_points_exact(c, results[c.criterion_id])
        if p is None:
            continue
        applicable += _frac(c.weight)
        earned += p
    if applicable == 0:
        return None
    return round2(earned / applicable * 100)


def compute_sections(results: dict[str, Any], criteria: list[CriterionConfig]) -> list[dict[str, Any]]:
    """Итоги по секциям той же формулой. Секции в порядке первого появления в чек-листе."""
    order: list[str] = []
    grouped: dict[str, list[CriterionConfig]] = {}
    for c in criteria:
        if c.section not in grouped:
            order.append(c.section)
            grouped[c.section] = []
        grouped[c.section].append(c)
    out = []
    for section in order:
        crits = grouped[section]
        weighted = [c for c in crits if not c.is_critical_flag]
        earned = sum((criterion_points_exact(c, results.get(c.criterion_id)) or Fraction(0)) for c in weighted)
        applicable = sum((_frac(c.weight) for c in weighted if results.get(c.criterion_id) is not None), Fraction(0))
        out.append({
            "section": section,
            "criteria": [c.criterion_id for c in crits],
            "is_flag_section": not weighted,
            "earned": round2(earned) if weighted else None,
            "applicable_weight": round2(applicable) if weighted else None,
            "max_weight": round2(sum((_frac(c.weight) for c in weighted), Fraction(0))) if weighted else None,
            "score": (round2(earned / applicable * 100) if applicable else None) if weighted else None,
        })
    return out


def compute_critical_flag(results: dict[str, Any], criteria: Iterable[CriterionConfig]) -> bool:
    return any(results.get(c.criterion_id) is True for c in criteria if c.is_critical_flag)


def redistributed_weights(results: dict[str, Any], criteria: list[CriterionConfig]) -> dict[str, float | None]:
    """Эффективный вес каждого критерия после перераспределения N/A (для подсказки в UI)."""
    applicable = sum((_frac(c.weight) for c in criteria
                      if not c.is_critical_flag and results.get(c.criterion_id) is not None), Fraction(0))
    out: dict[str, float | None] = {}
    for c in criteria:
        if c.is_critical_flag or results.get(c.criterion_id) is None or applicable == 0:
            out[c.criterion_id] = None
        else:
            out[c.criterion_id] = round2(_frac(c.weight) / applicable * 100)
    return out
