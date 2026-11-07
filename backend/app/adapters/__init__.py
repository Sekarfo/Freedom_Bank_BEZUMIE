"""Реестр адаптеров каналов. Новый канал = новый класс адаптера, конвейер не меняется."""
from __future__ import annotations

from typing import Any

from app.adapters.base import ChannelAdapter
from app.adapters.chat import ChatAdapter, parse_mmss
from app.adapters.voice import VoiceAdapter, parse_iso
from app.schemas import Conversation

ADAPTERS: list[ChannelAdapter] = [ChatAdapter(), VoiceAdapter()]


def pick_adapter(record: dict[str, Any]) -> ChannelAdapter:
    code = str(record.get("channel") or "").strip().lower()
    for adapter in ADAPTERS:
        if code in adapter.channel_codes:
            return adapter
    # Неизвестный канал: определяем по формату меток времени и наличию записи.
    transcript = record.get("transcript") or []
    first_ts = transcript[0].get("timestamp") if transcript else None
    if record.get("audio_url") or (first_ts and parse_mmss(first_ts) is None and parse_iso(first_ts)):
        return ADAPTERS[1]
    return ADAPTERS[0]


def normalize_record(record: dict[str, Any], dataset: str, seq: int = 0) -> Conversation:
    if not record.get("conversation_id"):
        raise ValueError("В записи нет conversation_id")
    if not isinstance(record.get("transcript"), list):
        raise ValueError(f"{record.get('conversation_id')}: поле transcript должно быть массивом")
    return pick_adapter(record).normalize(record, dataset, seq)


def extract_records(payload: Any) -> list[dict[str, Any]]:
    """Принимает формат организаторов, {records: [...]}, список записей или одну запись."""
    if isinstance(payload, dict):
        if isinstance(payload.get("data"), dict) and "records" in payload["data"]:
            return list(payload["data"]["records"] or [])
        if "records" in payload:
            return list(payload["records"] or [])
        if "transcript" in payload:
            return [payload]
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    raise ValueError("Не удалось найти записи диалогов в JSON (ожидается data.records, records или список)")


__all__ = ["ADAPTERS", "ChatAdapter", "VoiceAdapter", "normalize_record", "extract_records", "pick_adapter"]
