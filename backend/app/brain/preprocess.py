"""Этап 0: нумерованный транскрипт, язык клиента и оператора, NLP-сигналы."""
from __future__ import annotations

import re

from app.adapters.base import detect_lang
from app.brain import signals as S
from app.schemas import Conversation, Signals

LONG_GAP_SEC = 120          # ожидание оператора дольше 2 минут после реплики клиента — подсказка для dialogue
BOT_TEXT_LIMIT = 600        # длинные шаблоны бота сокращаем в промпте (нумерация не меняется)
SPEAKER_LABEL = {"client": "client", "operator": "operator", "bot": "bot", "system": "system"}


def compute_signals(conv: Conversation) -> Signals:
    ops = [(t.idx, t.text) for t in conv.turns if t.speaker == "operator"]
    system = [(t.idx, t.text) for t in conv.turns if t.speaker == "system"]
    sig = Signals()
    sig.operator_turns = [i for i, _ in ops]
    sig.no_operator = not ops
    sig.client_lang = conv.language

    if ops:
        first_idx, first_text = ops[0]
        sig.first_operator_idx = first_idx
        sig.last_operator_idx = ops[-1][0]
        sig.operator_lang_first_turn = detect_lang(_first_sentence(first_text)) or detect_lang(first_text)
        sig.greeting_turns = S.find_turns(S.GREETING, ops[:2])
        sig.intro_turns = S.find_turns(S.INTRO, ops[:2])
        sig.farewell_turns = S.find_turns(S.FAREWELL, ops[-3:])
        sig.help_offer_turns = S.find_turns(S.HELP_OFFER, ops[-4:])
        sig.critical_pattern_turns = S.find_turns(S.CRITICAL, ops)
        sig.critical_pattern_hits = S.find_hits(S.CRITICAL, ops)
        sig.wait_warning_turns = S.find_turns(S.WAIT_WARNING, ops)

    sig.verification_system_turns = S.find_turns(S.VERIFICATION, system)
    sig.abrupt_end_turns = S.find_turns(S.ABRUPT_END, system)
    sig.operator_closed_turns = S.find_turns(S.OPERATOR_CLOSED, system)

    # Обрыв: системное событие ухода клиента/обрыва связи; в чате ещё — последняя содержательная реплика клиента
    # без ответа оператора. В звонках порядок реплик ASR неточен, поэтому только системные события.
    content = [t for t in conv.turns if t.speaker in ("client", "operator")]
    last_is_client = bool(content) and content[-1].speaker == "client" and bool(ops)
    if conv.is_speech:
        sig.chat_ended_abruptly = bool(sig.abrupt_end_turns)
    else:
        sig.chat_ended_abruptly = bool(sig.abrupt_end_turns) or (last_is_client and not sig.operator_closed_turns)

    # Длинные паузы оператора после реплики клиента (кроме первой реакции оператора).
    if ops and not conv.is_speech:
        first_op = ops[0][0]
        pending_client = None
        for t in conv.turns:
            if t.speaker == "client":
                pending_client = pending_client or t
            elif t.speaker == "operator":
                if pending_client and t.idx > first_op and t.offset_sec is not None and pending_client.offset_sec is not None:
                    gap = t.offset_sec - pending_client.offset_sec
                    if gap >= LONG_GAP_SEC:
                        sig.long_gaps.append({"after_idx": pending_client.idx, "reply_idx": t.idx, "seconds": int(gap)})
                pending_client = None
    return sig


def _first_sentence(text: str) -> str:
    for part in re.split(r"(?<=[.!?])\s+", text or ""):
        if re.search(r"[^\W\d_]{3,}", part):
            return part
    return text or ""


def _ts(t) -> str:  # noqa: ANN001
    if t.offset_sec is None:
        return t.ts_raw
    sec = int(t.offset_sec)
    return f"{sec // 60:02d}:{sec % 60:02d}"


def render_transcript(conv: Conversation) -> str:
    lines = []
    for t in conv.turns:
        text = " ".join((t.text or "").split())
        if t.speaker == "bot" and len(text) > BOT_TEXT_LIMIT:
            text = text[:BOT_TEXT_LIMIT].rstrip() + " …[сокращено]"
        lines.append(f"[{t.idx}] {SPEAKER_LABEL[t.speaker]} ({_ts(t)}): {text}")
    return "\n".join(lines)


def _list(v: list[int]) -> str:
    return ", ".join(f"[{i}]" for i in v) if v else "нет"


LANG_NAMES = {"RU": "русский", "KZ": "казахский", "EN": "английский"}


def render_signals(sig: Signals) -> str:
    lines = [
        f"- Первая реплика оператора: {('[' + str(sig.first_operator_idx) + ']') if sig.first_operator_idx else 'оператор не отвечал'}",
        f"- Язык клиента (по данным): {LANG_NAMES.get(sig.client_lang or '', sig.client_lang or '?')}",
        f"- Язык первой реплики оператора (детектор): {LANG_NAMES.get(sig.operator_lang_first_turn or '', 'не определён')}",
        f"- Приветствие в первых репликах оператора: {_list(sig.greeting_turns)}",
        f"- Представление (имя) в первых репликах оператора: {_list(sig.intro_turns)}",
        f"- Прощание в последних репликах оператора: {_list(sig.farewell_turns)}",
        f"- Итог / предложение дальнейшей помощи: {_list(sig.help_offer_turns)}",
        f"- Системное подтверждение личности: {_list(sig.verification_system_turns)}",
        f"- Предупреждение об ожидании: {_list(sig.wait_warning_turns)}",
        "- Долгие паузы оператора после вопроса клиента (кроме первой реакции): "
        + (", ".join(f"после [{g['after_idx']}] ответ в [{g['reply_idx']}] через {g['seconds'] // 60} мин {g['seconds'] % 60} с"
                     for g in sig.long_gaps) or "нет"),
        f"- Шаблоны возможных грубых нарушений: {'; '.join(sig.critical_pattern_hits) or 'нет'}",
        f"- Обрыв диалога (уход клиента / потеря связи / вопрос без ответа): {'да ' + _list(sig.abrupt_end_turns) if sig.chat_ended_abruptly else 'нет'}",
        f"- Оператор закрыл чат (системное событие): {_list(sig.operator_closed_turns)}",
    ]
    return "\n".join(lines)
