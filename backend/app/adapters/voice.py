"""Адаптер звонка: метки ISO 8601 с часовым поясом + ссылка на запись.

offset_sec        — секунды от самой ранней метки диалога;
audio_offset_sec  — позиция в аудиофайле: от события «Запись ... запущена» с вычетом пауз записи
                    («приостановлены» → «возобновлены»). Допущение: во время паузы звук не пишется.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.adapters.base import norm_language, norm_speaker
from app.schemas import Conversation, Turn

_FRAC = re.compile(r"(\.\d{6})\d+")

REC_START = re.compile(r"запись и транскрибирование запущены|жазу және транскрипция басталды|recording and transcription (?:started|have started)", re.I)
REC_PAUSE = re.compile(r"приостановлен|тоқтатыл|paused", re.I)
REC_RESUME = re.compile(r"возобновлен|жалғастырыл|қайта басталды|resumed", re.I)


def parse_iso(ts: Any) -> datetime | None:
    s = str(ts or "").strip()
    if not s:
        return None
    s = _FRAC.sub(r"\1", s.replace("Z", "+00:00"))
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


class VoiceAdapter:
    channel_codes = {"voice call", "voice", "call", "phone"}
    channel = "voice"

    def normalize(self, record: dict[str, Any], dataset: str, seq: int = 0) -> Conversation:
        transcript = record.get("transcript") or []
        parsed = [parse_iso(item.get("timestamp")) for item in transcript]
        valid = [p for p in parsed if p is not None]
        base = min(valid) if valid else None

        # Позиция в аудио: старт записи и паузы.
        rec_start = None
        pauses: list[tuple[float, float]] = []
        pause_open: float | None = None
        for item, dt in zip(transcript, parsed):
            if dt is None or base is None or norm_speaker(item.get("speaker")) != "system":
                continue
            text = str(item.get("text") or "")
            t = (dt - base).total_seconds()
            if rec_start is None and REC_START.search(text):
                rec_start = t
            elif REC_PAUSE.search(text) and pause_open is None:
                pause_open = t
            elif REC_RESUME.search(text) and pause_open is not None:
                pauses.append((pause_open, t))
                pause_open = None
        if pause_open is not None:
            pauses.append((pause_open, float("inf")))
        rec_start = rec_start or 0.0

        def audio_pos(t: float) -> float:
            paused = 0.0
            for a, b in pauses:
                if t >= b:
                    paused += b - a
                elif t > a:
                    paused += t - a
            return max(0.0, t - rec_start - paused)

        turns: list[Turn] = []
        for i, (item, dt) in enumerate(zip(transcript, parsed), start=1):
            offset = (dt - base).total_seconds() if (dt is not None and base is not None) else None
            turns.append(Turn(
                idx=i,
                speaker=norm_speaker(item.get("speaker")),
                text=str(item.get("text") or ""),
                ts_raw="" if item.get("timestamp") is None else str(item.get("timestamp")),
                offset_sec=round(offset, 3) if offset is not None else None,
                audio_offset_sec=round(audio_pos(offset), 3) if offset is not None else None,
            ))
        lang = norm_language(record.get("language"), [t.text for t in turns if t.speaker == "client"])
        return Conversation(
            conversation_id=str(record.get("conversation_id")),
            dataset=dataset,
            channel="voice",
            language=lang,
            turns=turns,
            audio_url=record.get("audio_url") or None,
            is_speech=True,
            raw=record,
        )
