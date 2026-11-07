"""Словари и регулярные выражения RU/KZ/EN для NLP-сигналов.

Сигналы — подсказки, а не вердикты. Дополняем словари по мере просмотра DEV.
"""
from __future__ import annotations

import re

_F = re.IGNORECASE | re.UNICODE


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in patterns), _F)


GREETING = _rx(
    # RU
    r"\bздравствуйте\b", r"\bздравствуй\b", r"\bдобр(?:ый|ое|ого)\s+(?:день|утро|вечер|времени)",
    r"\bприветствую\b", r"\bпривет\b", r"\bдоброго\s+времени\s+суток\b",
    # KZ
    r"сәлеметсіз\s*бе", r"\bсәлем\b", r"қайырлы\s+(?:күн|таң|кеш)", r"\bармысыз\b", r"сәлеметсіздер\s*ме",
    # EN
    r"\bhello\b", r"\bhi\b", r"\bhey\b", r"\bgood\s+(?:morning|afternoon|evening|day)\b", r"\bgreetings\b",
)

INTRO = _rx(
    # RU: «меня зовут Алина», «я Тимур», «с вами Олег», «на связи». Имя — с заглавной (регистр важен: (?-i:...)).
    r"меня\s+зовут", r"(?-i:(?:^|[\s,.!])[Яя]\s*[-—]?\s*[А-ЯЁ][а-яё]+)", r"\bна\s+связи\b",
    r"(?-i:\b[Сс]\s+вами\s+[А-ЯЁ][а-яё]+)", r"\[имя скрыто\]",
    # KZ: «менің атым Аружан», «мен Дана», «Мен Нұрланмын»
    r"менің\s+атым", r"(?-i:\b[Мм]ен\s+[А-ЯЁӘҒҚҢӨҰҮҺІ][а-яёәғқңөұүһі]+)",
    # EN
    r"\bmy\s+name\s+is\b", r"(?-i:\b[Ii]['’]m\s+[A-Z][a-z]+)", r"(?-i:\b[Ii]\s+am\s+[A-Z][a-z]+)",
    r"(?-i:\b[Tt]his\s+is\s+[A-Z][a-z]+)",
)

FAREWELL = _rx(
    r"до\s+свидания", r"всего\s+(?:доброго|хорошего)", r"хорошего\s+(?:дня|вечера|утра|настроения)",
    r"до\s+встречи", r"удачного\s+дня", r"\bпрощайте\b",
    r"сау\s+бол(?:ыңыз|ыңыздар)", r"көріскенше", r"қош\s+бол", r"жақсы\s+күн", r"күніңіз\s+сәтті",
    r"\bgoodbye\b", r"\bbye\b", r"have\s+a\s+(?:nice|great|good)\s+(?:day|evening|one)", r"\btake\s+care\b",
)

HELP_OFFER = _rx(
    r"если\s+(?:будут|останутся|возникнут|появятся)\s+(?:ещё\s+|еще\s+)?вопрос", r"могу\s+(?:ли\s+)?(?:ещё|еще)\s+чем",
    r"\bобращайтесь\b", r"напишите\s+(?:нам|сюда|в\s+чат)", r"чем\s+(?:ещё|еще)\s+(?:могу|помочь)",
    r"остались\s+вопросы", r"\bитак\b", r"подвед[уё]м\s+итог",
    r"тағы\s+(?:сұрақ|сұрағыңыз)", r"сұрақтарыңыз\s+болса", r"бізге\s+жазыңыз", r"хабарласыңыз",
    r"anything\s+else", r"let\s+(?:us|me)\s+know", r"feel\s+free", r"reach\s+out", r"if\s+you\s+have\s+(?:any\s+)?(?:more|other)?\s*questions",
)

CRITICAL = _rx(
    # Деньги / услуги в обход компании
    r"(?:скиньте|переведите|перекиньте|отправьте)\s+(?:деньги\s+)?мне", r"мне\s+на\s+карту", r"на\s+мою\s+карту",
    r"мой\s+личный\s+номер", r"мой\s+номер\s+телефона", r"напишите\s+мне\s+(?:в\s+)?(?:whatsapp|ватсап|телеграм|telegram|личк)",
    r"частн(?:ая|ую|ой)\s+консультаци", r"лично\s+(?:вложу|инвестирую|проконсультирую)",
    r"маған\s+(?:ақша\s+)?(?:аударыңыз|жіберіңіз)", r"менің\s+картама", r"жеке\s+нөмірім",
    r"send\s+(?:the\s+)?money\s+to\s+me", r"transfer\s+(?:it\s+|the\s+money\s+)?to\s+my\s+(?:card|account)",
    r"my\s+personal\s+(?:number|phone|card)", r"private\s+consultation",
    # Оскорбления
    r"читать\s+умеете", r"\bидиот", r"\bтуп(?:ой|ая|ые|ица)\b", r"\bдура[кч]", r"\bбестолков", r"\bнедалёк",
    r"оқи\s+алмайсыз\s+ба", r"\bақымақ",
    r"can['’]?t\s+you\s+read", r"\bstupid\b", r"\bidiot\b", r"\bdumb\b",
)

VERIFICATION = _rx(
    r"личност[ьи]\s+клиента\s+подтвержден", r"идентификаци[яю]\s+(?:пройдена|успешн|подтвержд)",
    r"жеке\s+басы\s+расталды", r"сәйкестендіру\s+(?:сәтті|өтті)",
    r"identity\s+(?:has\s+been\s+)?verified", r"verification\s+(?:passed|successful|completed)",
)

ABRUPT_END = _rx(
    r"соединение\s+с\s+клиентом\s+прервано", r"клиент\s+покинул\s+чат",
    r"клиентпен\s+байланыс\s+үзілді", r"клиент\s+чаттан\s+шықты",
    r"connection\s+to\s+(?:the\s+)?customer\s+(?:was\s+)?lost", r"customer\s+left\s+the\s+chat",
)
# «Клиент завершил разговор» — штатное окончание звонка, не обрыв (используется только как контекст).
CALL_ENDED_BY_CLIENT = _rx(r"клиент\s+завершил\s+разговор", r"customer\s+ended\s+the\s+(?:call|conversation)")

OPERATOR_CLOSED = _rx(r"оператор\s+закрыл\s+чат", r"оператор\s+чатты\s+жапты", r"agent\s+closed\s+the\s+chat")

WAIT_WARNING = _rx(
    r"\bминут(?:у|ку|очку)\b", r"сейчас\s+(?:проверю|уточню|посмотрю)", r"\bожидайте\b", r"\bподождите\b",
    r"\bсекунд(?:у|очку)\b", r"дайте\s+(?:мне\s+)?(?:минуту|время)", r"нужно\s+(?:немного\s+)?времени",
    r"күте\s+тұрыңыз", r"бір\s+минут", r"қазір\s+(?:тексеремін|қараймын)",
    r"one\s+moment", r"\bjust\s+a\s+(?:moment|minute|sec)", r"let\s+me\s+check", r"please\s+wait", r"\bhold\s+on\b",
)


def find_turns(pattern: re.Pattern[str], turns: list[tuple[int, str]]) -> list[int]:
    return [idx for idx, text in turns if pattern.search(text or "")]


def find_hits(pattern: re.Pattern[str], turns: list[tuple[int, str]]) -> list[str]:
    hits = []
    for idx, text in turns:
        m = pattern.search(text or "")
        if m:
            hits.append(f"[{idx}] «{m.group(0)}»")
    return hits
