"""Маскирование персональных данных (brain/pii): правила, номера словами, имена (NER + контекст), конвейер."""
import asyncio
import importlib.util
import json

import pytest

from app.adapters import normalize_record
from app.brain import pii
from app.brain.pii import Masker, mask_conversation
from app.brain.pii.checksums import iban_ok, iin_ok, luhn_ok
from app.brain.pii.spoken import words_to_digits
from app.brain.pipeline import Pipeline, cache_key
from app.schemas import EvaluationOptions
from conftest import EXAMPLE_VALUES, SAMPLE_RECORD, MockClient, judge_json

IIN = "900101300017"           # валидный контрольный разряд
CARD = "4111 1111 1111 1111"   # проходит алгоритм Луна
IBAN = "KZ86125KZT5004100100"  # валидная контрольная сумма mod 97
HAS_NER = importlib.util.find_spec("natasha") is not None and importlib.util.find_spec("slovnet") is not None
needs_ner = pytest.mark.skipif(not HAS_NER, reason="natasha/slovnet не установлены")


class FakeNER:
    """Детерминированный NER для тестов: «находит» заданные слова."""
    name = "fake-ner"

    def __init__(self, *names: str):
        self.names = names

    def persons(self, text):
        out = []
        for n in self.names:
            i = text.find(n)
            while i >= 0:
                out.append((i, i + len(n)))
                i = text.find(n, i + 1)
        return out


def mask(text: str, ner=None) -> str:
    return Masker(ner).mask(text)


# --------------------------------------------------------------------------- контрольные суммы
def test_checksums():
    assert iin_ok(IIN) and not iin_ok("900101300015")
    assert luhn_ok(CARD) and not luhn_ok("4111 1111 1111 1112")
    assert iban_ok(IBAN) and iban_ok("KZ86 125K ZT50 0410 0100") and not iban_ok("KZ87125KZT5004100100")


# --------------------------------------------------------------------------- структурированные данные
@pytest.mark.parametrize("text, expected", [
    (f"Мой ИИН {IIN}", "Мой ИИН [IIN_1]"),
    (f"карта {CARD}", "карта [CARD_1]"),
    ("карта 4111111111111111.", "карта [CARD_1]."),
    (f"IBAN {IBAN}", "IBAN [IBAN_1]"),
    ("IBAN KZ86 125K ZT50 0410 0100 please", "IBAN [IBAN_1] please"),
    ("звоните +7 701 123-45-67", "звоните [PHONE_1]"),
    ("тел. 8 (701) 123 45 67", "тел. [PHONE_1]"),
    ("пишите на Ivan.Petrov@mail.kz", "пишите на [EMAIL_1]"),
    ("удостоверение № 045678912 выдано 12.03.2020", "удостоверение № [DOC_1] выдано 12.03.2020"),
    ("Жеке куәлік нөмірі 045678912", "Жеке куәлік нөмірі [DOC_1]"),
    ("Номер счёта: 1234567, всё верно", "Номер счёта: [ACCOUNT_1], всё верно"),
    ("договор №987654 от 01.02.2025", "договор №[ACCOUNT_1] от 01.02.2025"),
    ("account no. 5512345", "account no. [ACCOUNT_1]"),
    ("дата рождения 12.03.1985", "дата рождения [DOB_1]"),
    ("date of birth: 3 March 1990", "date of birth: [DOB_1]"),
    ("Скажите код из SMS: 4821", "Скажите код из SMS: [SECRET_1]"),
    ("cvv 123, пин-код 0000", "cvv [SECRET_1], пин-код [SECRET_2]"),
    ("пароль Qwerty123!", "пароль [SECRET_1]"),
    ("Кодовое слово Барсик", "Кодовое слово [SECRET_1]"),
    ("живу на улице Абая 150, кв. 12", "живу на [ADDRESS_1]"),
    ("на улице абая 150 кв 12", "на [ADDRESS_1]"),
    ("Абай даңғылы 150", "[ADDRESS_1]"),
    ("дом 5, кв. 12", "[ADDRESS_1]"),
    ("индекс 050000", "индекс [ADDRESS_1]"),
    ("код 900101300015", "код [NUMBER_1]"),            # 12 цифр без валидного ИИН — всё равно скрыто
    ("счёт 4111111111111111", "счёт [CARD_1]"),          # пересечение: карта сильнее счёта
    ("карта 1234 5678 9012 3456", "карта [NUMBER_1]"),    # без Луна (опечатка?) — всё равно скрыта
])
def test_masks(text, expected):
    assert mask(text) == expected


