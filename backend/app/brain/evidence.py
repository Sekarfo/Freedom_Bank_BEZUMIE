"""Проверка доказательств: номера реплик в диапазоне и цитата есть в тексте реплики.

Статусы:
  verified   — цитата найдена в одной из указанных реплик (нечёткое совпадение ≥ порога);
  corrected  — цитата найдена, но в другой реплике: номер исправлен кодом (типичная ошибка ±1);
  no_quote   — номера корректны, но цитаты нет (вывод не подтверждён текстом);
  unverified — цитата не найдена ни в одной реплике (возможна выдумка модели);
  missing    — нет ни корректных номеров, ни цитаты.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from pydantic import BaseModel, Field

from app.schemas import Conversation

_PREFIX = re.compile(r"^\s*\[\d+\]\s*(?:client|operator|bot|system)?\s*(?:\([^)]*\))?\s*:?\s*", re.I)
_PUNCT = re.compile(r"[«»\"'“”„`.,!?;:()\[\]{}…—–\-/\\]+")
_SPLIT = re.compile(r"\.\.\.|…|\s\[\.\.\.\]\s")


def normalize(text: str) -> str:
    t = (text or "").lower().replace("ё", "е")
    t = _PUNCT.sub(" ", t)
    return " ".join(t.split())


def fragment_score(fragment: str, text: str) -> float:
    """Лучшее совпадение фрагмента с подстрокой текста (окно из того же числа слов ± 2)."""
    q, t = normalize(fragment), normalize(text)
    if not q:
        return 0.0
    if q in t:
        return 1.0
    qw, tw = q.split(), t.split()
    if not tw:
        return 0.0
    # Быстрый отсев: при пересечении слов < 30% символьное сходство ≥ 0.85 недостижимо.
    qset = set(qw)
    overlap = len(qset & set(tw)) / len(qset)
    if len(qset) > 3 and overlap < 0.3:
        return overlap * 0.5
    n = len(qw)
    best = 0.0
    for size in {max(1, n - 2), max(1, n - 1), n, n + 1, n + 2}:
        if size > len(tw):
            size = len(tw)
        for start in range(0, len(tw) - size + 1):
            window = " ".join(tw[start:start + size])
            r = SequenceMatcher(None, q, window, autojunk=False).ratio()
            if r > best:
                best = r
                if best >= 0.999:
                    return best
    return best


def quote_score(quote: str, text: str) -> float:
    """Цитата с многоточиями проверяется по частям; итог — минимум по частям."""
    quote = _PREFIX.sub("", quote or "").strip()
    parts = [p for p in (_SPLIT.split(quote) if quote else []) if len(normalize(p)) >= 3]
    if not parts:
        return 0.0
    return min(fragment_score(p, text) for p in parts)


class EvidenceCheck(BaseModel):
    status: str
    turns: list[int] = Field(default_factory=list)       # итоговые номера (валидные, с исправлением)
    out_of_range: list[int] = Field(default_factory=list)
    matched_turn: int | None = None
    score: float = 0.0
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("verified", "corrected")


def verify_evidence(evidence_turns: list[int], quote: str, conv: Conversation, threshold: float = 0.85) -> EvidenceCheck:
    n = conv.n_turns
    valid, bad = [], []
    for t in evidence_turns or []:
        if isinstance(t, int) and not isinstance(t, bool) and 1 <= t <= n:
            if t not in valid:
                valid.append(t)
        else:
            bad.append(t)
    note = f"Номера вне диапазона 1..{n}: {bad}. " if bad else ""
    if not (quote or "").strip():
        if valid:
            return EvidenceCheck(status="no_quote", turns=valid, out_of_range=bad, note=note + "Цитата не указана.")
        return EvidenceCheck(status="missing", turns=[], out_of_range=bad, note=note + "Нет номеров реплик и цитаты.")

    best_idx, best = None, 0.0
    for idx in valid:
        s = quote_score(quote, conv.turns[idx - 1].text)
        if s > best:
            best_idx, best = idx, s
    if best >= threshold:
        return EvidenceCheck(status="verified", turns=valid, out_of_range=bad, matched_turn=best_idx,
                             score=round(best, 3), note=note.strip())

    # Цитата не в указанных репликах — ищем по всему диалогу (ошибка нумерации у модели).
    g_idx, g_best = None, 0.0
    for t in conv.turns:
        s = quote_score(quote, t.text)
        if s > g_best:
            g_idx, g_best = t.idx, s
    if g_best >= threshold and g_idx is not None:
        turns = sorted(set(valid) | {g_idx})
        return EvidenceCheck(status="corrected", turns=turns, out_of_range=bad, matched_turn=g_idx, score=round(g_best, 3),
                             note=(note + f"Цитата найдена в реплике [{g_idx}], номер добавлен кодом.").strip())
    status = "unverified" if valid else "missing"
    return EvidenceCheck(status=status, turns=valid, out_of_range=bad, score=round(max(best, g_best), 3),
                         note=(note + "Цитата не найдена в тексте диалога.").strip())
