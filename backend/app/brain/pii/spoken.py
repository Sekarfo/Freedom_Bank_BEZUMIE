"""Номера, продиктованные словами или цифрами через пробел (расшифровка звонка, ASR).

«восемь семьсот один сто двадцать три сорок пять шестьдесят семь» → 87011234567 → [PHONE_n]
«сегіз жеті жүз бір …», «eight seven oh one …», «870 412 300 017» — так же.

Серия слов-чисел (и групп цифр) без других слов между ними собирается в строку цифр по правилам устной речи
(«семьсот один» = 701, «сорок пять» = 45, «double five» = 55), затем классифицируется по длине
и контрольным суммам. Суммы («пятьдесят тысяч тенге») не трогаются: серия, за которой идёт валюта
или «тысяч/миллион», пропускается; короткие серии (< 7 цифр словами, < 10 цифрами) тоже.
"""
from __future__ import annotations

import re
from typing import Iterator

from app.brain.pii.checksums import iin_ok, luhn_ok
from app.brain.pii.rules import _CURRENCY, Span

# слово → (тип, значение); типы: u — единицы, z — ноль, t — десятки, teen — 10..19, h — сотни, m — множитель сотни
_W: dict[str, tuple[str, int]] = {}


def _add(kind: str, pairs: dict[str, int]) -> None:
    for w, v in pairs.items():
        _W[w] = (kind, v)


_add("z", {"ноль": 0, "нуль": 0, "нөл": 0, "zero": 0, "oh": 0})
_add("u", {"один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6,
           "семь": 7, "восемь": 8, "девять": 9,
           "бір": 1, "екі": 2, "үш": 3, "төрт": 4, "бес": 5, "алты": 6, "жеті": 7, "сегіз": 8, "тоғыз": 9,
           "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9})
_add("teen", {"десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
              "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
              "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
              "seventeen": 17, "eighteen": 18, "nineteen": 19})
_add("t", {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70,
           "восемьдесят": 80, "девяносто": 90,
           "он": 10, "жиырма": 20, "отыз": 30, "қырық": 40, "елу": 50, "алпыс": 60, "жетпіс": 70, "сексен": 80,
           "тоқсан": 90,
           "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
           "ninety": 90})
_add("h", {"сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500, "шестьсот": 600,
           "семьсот": 700, "восемьсот": 800, "девятьсот": 900})
_add("m", {"жүз": 100, "hundred": 100})
_REPEAT = {"double": 2, "triple": 3}

_TOKEN = re.compile(r"\d+|[^\W\d_]+")
_GAP = re.compile(r"^[\s,\-–—]*$")
_AFTER_AMOUNT = re.compile(r"^\s*" + _CURRENCY, re.I)
MIN_DIGITS_WORDS = 7     # серия со словами: от 7 цифр (номер документа, счёта, телефона)
MIN_DIGITS_PLAIN = 10    # серия из одних групп цифр: от 10 цифр (иначе это суммы «1 000 000»)


def words_to_digits(tokens: list[str]) -> str:
    out: list[str] = []
    cur: dict[str, int] | None = None   # {"h":…, "t":…, "u":…} — текущая группа «сотни-десятки-единицы»
    repeat = 1

    def flush() -> None:
        nonlocal cur
        if cur is not None:
            out.append(str(sum(cur.values())))
            cur = None

    for tok in tokens:
        if tok.isdigit():
            flush()
            out.append(tok)
            continue
        if tok in _REPEAT:
            flush()
            repeat = _REPEAT[tok]
            continue
        kind, v = _W[tok]
        if repeat > 1 and kind in ("u", "z"):
            flush()
            out.append(str(v) * repeat)
            repeat = 1
            continue
        repeat = 1
        if kind == "z":
            flush()
            out.append("0")
        elif kind == "h":
            flush()
            cur = {"h": v}
        elif kind == "m":
            if cur is not None and set(cur) == {"u"}:
                cur = {"h": cur["u"] * 100}       # «жеті жүз» = 700, «seven hundred» = 700
            else:
                flush()
                cur = {"h": 100}
        elif kind in ("t", "teen"):
            if cur is not None and "t" not in cur and "u" not in cur:
                cur["t"] = v
            else:
                flush()
                cur = {"t": v}
            if kind == "teen":
                cur["u"] = 0                       # «двенадцать» занимает и десятки, и единицы
        else:  # u
            if cur is not None and "u" not in cur:
                cur["u"] = v
            else:
                flush()
                cur = {"u": v}
    flush()
    return "".join(out)


def classify(d: str) -> str:
    if len(d) == 11 and d[0] in "78" or len(d) == 10 and d[0] == "7":
        return "PHONE"
    if len(d) == 12 and iin_ok(d):
        return "IIN"
    if 13 <= len(d) <= 19 and luhn_ok(d):
        return "CARD"
    return "NUMBER"


def find_spoken_numbers(text: str) -> Iterator[Span]:
    low = (text or "").lower()
    toks = [(m.start(), m.end(), m.group(0)) for m in _TOKEN.finditer(low)]
    i = 0
    while i < len(toks):
        if not (toks[i][2].isdigit() or toks[i][2] in _W or toks[i][2] in _REPEAT):
            i += 1
            continue
        j = i
        while (j + 1 < len(toks) and (toks[j + 1][2].isdigit() or toks[j + 1][2] in _W or toks[j + 1][2] in _REPEAT)
               and _GAP.match(low[toks[j][1]:toks[j + 1][0]])):
            j += 1
        run = [t[2] for t in toks[i:j + 1]]
        start, end = toks[i][0], toks[j][1]
        i = j + 1
        has_words = any(not t.isdigit() for t in run)
        if not has_words and len(run) < 2:
            continue                                   # одиночное число — дело регулярных правил
        if _AFTER_AMOUNT.match(low[end:]):
            continue                                   # сумма, а не номер
        d = words_to_digits(run)
        if len(d) < (MIN_DIGITS_WORDS if has_words else MIN_DIGITS_PLAIN):
            continue
        kind = classify(d)
        key = d[-10:] if kind == "PHONE" else d
        yield Span(start, end, kind, key, "spoken")