@pytest.mark.parametrize("text", [
    "Пополните счёт 50000 тенге",
    "на счёт 250000 ₸",
    "комиссия 1500000 тг, сумма 1 000 000 ₸",
    "время 12:30, дата 12.03.2024, заявка 12345",
    "договор от 01.02.2025",
    "карта 4400 **** **** 1234",          # уже частично скрыта
    "курс 512.35, 0.5%",
    "[Имя скрыто], пункт меню 3",
    "Кодовое слово не помню",
    "пароль не приходит",
    "на улице было холодно",
    "на улице было 5 градусов",
    "пятьдесят тысяч тенге, сто двадцать пять долларов, в двенадцать тридцать",
    "Откройте «Меню → Отчёты и документы → Отчёт брокера»",
])
def test_not_masked(text):
    assert mask(text) == text


def test_same_value_same_token_across_formats():
    m = Masker()
    a = m.mask("звоните +7 701 123-45-67")
    b = m.mask("или 8 701 123 45 67, а второй +7 702 000 11 22")
    assert a == "звоните [PHONE_1]"
    assert b == "или [PHONE_1], а второй [PHONE_2]"
    assert m.counts["PHONE"] == 3 and m.distinct == {"PHONE": 2}


# --------------------------------------------------------------------------- номера словами (звонки)
@pytest.mark.parametrize("words, digits", [
    ("восемь семьсот один сто двадцать три сорок пять шестьдесят семь", "87011234567"),
    ("восемь семь ноль один", "8701"),
    ("сто двенадцать", "112"),
    ("сегіз жеті жүз бір жүз жиырма үш қырық бес алпыс жеті", "87011234567"),
    ("он бір", "11"),
    ("eight seven oh one double five", "870155"),
])
def test_words_to_digits(words, digits):
    assert words_to_digits(words.split()) == digits


@pytest.mark.parametrize("text, expected", [
    ("номер восемь семьсот один сто двадцать три сорок пять шестьдесят семь", "номер [PHONE_1]"),
    ("иин девять ноль ноль один ноль один три ноль ноль ноль один семь", "иин [IIN_1]"),
    ("мой иин 900 101 300 017", "мой иин [IIN_1]"),
    ("my number is seven seven zero one one two three four five six seven", "my number is [PHONE_1]"),
    ("удостоверение ноль четыре пять шесть семь восемь девять один два", "удостоверение [NUMBER_1]"),
])
def test_spoken_numbers(text, expected):
    assert mask(text) == expected


# --------------------------------------------------------------------------- имена
@pytest.mark.parametrize("text, expected", [
    ("Здравствуйте! Меня зовут Алина, чем могу помочь?", "Здравствуйте! Меня зовут [PERSON_1], чем могу помочь?"),
    ("меня зовут асель, слушаю", "меня зовут [PERSON_1], слушаю"),          # ASR в нижнем регистре
    ("Сәлеметсіз бе! Менің атым Ерлан.", "Сәлеметсіз бе! Менің атым [PERSON_1]."),
    ("Сәлеметсіз бе, мен Әсел.", "Сәлеметсіз бе, мен [PERSON_1]."),
    ("Hello, my name is John Smith.", "Hello, my name is [PERSON_1]."),
    ("Hi, I'm Kate from support.", "Hi, I'm [PERSON_1] from support."),
    ("Здравствуйте, я Лиза.", "Здравствуйте, я [PERSON_1]."),
    ("С вами на связи оператор Динара.", "С вами на связи оператор [PERSON_1]."),
    ("Ивановой Марии Петровне отправлено", "[PERSON_1] отправлено"),
    ("Уважаемый Ерлан Серикұлы, добрый день", "Уважаемый [PERSON_1], добрый день"),
    ("меня зовут как?", "меня зовут как?"),                               # служебное слово — не имя
    ("Меня зовут Алина, Freedom Broker", "Меня зовут [PERSON_1], Freedom Broker"),
    ("Я Вам перезвоню", "Я Вам перезвоню"),
])
def test_context_names(text, expected):
    assert mask(text) == expected


