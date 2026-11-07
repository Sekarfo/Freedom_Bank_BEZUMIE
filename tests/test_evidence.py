"""evidence.py: правильная цитата проходит, выдуманная — нет, номер вне диапазона ловится."""
from app.brain.evidence import quote_score, verify_evidence


def test_exact_quote_verified(conv):
    ev = verify_evidence([4], "Меня зовут Алина", conv)
    assert ev.status == "verified" and ev.matched_turn == 4 and ev.ok


def test_case_punctuation_and_yo_normalized(conv):
    ev = verify_evidence([5], "в веб версии откройте вкладку кабинет", conv)
    assert ev.status == "verified"


def test_fuzzy_quote_with_typo_verified(conv):
    ev = verify_evidence([5], "Название тарифа будет под вашим ФИ0", conv)  # опечатка 0 вместо О
    assert ev.status == "verified"


def test_fabricated_quote_rejected(conv):
    ev = verify_evidence([4], "Переведите деньги мне на карту, я всё сделаю", conv)
    assert ev.status == "unverified" and not ev.ok


def test_out_of_range_turn_caught(conv):
    ev = verify_evidence([99, 4], "Меня зовут Алина", conv)
    assert ev.out_of_range == [99] and ev.status == "verified" and ev.turns == [4]
    ev2 = verify_evidence([0, 50], "", conv)
    assert ev2.status == "missing"


def test_wrong_turn_number_corrected(conv):
    ev = verify_evidence([5], "Хорошего вечера!", conv)  # цитата из реплики 8
    assert ev.status == "corrected" and ev.matched_turn == 8 and 8 in ev.turns and ev.ok


def test_quote_with_ellipsis_and_prefix(conv):
    assert quote_score("[4] operator (01:15): Здравствуйте! … Меня зовут Алина", conv.turns[3].text) >= 0.85
    ev = verify_evidence([4], "Здравствуйте! ... Понимаю, что искать неудобно.", conv)
    assert ev.status == "verified"


def test_no_quote_status(conv):
    ev = verify_evidence([4], "", conv)
    assert ev.status == "no_quote" and not ev.ok
