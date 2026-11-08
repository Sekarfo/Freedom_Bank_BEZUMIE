"""Регулярные правила: структурированные персональные данные (номера, адреса, коды) в RU / KZ / EN."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterator

from app.brain.pii.checksums import digits, iban_ok, iin_ok, luhn_ok, phone_canon, text_canon

# Чем выше приоритет, тем сильнее тип при пересечении найденных фрагментов.
PRIORITY: dict[str, int] = {
    "SECRET": 100, "EMAIL": 95, "IBAN": 90, "CARD": 85, "IIN": 80, "PHONE": 75, "DOB": 70,
    "DOC": 65, "ACCOUNT": 60, "ADDRESS": 55, "PERSON": 40, "NUMBER": 30,
}


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str
    key: str          # нормализованное значение: одинаковый key → одна и та же метка
    source: str = "rule"

    @property
    def priority(self) -> int:
        return PRIORITY[self.kind]


@dataclass(frozen=True)
class Rule:
    kind: str
    pattern: re.Pattern[str]
    group: str | int = 0
    check: Callable[[str], bool] | None = None
    canon: Callable[[str], str] = digits

    def find(self, text: str) -> Iterator[Span]:
        for m in self.pattern.finditer(text):
            value = m.group(self.group)
            if not value or (self.check is not None and not self.check(value)):
                continue
            yield Span(m.start(self.group), m.end(self.group), self.kind, self.canon(value), f"rule:{self.kind}")


# --------------------------------------------------------------------------- общие фрагменты
_L = "A-Za-zА-Яа-яЁёӘәІіҢңҒғҮүҰұҚқӨөҺһ"
_UP = "A-ZА-ЯЁӘІҢҒҮҰҚӨҺ"
_NUM_SIGN = r"(?:№|N|#|nr\.?|no\.?|номер\w*|нөмір\w*)?"
_CURRENCY = (r"(?:тг|тенге|теңге|₸|kzt|руб\w*|р\.|₽|usd|\$|долл\w*|dollars?|eur|€|евро|%|процент\w*|шт|акци\w*|"
             r"лот\w*|тысяч\w*|тыс\.?|млн|миллион\w*|млрд|миллиард\w*|мың|thousand|million)")
_MONTHS = (r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|қаңтар|ақпан|наурыз|"
           r"сәуір|мамыр|маусым|шілде|тамыз|қыркүйек|қазан|қараша|желтоқсан|january|february|march|april|may|june|"
           r"july|august|september|october|november|december)\w*")
_DATE = (r"(?:\d{1,2}[./\-]\d{1,2}[./\-](?:19|20)?\d{2}|\d{1,2}\s+" + _MONTHS + r"\s+(?:19|20)\d{2}(?:\s*(?:г\.?|года?|жыл\w*))?"
         r"|(?:19|20)\d{2}[./\-]\d{1,2}[./\-]\d{1,2})")

# Слова после «кодовое слово», которые не являются самим словом («кодовое слово не помню»).
_NOT_A_SECRET = (r"(?!(?:не|нет|я|мы|вы|у|в|и|а|для|это|был\w*|забыл\w*|помн\w*|назов\w*|назвать|скаж\w*|подскаж\w*|"
                 r"верн\w*|неверн\w*|правильн\w*|какое|какой|ваше|ваш|моё|мое|от|is|not|please|жоқ|емес)\b)")

_STREET_KW = (r"(?:улиц\w*|ул\.|проспект\w*|просп\.|пр-т\w*|переул\w*|пер\.|бульвар\w*|б-р|шоссе|микрорайон\w*|мкр\.?|"
              r"мкрн\.?|жилой\s+комплекс|жк|street|avenue|ave\.|road)")
_HOUSE = (r"(?:[,\s]+(?:(?i:дом|д\.|үй|house|bldg\.?)\s*)?\d+[а-яa-z]?(?:/\d+)?)"
          r"(?:[,\s]+(?i:корпус|корп\.?|к\.|блок)\s*\d+[а-яa-z]?)?"
          r"(?:[,\s]+(?i:квартира|кв\.?|пәтер|apt\.?|apartment|офис|оф\.)\s*\d+[а-яa-z]?)?")
_WORD_NOT_NUM = rf"(?!\d+\b)[{_L}0-9\-]+\.?"
_STREET_TAIL = rf"(?:[{_UP}][{_L}\-]*|би|батыр\w*|ата|хан\w*)"   # продолжение названия: «Толе би», «Абылай хана»
# После номера дома не должно идти единиц измерения: «на улице было 5 градусов» — не адрес.
_NOT_UNIT = r"(?!\s*(?:градус\w*|минут\w*|час\w*|секунд\w*|раз\b|" + _CURRENCY[3:] + r")"

RULES: tuple[Rule, ...] = (
    # --- коды и пароли: никогда не должны уходить наружу
    Rule("SECRET", re.compile(
        r"(?i:cvv2?|cvc2?|пин[\s\-]?код\w*|pin(?:[\s\-]?code)?|смс[\s\-]?код\w*|sms[\s\-]?code|код\w*\s+(?:из|с)\s+(?:смс|sms)|"
        r"код\w*\s+подтверждени\w*|одноразов\w+\s+код\w*|верификационн\w+\s+код\w*|verification\s+code|"
        r"one[\s\-]?time\s+(?:password|code)|otp|растау\s+код\w*)\s*(?:[:\-–—]|это|is)?\s*(?P<val>\d{3,8})(?!\d)"),
        group="val", canon=str),
    Rule("SECRET", re.compile(
        r"(?i:парол\w*|password|passcode|құпия\s?сөз\w*)\s*(?:[:\-–—]|это|is)?\s*(?P<val>(?=[^\s,;]*\d)[^\s,;]{4,32})"),
        group="val", canon=str),
    Rule("SECRET", re.compile(
        r"(?i:(?:кодов|секретн|контрольн)\w*\s+слов\w*|code\s*word|secret\s+word|кодтық\s+сөз\w*)\s*(?:[:\-–—]|это|is)?\s*"
        r"[«\"']?" + _NOT_A_SECRET + rf"(?P<val>[{_L}0-9]{{2,32}})", re.I),
        group="val", canon=text_canon),

    # --- e-mail, IBAN, телефоны с «+», карты, ИИН
    Rule("EMAIL", re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.I), canon=str.lower),
    Rule("IBAN", re.compile(r"(?<![A-Za-z0-9])KZ\d{2}(?: ?[A-Z0-9]){16}(?![A-Za-z0-9])", re.I),
         check=iban_ok, canon=lambda s: re.sub(r"\s", "", s).upper()),
    Rule("IBAN", re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}[A-Z0-9]{11,30}(?![A-Za-z0-9])"), check=iban_ok, canon=str.upper),
    Rule("PHONE", re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s\-()]{0,2}\d){8,12}(?!\d)"), canon=phone_canon),
    Rule("CARD", re.compile(
        r"(?<![\d\-])(?:\d{13,19}|\d{4}(?:([ \-])\d{4}){2}\1\d{1,7}|\d{4}([ \-])\d{6}\2\d{5})(?![\d\-])"), check=luhn_ok),
    Rule("IIN", re.compile(r"(?<!\d)\d{12}(?!\d)"), check=iin_ok),
    Rule("PHONE", re.compile(r"(?<![\d+])[78][\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"), canon=phone_canon),

    # --- контекстные: значение рядом с ключевым словом
    Rule("DOB", re.compile(
        r"(?:дат\w*\s+рождени\w*|д\.\s?р\.|родил\w*|туған\s+(?:күн\w*|жыл\w*)|date\s+of\s+birth|d\.?o\.?b\.?|born(?:\s+on)?)"
        r"\s*[:\-–—]?\s*(?:в\s+|on\s+)?(?P<val>" + _DATE + ")", re.I), group="val", canon=text_canon),
    Rule("DOC", re.compile(
        r"(?:удостоверени\w*(?:\s+личности)?|уд\.\s*личности|паспорт\w*|passport|id[\s\-]?card|жеке\s+куәлік\w*|куәлік\w*|"
        r"вид\s+на\s+жительство|driver'?s?\s+licen[cs]e|водительск\w+\s+удостоверени\w*)\s*" + _NUM_SIGN
        + r"\s*:?\s*(?P<val>[A-Za-z]{0,2}\d{6,10})(?!\d)", re.I), group="val", canon=text_canon),
    Rule("ACCOUNT", re.compile(
        r"(?:сч[её]т\w*|л/с|договор\w*|контракт\w*|account|acc\.|шот\w*|келісім\s?шарт\w*|субсч[её]т\w*)\s*" + _NUM_SIGN
        + r"\s*:?\s*(?P<val>[A-Za-z]{0,3}\d{5,20})(?!\d|[.,]\d)(?!\s*" + _CURRENCY + r")", re.I),
         group="val", canon=text_canon),

    # --- адреса
    # «улица Абая 150, кв. 12»; название с заглавной — номер дома необязателен
    Rule("ADDRESS", re.compile(
        r"(?i:" + _STREET_KW + rf")\s+[{_UP}0-9][{_L}0-9\-]*\.?(?:\s+{_STREET_TAIL}){{0,2}}(?:{_HOUSE})?{_NOT_UNIT}"), canon=text_canon),
    # «на улице абая 150» (расшифровка звонка в нижнем регистре) — только с номером дома
    Rule("ADDRESS", re.compile(r"(?i:" + _STREET_KW + rf")\s+{_WORD_NOT_NUM}(?:\s+{_WORD_NOT_NUM}){{0,2}}{_HOUSE}{_NOT_UNIT}"),
         canon=text_canon),
    # «Абай даңғылы 150», «Самал шағын ауданы»
    Rule("ADDRESS", re.compile(
        rf"[{_UP}][{_L}\-]+(?:\s+[{_UP}][{_L}\-]+)?\s+(?i:көшесі|даңғылы|шағын\s+ауданы|ықшам\s+ауданы)(?:{_HOUSE})?"),
         canon=text_canon),
    # «дом 5, кв. 12» без улицы
    Rule("ADDRESS", re.compile(
        r"(?i:дом|д\.|үй)\s*\d+[а-яa-z]?(?:/\d+)?[,\s]+(?i:квартира|кв\.?|пәтер|apt\.?)\s*\d+[а-яa-z]?"), canon=text_canon),
    Rule("ADDRESS", re.compile(r"(?i:почтов\w*\s+индекс|индекс|postal\s+code|zip(?:\s+code)?)\s*[:\-]?\s*(?P<val>\d{6})(?!\d)"),
         group="val"),

    # --- прочие длинные номера (12 цифр без валидного ИИН, 16 без Луна и т.п.)
    Rule("NUMBER", re.compile(r"(?<!\d)\d{10,}(?!\d)")),
)
