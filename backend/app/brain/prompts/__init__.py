"""Шаблоны промптов. Версия каждого шаблона сохраняется в журнале оценки.

Системная часть одинакова для всех диалогов одной версии чек-листа (кэшируется у Claude),
всё, что зависит от диалога, — в пользовательской части.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.brain.preprocess import render_signals, render_transcript
from app.checklist.render import render_checklist
from app.schemas import ChecklistConfig, Conversation, Signals

PROMPT_DIR = Path(__file__).resolve().parent

PROMPT_VERSIONS: dict[str, str] = {
    "judge": "judge-v1",
    "user": "user-v1",
}

CHANNEL_NAMES = {"chat": "текстовый чат", "voice": "телефонный звонок (расшифровка речи)",
                 "email": "электронное письмо", "messenger": "мессенджер"}
LANG_NAMES = {"RU": "русский", "KZ": "казахский", "EN": "английский"}

VOICE_NOTES = (
    "Это автоматическая расшифровка телефонного звонка. Не штрафуй оператора за ошибки распознавания речи: "
    "искажённые слова и названия компании (например, «Фридом брокер» может быть распознано как «федеброкер» или "
    "«fernetbark»), обрывки фраз, повторы, перебивания и неточный порядок реплик — это особенности устной речи и ASR. "
    "«[Имя скрыто]» и «[Персональные данные скрыты]» — данные, скрытые при анонимизации: «меня зовут [Имя скрыто]» "
    "означает, что оператор назвал своё имя. Ожидание в звонке и тишину на линии оценивай по смыслу реплик."
)


@lru_cache(maxsize=4)
def _template() -> str:
    return (PROMPT_DIR / "judge_system.md").read_text(encoding="utf-8")


def system_prompt(checklist: ChecklistConfig) -> str:
    return _template().replace("{{CHECKLIST}}", render_checklist(checklist))


def user_prompt(conv: Conversation, signals: Signals) -> str:
    parts = [
        "# Метаданные",
        f"- Канал: {CHANNEL_NAMES.get(conv.channel, conv.channel)}",
        f"- Язык клиента (по данным): {LANG_NAMES.get(conv.language, conv.language)}",
        f"- Число реплик: {conv.n_turns}",
    ]
    if conv.is_speech:
        parts += ["", "# Особенности звонка", VOICE_NOTES]
    parts += ["", "# Подсказки предобработки (эвристики, могут ошибаться)", render_signals(signals),
              "", "# Транскрипт", render_transcript(conv)]
    return "\n".join(parts)
