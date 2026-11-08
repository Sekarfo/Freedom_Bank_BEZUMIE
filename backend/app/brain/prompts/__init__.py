"""Шаблоны промптов. Версия каждого шаблона сохраняется в журнале оценки.

Системная часть одинакова для всех диалогов одной версии чек-листа (кэшируется у Claude),
всё, что зависит от диалога, — в пользовательской части.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.brain.pii import LABELS
from app.brain.preprocess import render_signals, render_transcript
from app.checklist.render import render_checklist
from app.reasons.taxonomy import ReasonTaxonomy, render_taxonomy
from app.schemas import ChecklistConfig, Conversation, Signals

PROMPT_DIR = Path(__file__).resolve().parent

PROMPT_VERSIONS: dict[str, str] = {
    "judge": "judge-v2",   # v2: причина обращения и исход (блок reason)
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


MASKING_NOTES = (
    "Персональные данные в транскрипте заменены метками до отправки модели: "
    + ", ".join(f"[{kind}_n] — {label}" for kind, label in LABELS.items()) + ". "
    "Одинаковое значение в диалоге получает одну и ту же метку. Метка означает, что в реплике было реальное значение: "
    "«меня зовут [PERSON_1]» — оператор назвал своё имя; если оператор назвал [ACCOUNT_1] или [CARD_1] клиента "
    "до подтверждения личности, это сообщение конфиденциальных данных так же, как с исходным номером; "
    "если оператор попросил [SECRET_1] (код из SMS, PIN, CVV, пароль), это запрос секретных данных. "
    "Не снижай оценку за сами метки и не считай их ошибкой оператора. В цитатах сохраняй метки как есть."
)


@lru_cache(maxsize=4)
def _template() -> str:
    return (PROMPT_DIR / "judge_system.md").read_text(encoding="utf-8")


def system_prompt(checklist: ChecklistConfig, taxonomy: ReasonTaxonomy | None = None) -> str:
    """Чек-лист и справочник причин одинаковы для всех диалогов → системный промпт кэшируется у Claude."""
    text = _template().replace("{{CHECKLIST}}", render_checklist(checklist))
    if taxonomy is None:
        return text.replace("{{REASONS}}", "").replace("{{REASON_FORMAT}}", "")
    reasons = "\n# Причина обращения\n" + render_taxonomy(taxonomy) + "\n"
    return (text.replace("{{REASONS}}", reasons)
            .replace("{{REASON_FORMAT}}", "затем `reason` (причина обращения и исход), "))


def user_prompt(conv: Conversation, signals: Signals, masked: bool = False) -> str:
    """masked — в транскрипте есть метки маскирования (пояснение добавляется только тогда,
    поэтому промпт диалогов без персональных данных не меняется и их кэш остаётся в силе)."""
    parts = [
        "# Метаданные",
        f"- Канал: {CHANNEL_NAMES.get(conv.channel, conv.channel)}",
        f"- Язык клиента (по данным): {LANG_NAMES.get(conv.language, conv.language)}",
        f"- Число реплик: {conv.n_turns}",
    ]
    if conv.is_speech:
        parts += ["", "# Особенности звонка", VOICE_NOTES]
    if masked:
        parts += ["", "# Маскирование персональных данных", MASKING_NOTES]
    parts += ["", "# Подсказки предобработки (эвристики, могут ошибаться)", render_signals(signals),
              "", "# Транскрипт", render_transcript(conv)]
    return "\n".join(parts)
