"""Контрольные суммы и нормализация значений — отсекают случайные совпадения регулярных выражений."""
from __future__ import annotations

import re


def digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def phone_canon(s: str) -> str:
    """+7 701 …, 8 701 … и 7701… — один номер: сравниваем по последним 10 цифрам."""
    d = digits(s)
    return d[-10:] if len(d) >= 10 else d


def text_canon(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower().replace("ё", "е"))


def luhn_ok(number: str) -> bool:
    """Номер карты: 13–19 цифр, алгоритм Луна."""
    d = digits(number)
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(d)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def iin_ok(value: str) -> bool:
    """ИИН/БИН РК: веса 1..11, при остатке 10 — веса 3..11,1,2; повторный остаток 10 — номер невалиден."""
    d = digits(value)
    if len(d) != 12:
        return False
    nums = [int(c) for c in d]
    s = sum(n * w for n, w in zip(nums[:11], range(1, 12))) % 11
    if s == 10:
        s = sum(n * w for n, w in zip(nums[:11], (3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2))) % 11
        if s == 10:
            return False
    return s == nums[11]


def iban_ok(value: str) -> bool:
    """IBAN: контрольная сумма ISO 13616 (mod 97); для KZ — ровно 20 знаков."""
    v = re.sub(r"\s", "", value).upper()
    if not 15 <= len(v) <= 34 or not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", v):
        return False
    if v.startswith("KZ") and len(v) != 20:
        return False
    rearranged = v[4:] + v[:4]
    return int("".join(str(int(ch, 36)) for ch in rearranged)) % 97 == 1
