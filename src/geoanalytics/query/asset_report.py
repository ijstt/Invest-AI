"""Структурный аналитический отчёт по активу (M2).

Собирает контекст актива (индикаторы + макро + новостной фон + факторы), берёт
связанные новости и формирует отчёт. Связный анализ — через контекст актива
(LLM при доступности, иначе шаблон). Это и есть «RAG»: ретрив структурированного
контекста + новостей и синтез ответа.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from geoanalytics.analytics.correlations import correlate_asset
from geoanalytics.analytics.factor_model import factor_scores_for_asset
from geoanalytics.analytics.forecasts import forecasts_for_asset
from geoanalytics.analytics.fundamentals import composition_for_asset, fundamentals_for_asset
from geoanalytics.analytics.graph_impact import graph_impacts_for_asset
from geoanalytics.analytics.macro import macro_snapshot
from geoanalytics.analytics.pressure import news_pressure
from geoanalytics.analytics.prices import apply_live_last, asset_indicators
from geoanalytics.analytics.recommendation import stance_for_asset
from geoanalytics.analytics.sentiment_trend import latest_momentum
from geoanalytics.context.asset_context import build_context, latest_context
from geoanalytics.context.events import top_impacts_for_asset
from geoanalytics.context.graph import factors_for_asset
from geoanalytics.core.locks import LLMBusy, llm_generation_lock
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Article, ArticleEntity, ArticleNumber, Asset
from geoanalytics.storage.repositories import ArticleRepository


@dataclass
class AssetReport:
    ticker: str
    found: bool = False
    name: str | None = None
    sector: str | None = None
    # Живой интрадей-LAST из среза MOEX (как у портфеля/топ-движений); None — нет свежего среза.
    last_price: float | None = None
    indicators: dict = field(default_factory=dict)
    macro: dict = field(default_factory=dict)
    factors: dict = field(default_factory=dict)
    correlations: dict = field(default_factory=dict)      # фактор → корреляция
    events: list[dict] = field(default_factory=list)      # топ-события влияния
    # G7: косвенные влияния через граф (события соседей-эмитентов, аттенюированные).
    graph_impacts: list[dict] = field(default_factory=list)
    # {title, url, sentiment, event_type, significance, published_at}
    news: list[dict] = field(default_factory=list)
    # F5: последний дивиденд из новостей — {value, published_at, yield_pct|None}.
    dividend: dict | None = None
    # G5: индекс новостного давления за 7 дней (Σ sig / 7).
    news_pressure_7d: float | None = None
    # G6: EWMA-14 суточного сентимента (тональный моментум).
    sentiment_ema_14d: float | None = None
    # H5: фундаментальные метрики из отчётов — [{metric, label, value, display, unit, period}].
    fundamentals: list[dict] = field(default_factory=list)
    # L2: состав/профиль эмитента — {profile: {market_cap, free_float, shares, …}, segments: […]}.
    composition: dict | None = None
    # L3: кросс-секционные факторные экспозиции — [{factor, label, zscore, percentile, day}].
    factor_ranks: list[dict] = field(default_factory=list)
    # B3/F10: прогнозы брокеров — [{label, value, unit, target_date, implied_pct, surprise_pct, …}].
    forecasts: list[dict] = field(default_factory=list)
    # C1: рекомендательная стойка — {signal, label, score, conviction, drivers[], risk}.
    stance: dict | None = None
    narrative: str | None = None
    note: str = ""


def build_report(ticker: str, rebuild: bool = True, use_llm: bool = True,
                 period: str = "D") -> AssetReport:
    """Формирует отчёт по активу. rebuild=True пересобирает контекст перед выводом.

    `period` (A7) — таймфрейм индикаторов: "D"/"W"/"M" (дни/недели/месяцы).
    """
    report = AssetReport(ticker=ticker.upper())

    # Пересборка контекста (сохраняет новую версию в asset_context). LLM-разбор — под МЕЖПРОЦЕССНЫМ
    # замком генерации (бот↔дашборд, [[core.locks]]): занят → собираем карточку без ИИ-разбора.
    if rebuild:
        if use_llm:
            try:
                with llm_generation_lock():
                    build_context(ticker, use_llm=True)
            except LLMBusy:
                build_context(ticker, use_llm=False)
                report.note = ("⏳ Система занята генерацией — карточка без ИИ-разбора, "
                               "повторите позже.")
        else:
            build_context(ticker, use_llm=False)

    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            report.note = "Актив не найден. Сначала: geo db seed (и при желании geo backfill)."
            return report

        report.found = True
        report.name = asset.name

        ind = asset_indicators(session, asset.id, period=period)
        report.indicators = ind.as_dict()
        # A1: показываемая цена = живой LAST (как портфель/топ-движения), не закрытие свечи.
        report.last_price = apply_live_last(session, report.ticker, report.indicators, period)
        report.macro = macro_snapshot(session).as_dict()

        factors = factors_for_asset(session, asset)
        report.sector = factors.sector
        report.factors = factors.as_dict()
        report.correlations = correlate_asset(session, asset)
        report.events = top_impacts_for_asset(session, asset.id)
        report.graph_impacts = [
            {"via": g.via_ticker, "relation": g.relation, "title": g.title,
             "direction": g.direction, "magnitude": g.magnitude, "url": g.url}
            for g in graph_impacts_for_asset(session, asset.id)
        ]

        for art in ArticleRepository(session).for_asset(asset.id):
            report.news.append({"title": art.title, "url": art.url,
                                "sentiment": art.sentiment, "event_type": art.event_type,
                                "significance": art.significance,
                                "published_at": art.published_at})

        report.dividend = _latest_dividend(
            session, asset.id, report.indicators.get("last")
        )

        report.news_pressure_7d = news_pressure(session, asset.id, window=7)
        report.sentiment_ema_14d = latest_momentum(session, asset.id, span=14)
        report.fundamentals = fundamentals_for_asset(session, asset.id)   # H5
        report.composition = composition_for_asset(session, asset)        # L2
        report.factor_ranks = factor_scores_for_asset(session, asset.id)   # L3
        report.forecasts = forecasts_for_asset(                          # B3/F10
            session, asset.id, last_price=report.indicators.get("last"))
        report.stance = stance_for_asset(                                # C1 + L4 (фундаментал)
            session, asset.id, report.ticker, horizon="long",            # долгосрочная линза
            indicators=report.indicators, forecasts=report.forecasts).as_dict()

        ctx = latest_context(session, asset.id)
        report.narrative = ctx.narrative if ctx else None

    if not report.indicators and not report.news:
        report.note = ("Данных по активу пока мало. Загрузите историю котировок "
                       f"(geo backfill -t {report.ticker}) и новости (geo pipeline).")
    return report


def _latest_dividend(session, asset_id: int, last_price) -> dict | None:
    """Последний дивиденд на акцию из новостей актива (F5) + доходность к цене.

    Берём только салиентные связи (salient IS NOT FALSE) — фоновое упоминание
    чужого дивиденда не должно приписываться активу. «Дивиденд 25₽ при цене
    250₽ = 10% доходности» — вычислимо без LLM.
    """
    row = session.execute(
        select(ArticleNumber.value, Article.published_at)
        .join(Article, Article.id == ArticleNumber.article_id)
        .join(ArticleEntity, ArticleEntity.article_id == Article.id)
        .where(
            ArticleEntity.entity_type == "asset",
            ArticleEntity.entity_id == asset_id,
            ArticleEntity.salient.isnot(False),
            ArticleNumber.kind == "dividend",
        )
        .order_by(Article.published_at.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    value, published_at = row
    yield_pct = round(value / float(last_price) * 100, 2) if last_price else None
    return {"value": value, "published_at": published_at, "yield_pct": yield_pct}
