"""Скрейпер фундаментальных метрик эмитентов с smart-lab.ru (L1, долгосрочная ветка).

Smart-lab отдаёт годовую отчётность МСФО как HTML-таблицу, где каждая строка —
``<tr field="КЛЮЧ">`` с каноничным ключом метрики, человекочитаемой меткой и единицей
в ``<th><span>`` («млрд руб» / «%» / «руб/акцию»), и значениями по годам в ``<td>``.
Колонки выровнены позиционно (пустые ячейки за отсутствующий год сохраняются), последняя
колонка — LTM (не 4-значный год) — отбрасывается, берём только годовые.

Маппинг ключей smart-lab → наши метрики и нормализация в базовые единицы:
- money  → значение × масштаб (млрд/млн/трлн), unit RUB/USD/EUR/CNY;
- share  → значение как есть (на акцию), unit RUB;
- ratio  → коэффициент (P/E, P/B), unit "ratio";
- pct    → процентная метрика (ROE, маржа, див.доходность), unit "pct" (число процентов).
Производные маржи (net/ebitda) считаются из годовых значений.

ХРУПКО: смена вёрстки/ToS smart-lab ломает парсер — отсюда мониторинг и ручной фолбэк
(`geo fundamentals add` из PDF, см. analytics.fundamentals). Сеть — только в `fetch_financials`;
`parse_financials` — чистая (тестируется без сети).
"""

from __future__ import annotations

import re

import httpx
from selectolax.parser import HTMLParser
from tenacity import retry, stop_after_attempt, wait_exponential

from geoanalytics.core.logging import get_logger
from geoanalytics.nlp.fundamentals import FundamentalFact
from geoanalytics.nlp.numeric import _MULT, _to_float

log = get_logger("connector.smartlab")

FINANCIALS_URL = "https://smart-lab.ru/q/{ticker}/f/y/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
_TIMEOUT = 20.0

# Ключ строки smart-lab (атрибут field) → (наша метрика, вид нормализации).
# money: денежная (масштаб млрд/млн); share: на акцию (RUB); ratio: коэффициент; pct: проценты.
_FIELD_MAP: dict[str, tuple[str, str]] = {
    "revenue": ("revenue", "money"),
    "net_income": ("net_profit", "money"),
    "ebitda": ("ebitda", "money"),
    "operating_income": ("operating_income", "money"),
    "ocf": ("ocf", "money"),
    "fcf": ("fcf", "money"),
    "capex": ("capex", "money"),
    "assets": ("assets", "money"),
    "bank_assets": ("assets", "money"),         # банки
    "net_assets": ("equity", "money"),          # промышленники: чистые активы ≈ капитал
    "capital": ("equity", "money"),             # банки
    "debt": ("debt", "money"),
    "net_debt": ("net_debt", "money"),
    "market_cap": ("market_cap", "money"),
    "ev": ("ev", "money"),
    "eps": ("eps", "share"),
    "dividend": ("dividend", "share"),
    "p_e": ("pe", "ratio"),
    "p_b": ("pb", "ratio"),
    "roe": ("roe", "pct"),
    "roa": ("roa", "pct"),
    "div_yield": ("div_yield", "pct"),
    "div_payout_ratio": ("payout", "pct"),
    "free_float": ("free_float", "pct"),
}

_CUR_WORDS = (("руб", "RUB"), ("долл", "USD"), ("$", "USD"), ("евро", "EUR"), ("юан", "CNY"))
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def _is_year(text: str) -> bool:
    """Чистый 4-значный год (отсекает LTM/пустые колонки)."""
    return bool(_YEAR_RE.match(text.strip()))


def _scale(unit_text: str) -> float:
    """Масштаб денежной единицы из «млрд руб»/«млн руб»/… (по умолчанию 1.0)."""
    low = unit_text.lower()
    for word, mult in _MULT.items():
        if word in low:
            return mult
    return 1.0


