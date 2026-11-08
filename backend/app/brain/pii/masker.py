"""Сборка детекторов: поиск фрагментов, разрешение пересечений, метки, отчёт."""
from __future__ import annotations

import copy
import hashlib
import re
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.brain.pii.names import (COMMON_WORD_NAMES, STOP, _WORD, NERBackend, find_context_names, find_ner_names,
                                 name_stem)
from app.brain.pii.rules import RULES, Span
from app.brain.pii.spoken import find_spoken_numbers
from app.schemas import Conversation

MASKING_VERSION = "pii-v2"

# Метки уже скрытых данных (анонимизация организаторов «[Имя скрыто]», наши «[CARD_1]») не трогаем.
_BRACKETS = re.compile(r"\[[^\[\]\n]{1,60}\]")


def resolve(spans: Iterable[Span]) -> list[Span]:
    """Непересекающийся набор: сначала более «сильный» тип, при равенстве — более длинный фрагмент."""
    chosen: list[Span] = []
    for s in sorted(spans, key=lambda s: (-s.priority, -(s.end - s.start), s.start)):
        if s.end <= s.start:
            continue
        if all(s.end <= c.start or s.start >= c.end for c in chosen):
            chosen.append(s)
    return sorted(chosen, key=lambda s: s.start)


class Masker:
    """Маскирует тексты одного диалога: одинаковое значение → одна метка, имена запоминаются для всего диалога."""

    def __init__(self, ner: NERBackend | None = None) -> None:
        self.ner = ner
        self._tokens: dict[tuple[str, str], str] = {}
        self._next: Counter[str] = Counter()
        self._names: dict[str, str] = {}     # основа слова имени → ключ человека
        self.counts: Counter[str] = Counter()
        self.sources: Counter[str] = Counter()

    def __deepcopy__(self, memo: dict) -> "Masker":
        """Копируется состояние (метки, имена, счётчики); модель NER общая."""
        other = Masker(self.ner)
        other._tokens, other._next = dict(self._tokens), Counter(self._next)
        other._names = dict(self._names)
        other.counts, other.sources = Counter(self.counts), Counter(self.sources)
        return other

    # ------------------------------------------------------------------ поиск
    def detect(self, text: str) -> list[Span]:
        if not text:
            return []
        spans: list[Span] = []
        for rule in RULES:
            spans.extend(rule.find(text))
        spans.extend(find_spoken_numbers(text))
        spans.extend(find_context_names(text))
        if self.ner is not None:
            spans.extend(find_ner_names(text, self.ner))
        protected = [(m.start(), m.end()) for m in _BRACKETS.finditer(text)]
        return [s for s in spans if all(s.end <= a or s.start >= b for a, b in protected)]

    def learn(self, spans: Iterable[Span], text: str) -> None:
        for s in spans:
            if s.kind != "PERSON":
                continue
            for w in _WORD.findall(text[s.start:s.end]):
                st = name_stem(w)
                if len(st) >= 3 and w.lower() not in STOP:
                    self._names.setdefault(st, s.key)

    def propagate(self, text: str) -> list[Span]:
        """Имя, найденное в одной реплике, маскируется и в остальных («спасибо, алина»)."""
        out = []
        for m in _WORD.finditer(text or ""):
            w = m.group(0)
            st = name_stem(w)
            key = self._names.get(st)
            if key is None or w.lower() in STOP:
                continue
            if not w[0].isupper() and st in COMMON_WORD_NAMES:
                continue
            out.append(Span(m.start(), m.end(), "PERSON", key, "propagated"))
        return out

    # ------------------------------------------------------------------ замена
    def _token(self, kind: str, key: str) -> str:
        k = (kind, key)
        if k not in self._tokens:
            self._next[kind] += 1
            self._tokens[k] = f"[{kind}_{self._next[kind]}]"
        return self._tokens[k]

    def render(self, text: str, spans: list[Span]) -> str:
        parts, pos = [], 0
        for s in spans:
            parts += [text[pos:s.start], self._token(s.kind, s.key)]
            self.counts[s.kind] += 1
            self.sources[s.source.split(":")[0]] += 1
            pos = s.end
        parts.append(text[pos:])
        return "".join(parts)

    def mask(self, text: str) -> str:
        """Один текст (например, фрагмент подсказки) с учётом имён, уже известных по диалогу."""
        if not text:
            return text
        spans = resolve(self.detect(text) + self.propagate(text))
        self.learn(spans, text)
        return self.render(text, spans)

    @property
    def distinct(self) -> dict[str, int]:
        return dict(self._next)


@dataclass
class MaskResult:
    conv: Conversation
    masker: Masker
    turns: list[int] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.summary.get("replacements", {}).values())

    def report(self) -> dict[str, Any]:
        """Для журнала оценки: только счётчики и номера реплик, без значений."""
        return {"enabled": True, "version": MASKING_VERSION, **self.summary, "turns": self.turns}


def _mask(conv: Conversation, ner: NERBackend | None) -> MaskResult:
    masker = Masker(ner)
    # Проход 1: находим всё и запоминаем имена — имя из 10-й реплики скроется и в 3-й.
    found = []
    for t in conv.turns:
        spans = resolve(masker.detect(t.text))
        masker.learn(spans, t.text)
        found.append(spans)
    # Проход 2: добавляем повторы известных имён и заменяем.
    turns, changed = [], []
    for t, spans in zip(conv.turns, found):
        final = resolve(spans + masker.propagate(t.text))
        text = masker.render(t.text, final)
        if final:
            changed.append(t.idx)
        turns.append(t.model_copy(update={"text": text}))
    summary = {"ner": ner.name if ner is not None else "off", "replacements": dict(masker.counts),
               "distinct_values": masker.distinct, "sources": dict(masker.sources)}
    # raw (исходная запись) в копию не берём: дальше по конвейеру уходит только замаскированный текст.
    return MaskResult(conv=conv.model_copy(update={"turns": turns, "raw": {}}), masker=masker, turns=changed,
                      summary=summary)


_cache: OrderedDict[str, MaskResult] = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_SIZE = 256


def mask_conversation(conv: Conversation, ner: NERBackend | None = None) -> MaskResult:
    """Детерминированно: тот же текст → те же метки. Результат кэшируется (ключ кэша оценки и сама оценка
    маскируют один и тот же диалог), вызывающий получает собственную копию."""
    h = hashlib.sha256()
    h.update((ner.name if ner is not None else "off").encode())
    for t in conv.turns:
        h.update(b"\x00" + t.text.encode("utf-8"))
    key = h.hexdigest()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
    if hit is None:
        hit = _mask(conv, ner)
        with _cache_lock:
            _cache[key] = hit
            while len(_cache) > _CACHE_SIZE:
                _cache.popitem(last=False)
    res = copy.deepcopy(hit)
    # Номера реплик, метаданные и т.п. берём из текущего диалога, текст — из кэша.
    res.conv = conv.model_copy(update={"turns": [t.model_copy(update={"text": m.text})
                                                 for t, m in zip(conv.turns, res.conv.turns)], "raw": {}})
    return res