def test_ner_precision_filters():
    ner = FakeNER("Айгерим", "Тапсырманы", "Отчёт", "Иванов Пётр")
    # одиночное слово в начале предложения от NER не принимается (типичная ошибка на казахском)
    assert mask("Тапсырманы орындаңыз.", ner) == "Тапсырманы орындаңыз."
    # внутри кавычек — название пункта меню
    assert mask("Откройте «Отчёт брокера»", ner) == "Откройте «Отчёт брокера»"
    # посреди фразы и из двух слов — принимается
    assert mask("Добрый день, Айгерим, ваш менеджер Иванов Пётр", ner) == "Добрый день, [PERSON_1], ваш менеджер [PERSON_2]"


def test_ner_boundaries_inside_word_ignored():
    class Cut:
        name = "cut"

        def persons(self, text):
            i = text.find("ен")
            return [(i, i + 2)]
    assert mask("саясатымен таныстыңыз", Cut()) == "саясатымен таныстыңыз"


def test_name_propagates_across_turns():
    rec = json.loads(json.dumps(SAMPLE_RECORD))
    rec["transcript"][5]["text"] = "Нашла, спасибо, алина. А комиссия где?"
    res = mask_conversation(normalize_record(rec, "TEST"))
    texts = [t.text for t in res.conv.turns]
    assert "Меня зовут [PERSON_1]." in texts[3]       # «Меня зовут Алина» — контекст
    assert "спасибо, [PERSON_1]" in texts[5]          # повтор в другой реплике и в нижнем регистре


@needs_ner
@pytest.mark.parametrize("text, expected", [
    ("Добрый день, Айгерим Сериковна. Ваш менеджер Иванов Пётр свяжется с вами.",
     "Добрый день, [PERSON_1]. Ваш менеджер [PERSON_2] свяжется с вами."),
    ("Тапсырманы орындаңыз. Шотты тексеріңіз.", "Тапсырманы орындаңыз. Шотты тексеріңіз."),
    ("Переключите меня на оператора.", "Переключите меня на оператора."),
])
def test_real_ner(text, expected):
    assert mask(text, pii.get_ner("slovnet")) == expected


# --------------------------------------------------------------------------- диалог
def _pii_record():
    rec = json.loads(json.dumps(SAMPLE_RECORD))
    rec["conversation_id"] = "test-pii-001"
    rec["transcript"][0]["text"] = f"Где посмотреть мой тариф? Мой ИИН {IIN}, телефон +7 701 123-45-67."
    rec["transcript"][4]["text"] = ("В веб-версии откройте вкладку «Кабинет». Ваш счёт № 7712345, "
                                    "название тарифа будет под вашим ФИО.")
    return rec


def test_mask_conversation_keeps_original_and_reports_no_values():
    conv = normalize_record(_pii_record(), "TEST")
    res = mask_conversation(conv)
    assert IIN in conv.turns[0].text                       # исходный диалог не изменён
    assert IIN not in res.conv.turns[0].text and "[IIN_1]" in res.conv.turns[0].text
    assert "[ACCOUNT_1]" in res.conv.turns[4].text and "[PERSON_1]" in res.conv.turns[3].text
    assert res.turns == [1, 4, 5] and res.conv.raw == {}
    assert [t.idx for t in res.conv.turns] == [t.idx for t in conv.turns]
    report = json.dumps(res.report(), ensure_ascii=False)
    for secret in (IIN, "7712345", "Алина"):
        assert secret not in report
    assert res.report()["replacements"] == {"IIN": 1, "PHONE": 1, "ACCOUNT": 1, "PERSON": 1}


