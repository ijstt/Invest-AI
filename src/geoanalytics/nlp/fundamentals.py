"""Извлечение фундаментальных метрик из текста отчётов эмитентов (H5) — БЕЗ модели, rule-based.

Тот же precision-first подход, что в [[numeric]] (F5): берём число ТОЛЬКО когда рядом узнаваемая
метка метрики и явная единица (млрд/млн руб. — масштаб; % / руб. на акцию — для коэффициентов).
Тексты отчётов шаблонны («Выручка составила 7 200 млрд руб.»), поэтому правила точнее дистилляции.
PDF→текст делает обёртка (`analytics.fundamentals`); здесь — чистая функция над текстом.

Метрики: revenue (выручка), net_profit (чистая прибыль), ebitda, assets (активы), equity
(капитал), eps (прибыль на акцию), dividend (дивиденд на акцию), pe (P/E).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from geoanalytics.nlp.numeric import MULT, to_float

_SNIPPET_MAX = 160
_WINDOW = 90                   # символов после метки метрики, где ищем число

# Метка метрики → варианты формулировки (нижний регистр; latin для EBITDA/OIBDA).
_TRIGGERS: dict[str, tuple[str, ...]] = {
    "revenue": ("выручка", "выручки", "выручку"),
    "net_profit": ("чистая прибыль", "чистой прибыли", "чистую прибыль"),
    "ebitda": ("ebitda", "oibda"),
    "assets": ("совокупные активы", "сумма активов", "активы составили", "итого активы"),
    "equity": ("собственный капитал", "капитал составил", "акционерный капитал"),
    "eps": ("прибыль на акцию", "eps"),
    "dividend": ("дивиденд", "дивиденды", "дивидендов"),
}

# Денежная величина со шкалой и валютой: «7 200 млрд руб.» / «1 234,5 млрд рублей».
_AMOUNT_SCALED = re.compile(
    r"(\d[\d  ]*(?:[.,]\d+)?)\s*(тыс|млн|млрд|трлн)\.?\s*"
    r"(руб[а-яё.]*|долл[а-яё.]*|\$|евро|юан[а-яё]*)",
    re.IGNORECASE,
)
# Сумма на акцию: «34,84 руб. на акцию» (eps/dividend).
_PER_SHARE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(руб[а-яё.]*|долл[а-яё.]*|\$)\.?\s*на\s+акци",
    re.IGNORECASE,
)
# Коэффициент P/E: «P/E 5,2» / «P/E составил 5.2».
_PE = re.compile(r"p\s*/\s*e[^\d]{0,12}(\d+(?:[.,]\d+)?)", re.IGNORECASE)

_CUR = (("руб", "RUB"), ("долл", "USD"), ("$", "USD"), ("евро", "EUR"), ("юан", "CNY"))


@dataclass(frozen=True)
class FundamentalFact:
    """Фундаментальная метрика: значение в базовой единице (RUB/доля/коэффициент)."""

    metric: str                # revenue/net_profit/ebitda/assets/equity/eps/dividend/pe
    value: float
    unit: str                  # RUB/USD/EUR/CNY | ratio
    period: str | None         # «2024» / «2024-H1» / «2024-9M» | None
    snippet: str


def _currency(word: str) -> str:
    low = (word or "").lower()
    for pref, code in _CUR:
        if low.startswith(pref) or pref == low[:1]:
            return code
    return "RUB"


def detect_period(text: str) -> str | None:
    """Отчётный период из текста: год + опц. квалификатор (полугодие/9 мес./квартал). None — нет."""
    ym = re.search(r"\b(20[2-3]\d)\b", text)
    if not ym:
        return None
    year = ym.group(1)
    low = text.lower()
    if re.search(r"1\s*полугоди|первое полугоди|6 месяц", low):
        return f"{year}-H1"
    if re.search(r"9 месяц|девять месяц", low):
        return f"{year}-9M"
    if re.search(r"1 квартал|первый квартал", low):
        return f"{year}-Q1"
    if re.search(r"3 квартал|третий квартал", low):
        return f"{year}-Q3"
    return year


def extract_fundamentals(text: str, *, period: str | None = None) -> list[FundamentalFact]:
    """Все фундаментальные факты текста (precision-first), без дублей по метрике (первое вхождение).

    `period` переопределяет автодетект (для отчётов, где год не в шапке)."""
    if not text:
        return []
    per = period or detect_period(text)
    low = text.lower()
    seen: set[str] = set()
    facts: list[FundamentalFact] = []

    for metric, triggers in _TRIGGERS.items():
        for trig in triggers:
            idx = low.find(trig)
            if idx == -1:
                continue
            window = text[idx:idx + len(trig) + _WINDOW]
            fact = _match_metric(metric, trig, window, per)
            if fact and metric not in seen:
                seen.add(metric)
                facts.append(fact)
            break

    if "pe" not in seen:
        pe = _PE.search(text)
        if pe:
            facts.append(FundamentalFact(
                metric="pe", value=to_float(pe.group(1)), unit="ratio", period=per,
                snippet=text[max(0, pe.start() - 10):pe.end()][:_SNIPPET_MAX]))
    return facts


def _match_metric(metric: str, trig: str, window: str,
                  period: str | None) -> FundamentalFact | None:
    """Находит число для метрики в окне после метки. eps/dividend — на акцию, прочие — со шкалой."""
    if metric in ("eps", "dividend"):
        m = _PER_SHARE.search(window)
        if m:
            return FundamentalFact(metric=metric, value=to_float(m.group(1)),
                                   unit=_currency(m.group(2)), period=period,
                                   snippet=window[:_SNIPPET_MAX])
        return None
    m = _AMOUNT_SCALED.search(window)
    if not m:
        return None
    value = to_float(m.group(1)) * MULT[m.group(2).lower()]
    return FundamentalFact(metric=metric, value=value, unit=_currency(m.group(3)),
                           period=period, snippet=window[:_SNIPPET_MAX])
