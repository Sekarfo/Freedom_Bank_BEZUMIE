"""Шаблоны промптов. Версия каждого шаблона сохраняется в журнале оценки.

Системная часть одинакова для всех диалогов одной версии чек-листа (кэшируется у Claude),
всё, что зависит от диалога, — в пользовательской части.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.brain.preprocess import render_signals, render_transcript
from app.checklist.render import render_checklist
from app.schemas import ChecklistConfig, Conversation, Signals, Verdict

PROMPT_DIR = Path(__file__).resolve().parent

PROMPT_VERSIONS: dict[str, str] = {
    "primary": "primary-v1",
    "review": "review-v1",
    "arbiter": "arbiter-v1",
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


@lru_cache(maxsize=32)
def _template(name: str) -> str:
    return (PROMPT_DIR / f"{name}_system.md").read_text(encoding="utf-8")


def system_prompt(kind: str, checklist: ChecklistConfig) -> str:
    return _template(kind).replace("{{CHECKLIST}}", render_checklist(checklist))


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


def _verdict_brief(v: Verdict) -> dict[str, Any]:
    return {"criterion_id": v.criterion_id, "value": v.value, "rationale": v.rationale,
            "evidence_turns": v.evidence_turns, "quote": v.quote, "confidence": v.confidence}


def review_user_prompt(conv: Conversation, signals: Signals, local_verdicts: dict[str, Verdict],
                       checklist: ChecklistConfig) -> str:
    rows = []
    for c in checklist.criteria:
        v = local_verdicts.get(c.criterion_id)
        rows.append(_verdict_brief(v) if v else {"criterion_id": c.criterion_id, "value": "НЕТ ВЕРДИКТА"})
    return (user_prompt(conv, signals)
            + "\n\n# Вердикты младшего оценщика\n"
            + json.dumps(rows, ensure_ascii=False, indent=1))


def arbiter_user_prompt(conv: Conversation, signals: Signals, disputed: list[str],
                        a: dict[str, Verdict], b: dict[str, Verdict]) -> str:
    rows = []
    for cid in disputed:
        rows.append({"criterion_id": cid,
                     "оценщик_A": _verdict_brief(a[cid]) if cid in a else None,
                     "оценщик_B": _verdict_brief(b[cid]) if cid in b else None})
    return (user_prompt(conv, signals)
            + "\n\n# Спорные критерии и мнения оценщиков\n"
            + json.dumps(rows, ensure_ascii=False, indent=1))
