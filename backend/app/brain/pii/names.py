"""Имена людей: NER-модель + контекстные правила RU/KZ/EN + отчества.

NER — slovnet (проект Natasha): CPU, без сети, модель ~2 МБ и эмбеддинги ~25 МБ поставляются пакетом natasha.
Модель обучена на русском, но находит и казахские имена («Айгерим Сериковна», «Ерлан») и латиницу («John Smith»).
Контекстные правила добирают то, что NER пропускает: нижний регистр в расшифровке звонка («меня зовут асель»),
казахские и английские формулы представления («менің атым …», «my name is …»), ФИО с отчеством.
"""
from __future__ import annotations

import importlib.util
import logging
import re
import threading
from pathlib import Path
from typing import Iterator, Protocol

from app.brain.pii.rules import Span

log = logging.getLogger("cqa.pii")


class MaskingUnavailable(RuntimeError):
    """Маскирование включено, но детектор не загрузился. Диалог не отправляется в модель (fail-closed)."""


# --------------------------------------------------------------------------- нормализация имени
_ENDINGS = ("ами", "ями", "ого", "его", "ому", "ему", "ой", "ей", "ом", "ем", "ым", "им", "ах", "ях", "ую", "юю",
            "а", "я", "у", "ю", "е", "ы", "и", "ь", "й")


def name_stem(word: str) -> str:
    """«Алина», «Алины», «Алиной» → «алин»: одна метка для склонённых форм одного имени."""
    w = word.lower().replace("ё", "е")
    if len(w) > 4:
        for e in _ENDINGS:
            if w.endswith(e) and len(w) - len(e) >= 3:
                return w[: -len(e)]
    return w


# Слова, которые не являются именем, хотя стоят на его месте или пишутся с заглавной.
STOP = {
    # бренды и роли
    "freedom", "фридом", "фридомброкер", "broker", "брокер", "bank", "банк", "finance", "финанс", "aim", "tradernet",
    "kassa", "касса", "support", "поддержка", "оператор", "специалист", "менеджер", "консультант", "бот", "bot",
    "клиент", "client", "customer", "маман", "operator", "agent", "manager", "specialist", "assistant", "ассистент",
    # служебные слова после «меня зовут», «this is», «i am»
    "как", "не", "так", "что", "вот", "просто", "это", "да", "нет", "ну", "э", "ээ", "эм", "мм", "а", "и", "вам",
    "вас", "ваш", "ваша", "вы", "вами", "вашего", "вашей", "вашу", "сейчас", "уже", "the", "a", "an", "your", "from", "calling", "here", "sorry", "fine", "ok",
    "okay", "good", "glad", "happy", "not", "just", "going", "still", "also", "very", "really", "trying", "back",
    "мен", "сіз", "біз", "жоқ", "иә", "бұл", "здравствуйте", "hello", "hi", "спасибо", "thanks",
    # формулы представления и приветствия, которые NER иногда включает в имя
    "менің", "меня", "мое", "моё", "моя", "мой", "my", "атым", "есімім", "зовут", "имя", "фамилия", "скрыто",
    "добрый", "доброе", "день", "утро", "вечер", "сәлеметсіз", "сәлем", "қайырлы", "күн", "рахмет", "уважаемый",
    "уважаемая", "құрметті", "dear", "mr", "mrs", "ms", "miss", "господин", "госпожа",
}
# Имена, совпадающие с обычными словами: в остальном тексте маскируются только с заглавной буквы.
COMMON_WORD_NAMES = {"вер", "надежд", "любов", "роз", "лили", "слав", "мир", "лев", "заря", "ангел", "дан", "жан",
                     "ай", "нур", "бал", "гул", "сая", "мая", "may", "will", "mark", "grace", "joy", "hope"}

_UP = "A-ZА-ЯЁӘІҢҒҮҰҚӨҺ"
_LOW = "a-zа-яёәіңғүұқөһ"
_CAP = rf"[{_UP}][{_LOW}]+(?:-[{_UP}]?[{_LOW}]+)?"
_ANY = rf"[{_UP}{_LOW}]+(?:-[{_UP}{_LOW}]+)?"
_ROLE = r"(?:(?i:оператор|специалист|менеджер|консультант|сотрудник|маман|operator|specialist|agent|manager)\s+)?"
_STRONG = (r"(?i:меня\s+зовут|мо[её]\s+имя|моя\s+фамилия|менің\s+атым|атым|есімім|менің\s+есімім|"
           r"тегім|аты-жөнім|my\s+name\s+is|my\s+name's|name\s+is|full\s+name\s+is)")
_WEAK = (r"(?:(?i:с\s+вами\s+(?:на\s+связи|говорит|работает)|вас\s+беспокоит|уважаем(?:ый|ая|ые)|господин|госпожа|"
         r"құрметті|this\s+is|i\s+am|i'm|dear|mr\.?|mrs\.?|ms\.?|miss|оператор|специалист|менеджер|маман|operator|"
         r"фио|мо[её]\s+фио|клиент|клиентка|мен|я))")
_SEP = r"(?:\s*[,:\-–—]\s*|\s+)"

CONTEXT_STRONG = re.compile(r"(?<![\w])" + _STRONG + _SEP + _ROLE + rf"(?P<val>{_ANY}(?:\s+{_CAP}){{0,2}})")
CONTEXT_WEAK = re.compile(r"(?<![\w])" + _WEAK + _SEP + _ROLE + rf"(?P<val>{_CAP}(?:\s+{_CAP}){{0,2}})")
PATRONYMIC = re.compile(rf"(?<![\w])(?P<val>(?:{_CAP}\s+){{1,2}}[{_UP}][{_LOW}]+"
                        r"(?:(?:ович|евич|ьич)(?:а|у|ем|е)?|(?:овн|евн|ичн|иничн)(?:а|ы|е|ой|у)|ұлы|улы|қызы|кызы))(?![\w])")
