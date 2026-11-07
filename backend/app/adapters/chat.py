"""Адаптер текстового чата: метки MM:SS (или HH:MM:SS)."""
from __future__ import annotations

import re
from typing import Any

from app.adapters.base import norm_language, norm_speaker
from app.schemas import Conversation, Turn

_MMSS = re.compile(r"^\s*(\d+):(\d{1,2})(?::(\d{1,2}))?(?:[.,](\d+))?\s*$")


def parse_mmss(ts: Any) -> float | None:
    m = _MMSS.match(str(ts or ""))
    if not m:
        return None
    a, b, c, frac = m.groups()
    if c is None:
        sec = int(a) * 60 + int(b)
    else:
        sec = int(a) * 3600 + int(b) * 60 + int(c)
    if frac:
        sec += float("0." + frac)
    return float(sec)


class ChatAdapter:
    channel_codes = {"agent chat", "chat", "web chat", "messenger"}
    channel = "chat"

    def normalize(self, record: dict[str, Any], dataset: str, seq: int = 0) -> Conversation:
        transcript = record.get("transcript") or []
        turns: list[Turn] = []
        for i, item in enumerate(transcript, start=1):  # нумерация строго по исходному массиву
            ts_raw = "" if item.get("timestamp") is None else str(item.get("timestamp"))
            turns.append(Turn(
                idx=i,
                speaker=norm_speaker(item.get("speaker")),
                text=str(item.get("text") or ""),
                ts_raw=ts_raw,
                offset_sec=parse_mmss(ts_raw),
            ))
        lang = norm_language(record.get("language"), [t.text for t in turns if t.speaker == "client"])
        channel = "messenger" if str(record.get("channel", "")).lower() == "messenger" else "chat"
        return Conversation(
            conversation_id=str(record.get("conversation_id")),
            dataset=dataset,
            channel=channel,
            language=lang,
            turns=turns,
            audio_url=None,
            is_speech=False,
            raw=record,
        )
