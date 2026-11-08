"""Маскирование персональных данных перед отправкой транскрипта во внешнюю модель.

В модель уходит только замаскированная копия: значения заменяются метками вида [CARD_1], [PERSON_2].
Одинаковое значение в диалоге получает одну метку, поэтому модель видит, что оператор повторил тот же номер,
и может оценить раскрытие данных до подтверждения личности. Соответствие «метка → значение» существует только
в памяти на время оценки. В БД остаётся исходный транскрипт (его показывает интерфейс), в журнал оценки
пишутся только счётчики.

Детекторы (rules.py, spoken.py, names.py), при пересечении побеждает тип с большим приоритетом:
  SECRET   — CVV, PIN, коды из SMS, пароли, кодовое слово;
  EMAIL, IBAN (mod 97), PHONE, CARD (Луна), IIN (контрольный разряд ИИН/БИН);
  DOB      — дата рождения (по ключевому слову; прочие даты не трогаются);
  DOC      — номер удостоверения, паспорта, ВНЖ, водительского;
  ACCOUNT  — номер счёта или договора (суммы с валютой не трогаются);
  ADDRESS  — улица/проспект/мкр + дом/квартира, «… көшесі», почтовый индекс;
  PERSON   — NER (slovnet/Natasha) + «меня зовут / менің атым / my name is» + ФИО с отчеством;
             найденное имя скрывается и в остальных репликах;
  NUMBER   — прочие длинные номера, в том числе продиктованные словами («восемь семьсот один…»).

Fail-closed: если маскирование включено, а NER не загрузился, диалог не отправляется (status: error).
"""
from __future__ import annotations

from typing import Any

from app.brain.pii.masker import MASKING_VERSION, Masker, MaskResult, mask_conversation, resolve
from app.brain.pii.names import MaskingUnavailable, get_ner

LABELS: dict[str, str] = {
    "PERSON": "имя, фамилия или отчество",
    "IIN": "ИИН/БИН",
    "CARD": "номер карты",
    "IBAN": "IBAN",
    "PHONE": "телефон",
    "EMAIL": "e-mail",
    "DOB": "дата рождения",
    "DOC": "номер документа (удостоверение, паспорт)",
    "ACCOUNT": "номер счёта или договора",
    "ADDRESS": "адрес",
    "SECRET": "код из SMS, PIN, CVV, пароль или кодовое слово",
    "NUMBER": "иной длинный номер",
}


def mask_for_model(conv, settings) -> MaskResult | None:  # noqa: ANN001
    """Копия диалога для внешней модели (None — маскирование выключено). MaskingUnavailable — не отправлять."""
    if not settings.pii_masking:
        return None
    return mask_conversation(conv, get_ner(settings.pii_ner))


def status(settings) -> dict[str, Any]:  # noqa: ANN001
    out: dict[str, Any] = {"enabled": settings.pii_masking, "version": MASKING_VERSION, "ner": settings.pii_ner}
    if settings.pii_masking:
        try:
            backend = get_ner(settings.pii_ner)
            out["ner_ready"] = True
            out["ner_model"] = backend.name if backend is not None else "off"
        except MaskingUnavailable as exc:
            out["ner_ready"] = False
            out["error"] = str(exc)
    return out


__all__ = ["LABELS", "MASKING_VERSION", "Masker", "MaskResult", "MaskingUnavailable", "get_ner", "mask_conversation",
           "mask_for_model", "resolve", "status"]
