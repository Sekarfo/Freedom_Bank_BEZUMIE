"""NLP-сигналы RU/KZ/EN."""
from app.adapters import normalize_record
from app.brain.preprocess import compute_signals


def _conv(lang, operator_texts, system_texts=()):
    transcript = [{"timestamp": "00:00", "speaker": "client", "text": "Вопрос"}]
    transcript += [{"timestamp": "00:10", "speaker": "system", "text": t} for t in system_texts]
    transcript += [{"timestamp": "01:00", "speaker": "operator", "text": t} for t in operator_texts]
    return normalize_record({"conversation_id": "s", "channel": "agent chat", "language": lang,
                             "transcript": transcript}, "TEST")


def test_ru_greeting_intro_farewell():
    s = compute_signals(_conv("RU", ["Здравствуйте, я Тимур. Чем помочь?", "Если вопрос останется, напишите нам. До свидания!"]))
    assert s.greeting_turns and s.intro_turns and s.farewell_turns and s.help_offer_turns
    assert s.operator_lang_first_turn == "RU"


def test_ru_no_false_intro():
    s = compute_signals(_conv("RU", ["Привет, где именно ищете? Я сейчас проверю."]))
    assert s.greeting_turns and not s.intro_turns


def test_kz_greeting_and_intro():
    s = compute_signals(_conv("KZ", ["Сәлеметсіз бе, мен Дана. Көмектесемін.", "Сау болыңыз!"]))
    assert s.greeting_turns and s.intro_turns and s.farewell_turns and s.operator_lang_first_turn == "KZ"
    s2 = compute_signals(_conv("KZ", ["Сәлем. Қай сайтқа кірдіңіз?"]))
    assert s2.greeting_turns and not s2.intro_turns


def test_en_and_language_mismatch():
    s = compute_signals(_conv("EN", ["Hi, I'm Maya from support.", "You're welcome. Take care!"]))
    assert s.intro_turns and s.farewell_turns and s.operator_lang_first_turn == "EN"
    s2 = compute_signals(_conv("KZ", ["Здравствуйте, меня зовут Алина."]))
    assert s2.operator_lang_first_turn == "RU" and s2.client_lang == "KZ"
    s3 = compute_signals(_conv("EN", ["I'm sorry to hear that."]))
    assert not s3.intro_turns


def test_language_by_first_sentence():
    s = compute_signals(_conv("RU", ["Здравствуйте! Я Кира. On Freedom Holding Corp. website open Investors, Financial Information."]))
    assert s.operator_lang_first_turn == "RU"


def test_critical_patterns_and_verification():
    s = compute_signals(_conv("RU", ["Здравствуйте, я Кирилл. Скиньте деньги мне на карту, я сам их вложу."],
                              ["Личность клиента подтверждена."]))
    assert s.critical_pattern_turns and s.verification_system_turns
    s2 = compute_signals(_conv("RU", ["Вы вообще читать умеете? Бот всё написал."]))
    assert s2.critical_pattern_turns


def test_abrupt_end():
    s = compute_signals(_conv("RU", ["Здравствуйте, я Ира."], ["Соединение с клиентом прервано."]))
    assert s.chat_ended_abruptly