def test_mask_cache_returns_independent_copies():
    conv = normalize_record(_pii_record(), "TEST")
    a = mask_conversation(conv)
    a.masker.mask("ещё один номер +7 777 000 00 00")
    b = mask_conversation(conv)
    assert b.masker.distinct == {"IIN": 1, "PHONE": 1, "ACCOUNT": 1, "PERSON": 1}
    assert [t.text for t in a.conv.turns] == [t.text for t in b.conv.turns]


# --------------------------------------------------------------------------- конвейер
OPTS = EvaluationOptions(provider="anthropic")


def test_pipeline_sends_only_masked_text(checklist):
    conv = normalize_record(_pii_record(), "TEST")
    overrides = {"consultation": {"evidence_turns": [5], "quote": "Ваш счёт № [ACCOUNT_1]"},
                 "contact": {"evidence_turns": [4], "quote": "Здравствуйте! Меня зовут [PERSON_1]."}}
    judge = MockClient([judge_json(EXAMPLE_VALUES, overrides=overrides)])
    out = asyncio.run(Pipeline(lambda p: judge).evaluate(conv, checklist, OPTS))

    sent = judge.calls[0]["system"] + judge.calls[0]["user"]
    for secret in (IIN, "701 123-45-67", "7712345", "Алина"):
        assert secret not in sent
    assert "[IIN_1]" in sent and "[ACCOUNT_1]" in sent and "# Маскирование персональных данных" in sent

    assert out.status == "ok" and out.score == 85.38
    by = {c["criterion_id"]: c for c in out.criteria}
    assert by["consultation"]["evidence_status"] == "verified"   # цитата с меткой сверена с тем же текстом
    assert by["contact"]["evidence_status"] == "verified"
    assert out.audit["masking"]["replacements"]["PERSON"] == 1
    stored = json.dumps(out.model_dump(), ensure_ascii=False)
    assert IIN not in stored and "7712345" not in stored


def test_fail_closed_when_ner_unavailable(checklist, monkeypatch):
    def broken(kind):
        raise pii.MaskingUnavailable("модель не найдена")
    monkeypatch.setattr(pii, "get_ner", broken)
    conv = normalize_record(_pii_record(), "TEST")
    judge = MockClient([judge_json(EXAMPLE_VALUES)])
    out = asyncio.run(Pipeline(lambda p: judge).evaluate(conv, checklist, OPTS))
    assert out.status == "error" and "Маскирование персональных данных недоступно" in out.error
    assert judge.calls == []                                     # в модель ничего не ушло
    assert pii.status(type("S", (), {"pii_masking": True, "pii_ner": "slovnet"})())["ner_ready"] is False


def test_cache_key_changes_only_when_text_masked(checklist, monkeypatch):
    from app import config

    plain = normalize_record(json.loads(json.dumps(dict(SAMPLE_RECORD, transcript=[
        {"timestamp": "00:00", "speaker": "client", "text": "Где посмотреть тариф?"},
        {"timestamp": "00:05", "speaker": "operator", "text": "Здравствуйте! Откройте «Кабинет»."}]))), "TEST")
    pii_conv = normalize_record(_pii_record(), "TEST")
    on = (cache_key(plain, checklist, "anthropic", "m"), cache_key(pii_conv, checklist, "anthropic", "m"))
    monkeypatch.setenv("PII_MASKING", "false")
    config.get_settings.cache_clear()
    try:
        off = (cache_key(plain, checklist, "anthropic", "m"), cache_key(pii_conv, checklist, "anthropic", "m"))
    finally:
        monkeypatch.delenv("PII_MASKING")
        config.get_settings.cache_clear()
    assert on[0] == off[0]      # без персональных данных промпт тот же — прежний кэш в силе
    assert on[1] != off[1]      # маскирование изменило промпт — новый ключ


def test_masking_can_be_disabled(checklist, monkeypatch):
    from app import config

    monkeypatch.setenv("PII_MASKING", "false")
    config.get_settings.cache_clear()
    try:
        conv = normalize_record(_pii_record(), "TEST")
        judge = MockClient([judge_json(EXAMPLE_VALUES)])
        out = asyncio.run(Pipeline(lambda p: judge).evaluate(conv, checklist, OPTS))
    finally:
        config.get_settings.cache_clear()
    assert IIN in judge.calls[0]["user"] and out.audit["masking"] == {"enabled": False}