def _currency(unit_text: str) -> str:
    low = unit_text.lower()
    for word, code in _CUR_WORDS:
        if word in low:
            return code
    return "RUB"


def _parse_value(raw: str, kind: str, unit_text: str) -> tuple[float, str] | None:
    """Сырое значение ячейки → (значение в базовой единице, unit). None — не число."""
    raw = raw.strip()
    if not raw or raw in {"-", "—"}:
        return None
    try:
        if kind == "pct":
            return _to_float(raw.replace("%", "")), "pct"
        if kind == "ratio":
            return _to_float(raw.replace("%", "")), "ratio"
        num = _to_float(raw.replace("%", ""))
        if kind == "share":
            return num, _currency(unit_text)
        # money
        return num * _scale(unit_text), _currency(unit_text)
    except ValueError:
        return None


def _value_cells(row) -> list:
    """Ячейки-значения строки (td без служебного chartrow), в порядке колонок."""
    return [c for c in row.css("td") if "chartrow" not in (c.attributes.get("class") or "")]


def _th_unit(row) -> str:
    """Единица метрики из ``<th><span>…</span></th>`` («млрд руб», «%», «руб/акцию»)."""
    th = row.css_first("th")
    span = th.css_first("span") if th else None
    return span.text(strip=True) if span else ""


def parse_financials(html: str) -> list[FundamentalFact]:
    """HTML годовой отчётности smart-lab → факты по нашим метрикам (включая маржи). Чистая."""
    tree = HTMLParser(html)
    tables = [t for t in tree.css("table") if len(t.css("tr")) > 5]
    if not tables:
        return []
    rows = tables[0].css("tr")

    # Год-строка: её value-ячейки содержат ≥2 четырёхзначных года. Индексы колонок-годов.
    year_cols: list[tuple[int, str]] = []
    for row in rows:
        labelled = [(i, c.text(strip=True)) for i, c in enumerate(_value_cells(row))]
        years = [(i, t) for i, t in labelled if _is_year(t)]
        if len(years) >= 2:
            year_cols = years
            break
    if not year_cols:
        return []

    # period → {metric: value} (для производных марж) + плоский список фактов.
    by_period: dict[str, dict[str, float]] = {}
    facts: list[FundamentalFact] = []
    for row in rows:
        field = row.attributes.get("field")
        if not field or field not in _FIELD_MAP:
            continue
        metric, kind = _FIELD_MAP[field]
        unit_text = _th_unit(row)
        cells = _value_cells(row)
        for idx, year in year_cols:
            if idx >= len(cells):
                continue
            parsed = _parse_value(cells[idx].text(strip=True), kind, unit_text)
            if parsed is None:
                continue
            value, unit = parsed
            facts.append(FundamentalFact(metric=metric, value=value, unit=unit,
                                         period=year, snippet="smart-lab.ru"))
            by_period.setdefault(year, {})[metric] = value

    facts.extend(_derived_margins(by_period))
    return facts


def _derived_margins(by_period: dict[str, dict[str, float]]) -> list[FundamentalFact]:
    """Производные маржи (%) из годовых значений: net = прибыль/выручка, ebitda = ebitda/выручка."""
    out: list[FundamentalFact] = []
    for year, m in by_period.items():
        rev = m.get("revenue")
        if not rev:
            continue
        if "net_profit" in m:
            out.append(FundamentalFact("net_margin", round(m["net_profit"] / rev * 100, 2),
                                       "pct", year, "smart-lab.ru (расч.)"))
        if "ebitda" in m:
            out.append(FundamentalFact("ebitda_margin", round(m["ebitda"] / rev * 100, 2),
                                       "pct", year, "smart-lab.ru (расч.)"))
    return out


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, max=10), reraise=True)
def fetch_financials(ticker: str) -> str:
    """HTML страницы годовой отчётности smart-lab по тикеру (с ретраями)."""
    url = FINANCIALS_URL.format(ticker=ticker.upper())
    resp = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    return resp.text
