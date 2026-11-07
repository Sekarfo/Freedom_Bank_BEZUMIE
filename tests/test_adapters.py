"""Адаптеры: исходные метки времени сохраняются, idx = позиция в исходном массиве."""
import json
from pathlib import Path

import pytest

from app.adapters import extract_records, normalize_record
from conftest import ROOT, SAMPLE_RECORD

VOICE_RECORD = {
    "conversation_id": "test-call-1",
    "channel": "voice call",
    "language": "RU",
    "audio_url": "https://example.org/audio-page",
    "transcript": [
        {"timestamp": "2026-11-03T13:10:00.6388245+00:00", "speaker": "system", "text": "Запись и транскрибирование запущены."},
        {"timestamp": "2026-11-03T13:10:10.0000000+00:00", "speaker": "bot", "text": "Для русского языка нажмите 2."},
        {"timestamp": "2026-11-03T13:10:20.6388245+00:00", "speaker": "system", "text": "Запись и транскрибирование приостановлены."},
        {"timestamp": "2026-11-03T13:10:30.6388245+00:00", "speaker": "system", "text": "Запись и транскрибирование возобновлены."},
        {"timestamp": "2026-11-03T13:10:40.6388245+00:00", "speaker": "operator", "text": "Здравствуйте, меня зовут [Имя скрыто]."},
        {"timestamp": "2026-11-03T13:10:35.6388245+00:00", "speaker": "client", "text": "Алло."},
    ],
}


def test_chat_keeps_raw_timestamps_and_order():
    conv = normalize_record(SAMPLE_RECORD, "DEV")
    assert conv.channel == "chat" and conv.language == "RU" and conv.audio_url is None
    assert [t.idx for t in conv.turns] == list(range(1, len(SAMPLE_RECORD["transcript"]) + 1))
    for turn, raw in zip(conv.turns, SAMPLE_RECORD["transcript"]):
        assert turn.ts_raw == raw["timestamp"]
        assert turn.speaker == raw["speaker"]
        assert turn.text == raw["text"]
    assert conv.turns[3].offset_sec == 75.0  # 01:15


def test_voice_offsets_and_pause_subtraction():
    conv = normalize_record(VOICE_RECORD, "DEV")
    assert conv.channel == "voice" and conv.is_speech and conv.audio_url
    assert conv.turns[0].ts_raw == VOICE_RECORD["transcript"][0]["timestamp"]  # 7 знаков дроби сохранены
    assert conv.turns[4].offset_sec == 40.0
    # пауза записи 10 с (20→30) вычитается из позиции в аудио
    assert conv.turns[4].audio_offset_sec == 30.0
    # реплика с более ранней меткой остаётся на своём месте (без пересортировки)
    assert conv.turns[5].idx == 6 and conv.turns[5].offset_sec == 35.0


def test_unknown_speaker_and_language_normalization():
    rec = {"conversation_id": "x", "channel": "agent chat", "language": "kk",
           "transcript": [{"timestamp": "00:01", "speaker": "Agent", "text": "Сәлеметсіз бе"}]}
    conv = normalize_record(rec, "upload")
    assert conv.language == "KZ" and conv.turns[0].speaker == "operator"


def test_extract_records_formats():
    assert len(extract_records({"isSuccess": True, "data": {"records": [SAMPLE_RECORD]}})) == 1
    assert len(extract_records({"records": [SAMPLE_RECORD, SAMPLE_RECORD]})) == 2
    assert len(extract_records([SAMPLE_RECORD])) == 1
    assert len(extract_records(SAMPLE_RECORD)) == 1
    with pytest.raises(ValueError):
        extract_records({"foo": 1})


def test_missing_transcript_rejected():
    with pytest.raises(ValueError):
        normalize_record({"conversation_id": "x", "channel": "agent chat"}, "upload")


DEV_FILE = ROOT / "data" / "raw" / "dev_dataset.json"


@pytest.mark.skipif(not DEV_FILE.exists(), reason="DEV-набор не скачан")
def test_real_dev_dataset_roundtrip():
    payload = json.loads(Path(DEV_FILE).read_text(encoding="utf-8"))
    records = extract_records(payload)
    assert len(records) == payload["data"]["total_records"]
    for rec in records:
        conv = normalize_record(rec, "DEV")
        assert conv.n_turns == len(rec["transcript"])
        assert all(t.ts_raw == r["timestamp"] for t, r in zip(conv.turns, rec["transcript"]))
        if rec["channel"] == "voice call":
            assert conv.audio_url and all(t.offset_sec is not None for t in conv.turns)