_WORD = re.compile(rf"[{_UP}{_LOW}]+(?:-[{_UP}{_LOW}]+)?")


def _words(text: str, start: int, end: int) -> Iterator[re.Match[str]]:
    """Целые слова внутри [start, end): NER иногда возвращает границы посреди слова («саясатым|ен»)."""
    for m in _WORD.finditer(text):
        if m.end() <= start:
            continue
        if m.start() >= end:
            break
        if m.start() >= start and m.end() <= end:
            yield m


def _trim(text: str, start: int, end: int, require_cap: bool) -> Iterator[tuple[int, int]]:
    """Разбивает фрагмент на имена: отбрасывает служебные слова и (для NER) слова со строчной буквы."""
    run: list[tuple[int, int]] = []
    for m in _words(text, start, end):
        w = m.group(0)
        ok = w.lower() not in STOP and len(w) >= 2 and (not require_cap or w[0].isupper())
        if ok:
            run.append((m.start(), m.end()))
        elif run:
            yield run[0][0], run[-1][1]
            run = []
    if run:
        yield run[0][0], run[-1][1]


def person_key(fragment: str) -> str:
    return " ".join(name_stem(w) for w in _WORD.findall(fragment))


def find_context_names(text: str) -> Iterator[Span]:
    for rx, need_cap in ((CONTEXT_STRONG, False), (CONTEXT_WEAK, True), (PATRONYMIC, True)):
        for m in rx.finditer(text):
            for s, e in _trim(text, m.start("val"), m.end("val"), need_cap):
                yield Span(s, e, "PERSON", person_key(text[s:e]), "context")


# --------------------------------------------------------------------------- NER
class NERBackend(Protocol):
    name: str

    def persons(self, text: str) -> list[tuple[int, int]]: ...


class SlovnetNER:
    """NER из проекта Natasha (slovnet + navec). Модели берутся из пакета natasha; сам natasha не импортируется
    (он тянет pymorphy2, а для NER он не нужен)."""

    name = "slovnet-ner-news-v1"

    def __init__(self) -> None:
        spec = importlib.util.find_spec("natasha")
        if spec is None or not spec.submodule_search_locations:
            raise MaskingUnavailable("не установлен пакет natasha (модели NER): pip install -r backend/requirements.txt")
        data = Path(list(spec.submodule_search_locations)[0]) / "data"
        try:
            from navec import Navec
            from slovnet import NER
        except ImportError as exc:
            raise MaskingUnavailable(f"не установлены slovnet/navec: {exc}") from exc
        emb = Navec.load(str(data / "emb" / "navec_news_v1_1B_250K_300d_100q.tar"))
        self._ner = NER.load(str(data / "model" / "slovnet_ner_news_v1.tar"))
        self._ner.navec(emb)
        self._lock = threading.Lock()

    def persons(self, text: str) -> list[tuple[int, int]]:
        if not text or not text.strip():
            return []
        with self._lock:
            markup = self._ner(text)
        return [(s.start, s.stop) for s in markup.spans if s.type == "PER"]


_backend: NERBackend | None = None
_backend_error: str | None = None
_backend_lock = threading.Lock()


def get_ner(kind: str) -> NERBackend | None:
    """kind: slovnet | off. Ошибка загрузки запоминается и поднимается как MaskingUnavailable при каждом вызове."""
    global _backend, _backend_error
    if kind in ("off", "none", ""):
        return None
    if kind not in ("slovnet", "natasha"):
        raise MaskingUnavailable(f"неизвестный PII_NER={kind!r}; допустимо: slovnet | off")
    with _backend_lock:
        if _backend is None and _backend_error is None:
            try:
                _backend = SlovnetNER()
                log.info("PII NER загружен: %s", _backend.name)
            except MaskingUnavailable as exc:
                _backend_error = str(exc)
            except Exception as exc:  # noqa: BLE001  повреждённые файлы модели и т.п.
                _backend_error = f"{type(exc).__name__}: {exc}"
        if _backend_error is not None:
            raise MaskingUnavailable(f"NER для имён недоступен — {_backend_error}")
        return _backend


def sentence_initial(text: str, pos: int) -> bool:
    """Слово стоит в начале реплики или предложения (после . ! ? … : ; перевода строки или тире)."""
    before = text[:pos].rstrip(" \t«\"'(—–-")
    while before and before[-1] in "»\")'":
        before = before[:-1].rstrip()
    return not before or before[-1] in ".!?…:;\n"


def in_quotes(text: str, pos: int) -> bool:
    """Внутри «…» или "…" — названия пунктов меню и документов, а не люди."""
    before = text[:pos]
    return before.count("«") > before.count("»") or before.count('"') % 2 == 1


def find_ner_names(text: str, backend: NERBackend) -> Iterator[Span]:
    """Фильтр точности поверх NER. Модель обучена на русских новостях, и в коротких репликах чата (особенно
    на казахском) она часто принимает за имя первое слово предложения («Тапсырманы …», «Переключите …»).
    Поэтому одиночное слово в начале предложения не принимается: если это действительно имя, оно будет
    найдено в другом месте диалога (представление, обращение посреди фразы) и скроется распространением."""
    for s, e in backend.persons(text):
        for ts, te in _trim(text, s, e, require_cap=True):
            if in_quotes(text, ts):
                continue
            single = len(list(_words(text, ts, te))) == 1
            if single and sentence_initial(text, ts):
                continue
            yield Span(ts, te, "PERSON", person_key(text[ts:te]), "ner")
