"""Общий протокол адаптера канала и вспомогательные функции нормализации."""
from __future__ import annotations

import re
from typing import Any, Protocol

from app.schemas import Conversation

SPEAKER_ALIASES = {
    "client": "client", "customer": "client", "user": "client", "клиент": "client",
    "operator": "operator", "agent": "operator", "оператор": "operator", "specialist": "operator",
    "bot": "bot", "assistant": "bot", "ai": "bot", "бот": "bot",
    "system": "system", "система": "system", "event": "system",
}

LANG_ALIASES = {"ru": "RU", "rus": "RU", "kz": "KZ", "kk": "KZ", "kaz": "KZ", "en": "EN", "eng": "EN"}

_KZ_LETTERS = re.compile(r"[әғқңөұүһі]", re.IGNORECASE)
_CYR = re.compile(r"[а-яё]", re.IGNORECASE)
_LAT = re.compile(r"[a-z]", re.IGNORECASE)


class ChannelAdapter(Protocol):
    channel_codes: set[str]

    def normalize(self, record: dict[str, Any], dataset: str, seq: int = 0) -> Conversation: ...


def norm_speaker(value: Any) -> str:
    return SPEAKER_ALIASES.get(str(value or "").strip().lower(), "system")


def detect_lang(text: str) -> str | None:
    """Грубый детектор RU/KZ/EN. Текст в «кавычках» (названия пунктов меню) игнорируется."""
    t = re.sub(r"«[^»]*»|\"[^\"]*\"", " ", text or "")
    if _KZ_LETTERS.search(t):
        return "KZ"
    cyr, lat = len(_CYR.findall(t)), len(_LAT.findall(t))
    if cyr == 0 and lat == 0:
        return None
    return "RU" if cyr >= lat else "EN"


def norm_language(value: Any, turns_text: list[str] | None = None) -> str:
    code = LANG_ALIASES.get(str(value or "").strip().lower())
    if code:
        return code
    for text in turns_text or []:
        d = detect_lang(text)
        if d:
            return d
    return "RU"
