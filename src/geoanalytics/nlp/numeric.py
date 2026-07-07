"""F5 (Волна 3): numeric extraction — числа событий из текста новости.

Извлекает три вида фактов высокоточными правилами (без модели):
    dividend     — дивиденд на акцию, руб («дивиденды в размере 5,19 руб. на акцию»);
    key_rate     — ключевая ставка, % («снизил ключевую ставку до 14%»);
    deal_amount  — сумма сделки в валюте («продал "Авто.ру" за 35 млрд рублей»);
    target_price — целевая цена/таргет брокера, руб/$ («целевая цена 350 руб») [F10].

Зачем: структурированные события вычислимы без LLM-пересказа — «дивиденд 25₽
при цене 250₽ = 10% доходности». Тексты — короткие RSS-лиды с шаблонными
формулировками, поэтому правила, а не дистилляция: объём (~400 статей с
числами) не оправдывает encoder-QA, а Qwen-7B остаётся эталоном для eval
(scripts/eval_numeric.py). Приоритет — precision: извлечённые числа идут в
расчёты, ложное число хуже пропуска.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DIVIDEND, KEY_RATE, DEAL_AMOUNT = "dividend", "key_rate", "deal_amount"
TARGET_PRICE = "target_price"  # F10: целевая цена/таргет брокера (руб/$)
KINDS = (DIVIDEND, KEY_RATE, DEAL_AMOUNT, TARGET_PRICE)

# Число в русской записи: запятая или точка как десятичный разделитель,
# пробельные группы тысяч («1 200,5»); \s покрывает nbsp.
_NUM = r"(\d{1,3}(?:\s\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"

_MULT = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}
_CUR_SYMBOL = {"$": "USD", "€": "EUR", "₽": "RUB"}

# «дивиденды … 5,19 руб. на акцию» / «дивиденды в размере ₽37,64 на акцию».
# Окно до суммы ограничено, чтобы не уехать в соседнее предложение.
_DIV_RE = re.compile(
    r"дивиденд[а-яё]*[^;!?]{0,80}?(₽\s*)?" + _NUM +
    r"\s*(руб[а-яё.]*|₽)?\s+на\s+(?:одну\s+|обыкновенную\s+|привилегированную\s+)?акци",
    re.IGNORECASE,
)

# «ключевую ставку … до 14%» / «на уровне 16%» / «составляет 21%».
# Диапазоны без решения («ставка 10-12%») не имеют целевого слова — не матчатся.
# Только единственное число: «три ключевые ставки ЕЦБ» — чужой ЦБ, не наша ставка.
_RATE_RE = re.compile(
    r"ключев(?:ая|ой|ую)\s+ставк[а-яё]*[^;!?]{0,80}?"
    r"(?:до|на\s+уровне|составля[а-яё]+|в\s+размере)\s*" + _NUM + r"\s*%",
    re.IGNORECASE,
)
# Прогноз/ожидание («Шохин допустил снижение до 14%») — это не значение ставки;
# маркер в окне перед матчем отменяет извлечение (по золоту Qwen 2026-06-12).
_FORECAST_RE = re.compile(
    r"\b(ожида|допуст|прогноз|может|вероятн|не\s+исключ)", re.IGNORECASE
)
_RATE_WINDOW = 80  # символов до матча, в которых ищем маркер прогноза

# Сумма сделки: только предлог «за» («продал … за 35 млрд рублей») — каноничная
# формулировка цены сделки. «на»/«в» давали агрегаты («нетто-покупки на 19,9 млрд»,
# «купит валюту на 208,2 млрд») — это потоки, не сделки (адъюдикация 2026-06-12).
_AMOUNT_RE = re.compile(
    r"\bза\s+(?:около|примерно|почти)?\s*([$€₽])?\s*" + _NUM +
    r"\s*(тыс|млн|млрд|трлн)\.?\s*(руб[а-яё.]*|долл[а-яё.]*|евро|юан[а-яё]*)?",
    re.IGNORECASE,
)
# Сумма считается сделкой только при глаголе сделки в окне ПЕРЕД ней — это
# отсекает обороты выручки («продажи выросли до $110 млрд») и бюджетные суммы.
_DEAL_TRIGGER_RE = re.compile(
    r"\b(сделк|выкуп|приобрет|приобрел|закупи|купи[лт]|покупк|продал|прода[её]т|оценив)",
    re.IGNORECASE,
)
_DEAL_WINDOW = 100  # символов до суммы, в которых ищем глагол сделки

# F10: целевая цена брокера — «целевая цена 350 руб» / «таргет — ₽350» / «справедливая
# стоимость $42». Валюта обязательна (precision-first), как у дивиденда. Проценты (потенциал
# роста «+20%») — это НЕ цена, ловятся отдельно (потенциал в роутере, не как target_price).
_TARGET_RE = re.compile(
    r"(?:целев(?:ая|ой|ую)\s+цен[а-яё]*|таргет[а-яё]*|"
    r"справедлив(?:ая|ой|ую)\s+(?:цен[а-яё]*|стоимост[а-яё]*))"
    r"[^;!?%]{0,60}?([$€₽]\s*)?" + _NUM +
    r"\s*(руб[а-яё.]*|₽|\$|долл[а-яё.]*|евро)?",
    re.IGNORECASE,
)

_SNIPPET_MAX = 200


@dataclass(frozen=True)
class NumericFact:
    """Извлечённое число: kind из KINDS, unit — RUB/USD/EUR/CNY/pct."""

    kind: str
    value: float
    unit: str
    snippet: str


def _to_float(raw: str) -> float:
    return float(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))


def _currency(symbol: str | None, word: str | None) -> str | None:
    if symbol:
        return _CUR_SYMBOL[symbol]
    if not word:
        return None
    low = word.lower()
    if low.startswith("руб"):
        return "RUB"
    if low.startswith("долл"):
        return "USD"
    if low.startswith("евро"):
        return "EUR"
    if low.startswith("юан"):
        return "CNY"
    return None


def _snippet(text: str, m: re.Match) -> str:
    return text[m.start():m.end()][:_SNIPPET_MAX]


def extract_numbers(text: str) -> list[NumericFact]:
    """Все числовые факты текста, без дублей, в порядке появления."""
    out: list[NumericFact] = []

    def add(fact: NumericFact) -> None:
        if all((fact.kind, fact.value, fact.unit) !=
               (f.kind, f.value, f.unit) for f in out):
            out.append(fact)

    for m in _DIV_RE.finditer(text):
        # Без признака валюты («дивиденды 5 на акцию») — слишком сомнительно.
        if m.group(1) or m.group(3):
            add(NumericFact(DIVIDEND, _to_float(m.group(2)), "RUB", _snippet(text, m)))

    for m in _RATE_RE.finditer(text):
        window = text[max(0, m.start() - _RATE_WINDOW):m.end()]
        if _FORECAST_RE.search(window):
            continue
        add(NumericFact(KEY_RATE, _to_float(m.group(1)), "pct", _snippet(text, m)))

    for m in _AMOUNT_RE.finditer(text):
        currency = _currency(m.group(1), m.group(4))
        if currency is None:
            continue
        window = text[max(0, m.start() - _DEAL_WINDOW):m.start()]
        if not _DEAL_TRIGGER_RE.search(window):
            continue
        value = _to_float(m.group(2)) * _MULT[m.group(3).lower()]
        add(NumericFact(DEAL_AMOUNT, value, currency, _snippet(text, m)))

    for m in _TARGET_RE.finditer(text):
        pre, num, post = m.group(1), m.group(2), m.group(3)
        sym = (pre or "").strip() or None
        if sym is None and post and post.strip() in _CUR_SYMBOL:
            sym = post.strip()
            post = None
        currency = _currency(sym, post)
        if currency is None:  # без признака валюты «таргет 350» — слишком сомнительно
            continue
        add(NumericFact(TARGET_PRICE, _to_float(num), currency, _snippet(text, m)))

    return out
