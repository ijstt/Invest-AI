"""Фундаменталка эмитентов из PDF-отчётов (H5): PDF→текст → rule-based метрики → БД → карточка.

Источник истины разбора — `nlp.fundamentals` (чистый, точный). Здесь — обёртка ввода: достать
текст из PDF (pypdf) или .txt, привязать к активу, сохранить идемпотентно и отдать для карточки/
бота. Автокраулера отчётов нет (надёжной ленты эмитентских PDF в РФ нет) — ввод ручной/CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from geoanalytics.core.logging import get_logger
from geoanalytics.nlp.fundamentals import FundamentalFact, extract_fundamentals
from geoanalytics.storage.models import Asset
from geoanalytics.storage.repositories import (
    AssetFundamentalRepository,
    AssetRepository,
    CompanyRepository,
    RevenueSegmentRepository,
)

log = get_logger("analytics.fundamentals")

# Человекочитаемые подписи метрик для карточки/бота (в каноничном порядке вывода).
METRIC_LABELS = {
    "revenue": "Выручка", "net_profit": "Чистая прибыль", "ebitda": "EBITDA",
    "operating_income": "Операционная прибыль",
    "net_margin": "Чистая маржа", "ebitda_margin": "EBITDA-маржа",
    "roe": "ROE", "roa": "ROA",
    "fcf": "FCF", "ocf": "Опер. денежный поток", "capex": "CAPEX",
    "debt": "Долг", "net_debt": "Чистый долг",
    "assets": "Активы", "equity": "Капитал",
    "market_cap": "Капитализация", "ev": "EV",
    "pe": "P/E", "pb": "P/B",
    "eps": "Прибыль на акцию", "dividend": "Дивиденд на акцию",
    "div_yield": "Див. доходность", "payout": "Payout", "free_float": "Free-float",
}


@dataclass
class IngestResult:
    ticker: str
    found: bool = False
    stored: int = 0
    facts: list[FundamentalFact] = None      # type: ignore[assignment]
    note: str = ""


def pdf_to_text(path: str | Path) -> str:
    """Текст из PDF (pypdf). Бросает понятную ошибку при отсутствии библиотеки/файла."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"нет файла: {p}")
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:        # pragma: no cover — зависимость в pyproject
        raise RuntimeError("не установлен pypdf — pip install pypdf") from exc
    reader = PdfReader(str(p))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def read_source_text(path: str | Path) -> str:
    """Текст из источника: PDF (pypdf) или .txt (как есть). Прочие — пробуем как текст."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        return pdf_to_text(p)
    return p.read_text(encoding="utf-8", errors="ignore")


def ingest_fundamentals(session, ticker: str, text: str, *, period: str | None = None,
                        source: str = "pdf") -> IngestResult:
    """Извлечь фундаментальные метрики из текста и сохранить для актива (идемпотентно)."""
    asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
    if asset is None:
        return IngestResult(ticker=ticker.upper(), note=f"актив {ticker.upper()} не найден")
    facts = extract_fundamentals(text, period=period)
    repo = AssetFundamentalRepository(session)
    for f in facts:
        repo.upsert(asset.id, f.metric, f.value, f.unit, period=f.period, source=source,
                    snippet=f.snippet)
    log.info("fundamentals_ingest", ticker=asset.ticker, stored=len(facts),
             period=period, source=source)
    return IngestResult(ticker=asset.ticker, found=True, stored=len(facts), facts=facts)


def scrape_fundamentals(session, ticker: str) -> IngestResult:
    """Скрейп годовой отчётности МСФО с smart-lab.ru → метрики по годам (идемпотентно).

    Многопериодно (хранит все доступные годы). Сеть/парсинг хрупки — при сбое возвращает
    note без падения (как graceful-коннекторы)."""
    from geoanalytics.connectors.smartlab import fetch_financials, parse_financials

    asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
    if asset is None:
        return IngestResult(ticker=ticker.upper(), note=f"актив {ticker.upper()} не найден")
    try:
        html = fetch_financials(ticker)
    except Exception as exc:  # noqa: BLE001 — сеть/HTTP smart-lab не должны ронять процесс
        log.warning("fundamentals_scrape_failed", ticker=ticker.upper(), error=str(exc))
        return IngestResult(ticker=asset.ticker, note=f"smart-lab недоступен: {exc}")
    facts = parse_financials(html)
    repo = AssetFundamentalRepository(session)
    for f in facts:
        repo.upsert(asset.id, f.metric, f.value, f.unit, period=f.period,
                    source="smartlab", snippet=f.snippet)
    # L2: обновить снапшот-профиль компании из тех же скрейпленных фактов.
    update_company_profile(session, asset, facts)
    log.info("fundamentals_scrape", ticker=asset.ticker, stored=len(facts))
    return IngestResult(ticker=asset.ticker, found=bool(facts), stored=len(facts), facts=facts)


def _latest_fact(facts: list[FundamentalFact], metric: str) -> FundamentalFact | None:
    """Факт метрики за свежайший период (период — строка-год; None в конец)."""
    matched = [f for f in facts if f.metric == metric]
    if not matched:
        return None
    return max(matched, key=lambda f: (f.period or ""))


def update_company_profile(session, asset: Asset, facts: list[FundamentalFact]) -> bool:
    """L2: записать профиль эмитента (market_cap/free_float/shares) на Company из фактов.

    `shares` выводится как капитализация / последняя цена закрытия (если оба есть). Без
    привязанной компании — no-op. Возвращает True, если профиль обновлён."""
    if asset.company_id is None:
        return False
    mc = _latest_fact(facts, "market_cap")
    ff = _latest_fact(facts, "free_float")
    market_cap = mc.value if mc else None
    free_float = ff.value if ff else None
    shares = None
    if market_cap:
        price = AssetRepository(session).latest_price(asset.id)
        if price is not None and price.close:
            shares = market_cap / float(price.close)
    if market_cap is None and free_float is None and shares is None:
        return False
    CompanyRepository(session).update_profile(
        asset.company_id, market_cap=market_cap, free_float=free_float, shares=shares)
    return True


def composition_for_asset(session, asset: Asset) -> dict | None:
    """L2: состав и профиль эмитента для карточки — ``{profile, segments}``.

    `profile` — {description, market_cap, free_float, shares} с человекочитаемыми display'ами;
    `segments` — сегменты выручки за свежайший период. None — нет привязанной компании."""
    company = asset.company
    if company is None:
        return None
    profile = {
        "description": company.description,
        "market_cap": company.market_cap,
        "market_cap_display": (format_value("market_cap", company.market_cap, "RUB")
                               if company.market_cap else None),
        "free_float": company.free_float,
        "shares": company.shares,
    }
    segments = [
        {"segment": s.segment, "value": s.value,
         "display": format_value("revenue", s.value, "RUB"),
         "share": s.share, "period": s.period}
        for s in RevenueSegmentRepository(session).for_company(company.id)
    ]
    has_profile = any(profile[k] is not None
                      for k in ("description", "market_cap", "free_float", "shares"))
    if not has_profile and not segments:
        return None
    return {"profile": profile, "segments": segments}


def format_value(metric: str, value: float, unit: str) -> str:
    """Человекочитаемое значение: денежные — млрд/трлн ₽; коэффициент — как есть; на акцию — ₽."""
    if unit == "pct":
        return f"{value:.1f}%"
    if unit == "ratio":
        return f"{value:.2f}"
    cur = {"RUB": "₽", "USD": "$", "EUR": "€", "CNY": "¥"}.get(unit, unit)
    if metric in ("eps", "dividend"):
        return f"{value:,.2f} {cur}"
    av = abs(value)
    if av >= 1e12:
        return f"{value / 1e12:.2f} трлн {cur}"
    if av >= 1e9:
        return f"{value / 1e9:.1f} млрд {cur}"
    if av >= 1e6:
        return f"{value / 1e6:.1f} млн {cur}"
    return f"{value:,.0f} {cur}"


def fundamentals_for_asset(session, asset_id: int) -> list[dict]:
    """Свежие метрики актива для карточки/бота: ``[{metric, label, value, display, unit, period}]``
    в каноничном порядке (выручка → прибыль → … → P/E)."""
    rows = AssetFundamentalRepository(session).latest_for_asset(asset_id)
    by_metric = {r.metric: r for r in rows}
    out: list[dict] = []
    for metric in METRIC_LABELS:
        r = by_metric.get(metric)
        if r is None:
            continue
        out.append({
            "metric": metric, "label": METRIC_LABELS[metric], "value": r.value,
            "display": format_value(metric, r.value, r.unit), "unit": r.unit,
            "period": r.period,
        })
    return out
