"""Веб-дашборд на HTMX + Jinja (M5.2).

Server-rendered страницы поверх той же логики, что у CLI/JSON-API: дашборд рынка,
страница актива (с SVG-графиком цены) и экран бэктеста (с кривой капитала).
HTMX подменяет фрагменты результата без перезагрузки; формы — обычный GET, поэтому
страницы работают и без JavaScript (прогрессивное улучшение).
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from geoanalytics.alerts import manage
from geoanalytics.analytics.backtest import PRICE_STRATEGIES, backtest_asset_cached
from geoanalytics.analytics.indicators import bollinger, sma
from geoanalytics.analytics.prices import apply_live_last, asset_indicators, ohlcv_series
from geoanalytics.analytics.resample import resample_ohlcv
from geoanalytics.api.charts import (
    candles,
    date_labels,
    equity_chart,
    pie,
    rsi_panel,
    sentiment_strip,
    sparkline,
    treemap,
    volume_bars,
)
from geoanalytics.core.types import EntityType
from geoanalytics.query.alerts_feed import get_alert, recent_alerts
from geoanalytics.query.ask import answer as ask_answer
from geoanalytics.query.asset_report import build_report
from geoanalytics.query.assets_feed import list_assets
from geoanalytics.query.news_summary import build_snapshot, recent_headlines
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import (
    Article,
    ArticleEntity,
    Asset,
    Event,
    EventImpact,
)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()

_STRATEGIES = [*PRICE_STRATEGIES, "sentiment"]
_ALERT_TYPES = ["price_move", "neg_spike", "new_event", "technical", "combo",
                "calendar", "portfolio"]
_SEVERITIES = ["info", "warning", "critical"]
# Диапазоны графика (зум по дневным данным): метка → глубина в днях (None = вся история).
_CHART_RANGES: dict[str, int | None] = {"1m": 31, "3m": 93, "6m": 186, "1y": 372, "max": None}

# Лёгкий TTL-кэш тяжёлых раннеров дашборда (портфельный отчёт = HMM-режим + N OLS-атрибуций
# на запрос): повторные перезагрузки/автообновление не пересчитывают всё заново. Процесс
# один, пользователь один — простого dict достаточно.
_CACHE_TTL_SEC = 60.0
_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, fn, ttl: float = _CACHE_TTL_SEC):
    """Мемоизация с TTL: возвращает свежий кэш или пересчитывает через `fn`."""
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (now, value)
    return value


def _invalidate_cache(key: str) -> None:
    """Сброс кэша (после мутаций — напр., правки портфеля)."""
    _cache.pop(key, None)


def _asset_ohlcv(ticker: str, days: int | None) -> list:
    """OHLCV-свечи по тикеру за последние `days` дней (None = вся история)."""
    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            return []
        since = datetime.now(UTC) - timedelta(days=days) if days else None
        return ohlcv_series(session, asset.id, since=since)


def _sentiment_cells(ticker: str, days: int = 90) -> list[dict]:
    """Дневная тональность по активу за `days` дней: [{label, score}] (среднее за день, C5)."""
    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            return []
        since = datetime.now(UTC) - timedelta(days=days)
        rows = session.execute(
            select(Article.published_at, Article.sentiment_score)
            .join(ArticleEntity, ArticleEntity.article_id == Article.id)
            .where(ArticleEntity.entity_type == EntityType.ASSET.value,
                   ArticleEntity.entity_id == asset.id,
                   Article.published_at >= since,
                   Article.sentiment_score.is_not(None))
        ).all()
    by_day: dict = defaultdict(list)
    for pub, score in rows:
        by_day[pub.date()].append(float(score))
    return [{"label": d.strftime("%d.%m"), "score": sum(v) / len(v)}
            for d, v in sorted(by_day.items())]


def _price_overlays(closes: list[float]) -> list[dict]:
    """Наложения на цену: линии SMA20/50/200 и полосы Bollinger (C1).

    Каждый ряд выровнен по барам — значение на баре i считается по префиксу `closes[:i+1]`;
    в прогреве индикатора (мало данных) → None, такие точки график пропускает. Цвета/штрих
    заданы тут (charts.py остаётся чисто-геометрическим). Ряды целиком из None (короткий
    диапазон) график отбросит сам.
    """
    if len(closes) < 20:
        return []

    def sma_line(window: int) -> list[float | None]:
        return [sma(closes[:i + 1], window) for i in range(len(closes))]

    def boll_line(idx: int) -> list[float | None]:  # 0 — нижняя, 2 — верхняя
        return [(bollinger(closes[:i + 1]) or (None, None, None))[idx]
                for i in range(len(closes))]

    return [
        {"name": "SMA20", "values": sma_line(20), "css": "#e3a008", "dash": ""},
        {"name": "SMA50", "values": sma_line(50), "css": "#4aa8ff", "dash": ""},
        {"name": "SMA200", "values": sma_line(200), "css": "#a371f7", "dash": ""},
        {"name": "Bollinger ↑", "values": boll_line(2), "css": "var(--muted)", "dash": "3,3"},
        {"name": "Bollinger ↓", "values": boll_line(0), "css": "var(--muted)", "dash": "3,3"},
    ]


def _chart_event_markers(ticker: str, bar_dates: list, limit: int = 12) -> list[dict]:
    """Маркеры событий влияния для графика актива (#5): событие → ближайший бар.

    Берёт значимые EventImpact актива в окне графика и мапит дату события (occurred_at)
    на индекс ближайшего бара (≤ даты). Возвращает вход для `charts._event_markers`.
    """
    import bisect

    if not bar_dates:
        return []
    bar_days = [d.date() if hasattr(d, "date") else d for d in bar_dates]
    n = len(bar_days)
    with session_scope() as session:
        asset = session.scalars(
            select(Asset).where(Asset.ticker == ticker.upper())
        ).first()
        if asset is None:
            return []
        rows = session.execute(
            select(Event.occurred_at, Event.title, Event.event_type,
                   EventImpact.direction, EventImpact.magnitude)
            .join(EventImpact, EventImpact.event_id == Event.id)
            .where(EventImpact.asset_id == asset.id, Event.occurred_at >= bar_dates[0])
            .order_by(EventImpact.magnitude.desc())
            .limit(limit)
        ).all()
    markers = []
    for occurred, title, etype, direction, magnitude in rows:
        if occurred is None:
            continue
        ed = occurred.date() if hasattr(occurred, "date") else occurred
        idx = min(max(bisect.bisect_right(bar_days, ed) - 1, 0), n - 1)
        markers.append({"idx": idx, "direction": direction, "magnitude": magnitude,
                        "title": title, "type": etype})
    return markers


def _chart_context(ticker: str, rng: str = "6m", period: str = "D", kind: str = "line",
                   ovl: bool = True, vol: bool = True, osc: bool = True) -> dict:
    """Данные графика по выбранным диапазону/периоду/типу (для HTMX-партиала).

    Помимо основного графика готовит сабпанели: объём (C2) и осциллятор RSI (C3). Флаги
    `ovl`/`vol`/`osc` включают/выключают оверлеи SMA-Bollinger и сабпанели — их можно
    отключить тумблерами на дашборде (расчёт пропускается, если выключено).
    """
    rows = _asset_ohlcv(ticker, _CHART_RANGES.get(rng))
    if period in ("W", "M"):
        rows = resample_ohlcv(rows, period)
    ohlc = [(r[0], r[1], r[2], r[3], r[4]) for r in rows]   # без объёма — для candles/sparkline
    closes = [r[4] for r in rows]
    volumes = [r[5] for r in rows]
    ups = [r[4] >= r[1] for r in rows]                      # close ≥ open
    bar_dates = [r[0] for r in rows]
    labels = date_labels(bar_dates)
    overlays = _price_overlays(closes) if ovl else []
    markers = _chart_event_markers(ticker, bar_dates)       # #5: события на графике
    if kind == "candles":
        chart = candles(ohlc, height=260, labels=labels, overlays=overlays, markers=markers)
    else:
        chart = sparkline(closes, height=240, labels=labels, overlays=overlays,
                          markers=markers, dates=bar_dates)
    return {"ticker": ticker.upper(), "chart": chart, "chart_kind": kind,
            "chart_range": rng, "chart_period": period, "ranges": list(_CHART_RANGES),
            "volpanel": volume_bars(volumes, ups) if vol else None,
            "oscpanel": rsi_panel(closes) if osc else None,
            "chart_ovl": int(ovl), "chart_vol": int(vol), "chart_osc": int(osc)}


def _indicators_context(ticker: str, period: str = "D") -> dict:
    """Индикаторы актива на выбранном таймфрейме D/W/M (A7) — для панели и HTMX-тумблера.

    Считает ТОЛЬКО индикаторы (без пересборки отчёта/LLM), чтобы переключение Д/Н/М
    было дешёвым. На W/M история сжимается в `asset_indicators`.
    """
    period = period if period in ("D", "W", "M") else "D"
    with session_scope() as session:
        asset = session.scalars(
            select(Asset).where(Asset.ticker == ticker.upper())
        ).first()
        ind = asset_indicators(session, asset.id, period=period).as_dict() if asset else {}
        # A1: на дневном таймфрейме показываем живой LAST (единая цена с портфелем/дашбордом).
        if asset:
            apply_live_last(session, ticker, ind, period)
    return {"ticker": ticker.upper(), "indicators": ind, "ind_period": period}


def _asset_context(ticker: str) -> dict:
    report = build_report(ticker, rebuild=False, use_llm=False)
    # Начальный график (линия, 6м) с оверлеями и сабпанелями объёма/RSI; свечи и другие
    # таймфреймы переключает HTMX-партиал через тот же `_chart_context`.
    ctx = _chart_context(ticker) if report.found else {
        "chart": None, "chart_kind": "line", "chart_range": "6m", "chart_period": "D",
        "ranges": list(_CHART_RANGES), "volpanel": None, "oscpanel": None,
        "chart_ovl": 1, "chart_vol": 1, "chart_osc": 1}
    sentiment = sentiment_strip(_sentiment_cells(ticker)) if report.found else None
    factor_trend = _factor_trend(ticker) if report.found else None
    # Индикаторы дублируем в ctx с таймфреймом по умолчанию (D) — панель умеет Д/Н/М.
    return {"report": report, "ticker": ticker.upper(), "sentiment": sentiment,
            "factor_trend": factor_trend,
            "indicators": report.indicators, "ind_period": "D", **ctx}


def _factor_trend(ticker: str):
    """L5: спарклайн композитного факторного z-скора во времени (None, если <2 дней истории)."""
    from datetime import datetime

    from geoanalytics.storage.repositories import FactorScoreRepository
    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            return None
        rows = FactorScoreRepository(session).series_for_asset(asset.id, "composite", days=120)
    if len(rows) < 2:
        return None
    vals = [r.zscore for r in rows]
    dates = [datetime(r.day.year, r.day.month, r.day.day) for r in rows]
    return sparkline(vals, width=320, height=80, dates=dates)


def _backtest_context(ticker: str, strategy: str) -> dict:
    base = {"ticker": ticker.upper(), "strategy": strategy}
    try:
        result = backtest_asset_cached(ticker, strategy=strategy)
    except ValueError as exc:
        return {**base, "error": str(exc)}
    if result is None:
        return {**base, "error": f"Актив {ticker.upper()} не найден"}
    return {**base, "result": result,
            "equity": equity_chart(result.equity_curve, result.trades)}


def _portfolio_context() -> dict:
    """Отчёт по портфелю (J1) для веб-страницы — зеркало `geo portfolio`.

    Корреляции/экспозицию заранее раскладываем в списки: ключи-кортежи неудобны в Jinja.
    """
    report = _cached("portfolio_report", _compute_portfolio_report)
    # Среднесрочная сводка-стойка (недельный ТФ) — отдельный TTL (тяжелее: ресемпл+стойки).
    stance = _cached("portfolio_stance", lambda: _compute_portfolio_stance(report), ttl=300.0)
    correlations = [{"pair": f"{a} / {b}", "r": r}
                    for (a, b), r in sorted(report.correlations.items())]
    exposure = sorted(report.exposure.items())

    # Стоимость во времени — спарклайн с подписями дат; аллокация — кольцо по секторам.
    value_chart = None
    if report.value_series:
        dates = [datetime(d.year, d.month, d.day) for d, _ in report.value_series]
        value_chart = sparkline([v for _, v in report.value_series], width=820, height=200,
                                 labels=date_labels(dates, width=820), dates=dates)
    # P&L во времени (value − база покупки) — есть только при истории по снимкам с известной базой.
    pnl_chart = None
    if len(report.pnl_series) >= 2:
        pdates = [datetime(d.year, d.month, d.day) for d, _ in report.pnl_series]
        pnl_chart = sparkline([v for _, v in report.pnl_series], width=820, height=160,
                              labels=date_labels(pdates, width=820), dates=pdates)
    alloc_pie = pie(report.sector_alloc)
    # Treemap аллокации по ПОЗИЦИЯМ (площадь ∝ вес) — нагляднее кольца на многих холдингах.
    # Канва вытянута вниз под высоту соседних панелей (пай+легенда / риск-бары), чтобы карта
    # занимала всю выделенную область (читаемость крупных плиток).
    alloc_treemap = treemap([(p.ticker, p.weight_pct) for p in report.positions
                             if p.weight_pct], width=720, height=620)
    # Вклад в риск — позиции с оценённым вкладом, по убыванию (для бар-чарта).
    risk_rows = sorted((p for p in report.positions if p.risk_contribution_pct is not None),
                       key=lambda p: p.risk_contribution_pct, reverse=True)
    risk_max = max((p.risk_contribution_pct for p in risk_rows), default=0.0)
    return {"report": report, "stance": stance, "correlations": correlations,
            "exposure": exposure,
            "value_chart": value_chart, "pnl_chart": pnl_chart, "alloc_pie": alloc_pie,
            "alloc_treemap": alloc_treemap,
            "risk_rows": risk_rows, "risk_max": risk_max,
            "assets": list_assets()}


def _compute_portfolio_stance(report):
    """Среднесрочная сводка-стойка по портфелю (недельный ТФ) из кэшированного отчёта."""
    from geoanalytics.analytics.recommendation import portfolio_stance

    with session_scope() as session:
        return portfolio_stance(session, report, period="W")


def _add_position(ticker: str, quantity: float, price: float | None) -> None:
    """Добавить/нарастить позицию (зеркало `geo portfolio add`). Ошибки глушит вызывающий."""
    from geoanalytics.storage.repositories import PortfolioRepository

    with session_scope() as session:
        PortfolioRepository(session).upsert_position(ticker, quantity, price)


def _remove_position(ticker: str) -> None:
    """Удалить позицию целиком (зеркало `geo portfolio remove`)."""
    from geoanalytics.storage.repositories import PortfolioRepository

    with session_scope() as session:
        PortfolioRepository(session).remove_position(ticker)


def _compute_portfolio_report():
    """Тяжёлый раннер портфеля (через TTL-кэш в `_portfolio_context`).

    Оценку ведём по живой интрадей-цене (как дашборд), чтобы «Цена» в портфеле не отставала
    от топ-движений: подмешиваем последний LAST из среза MOEX (`latest_live_prices`).
    """
    from geoanalytics.analytics.portfolio import live_portfolio_report
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        return live_portfolio_report(session)


# Факторы влияния (C): корреляция доходности с сырьём/валютами (correlate_asset).
_FACTOR_CSS = {"brent": "gn-commodity", "gold": "gn-commodity", "silver": "gn-commodity",
               "platinum": "gn-commodity", "palladium": "gn-commodity",
               "usd_rub": "gn-fx", "usd_eur": "gn-fx"}
_FACTOR_RU = {"brent": "Brent", "gold": "золото", "silver": "серебро",
              "platinum": "платина", "palladium": "палладий",
              "usd_rub": "USD/RUB", "usd_eur": "USD/EUR"}
_EVENT_AGG = {"positive": ("up", "Позитивные события", "↑"),
              "negative": ("down", "Негативные события", "↓"),
              "neutral": ("muted", "Нейтральные события", "•")}


def _graph_context(ticker: str) -> dict:
    """Граф влияния актива: радиальное ДЕРЕВО «актив → агрегаты → листья».

    Группировка (A) вместо плоской звезды: ветвь СЕКТОР (gn-sector) тянет к себе пиров
    (gn-peer, кликабельны → свой граф); события агрегированы ПО НАПРАВЛЕНИЮ — ветви
    «↑ позитив» / «↓ негатив» / «• нейтрально», под каждой отдельные события (размер+подпись =
    magnitude, заголовок в подсказке). Факторы влияния не только тикеры (C): сырьё/валюты
    как ветви-листья с весом = |корреляция| доходности (gn-commodity/gn-fx), плюс макро-
    драйверы сектора (gn-macro, без числа). События очищены top_impacts (живые связи).
    """
    from geoanalytics.analytics.correlations import correlate_asset
    from geoanalytics.analytics.graph_weight import (
        MACRO_WEIGHT,
        aggregate_weight,
        asset_node_weight,
    )
    from geoanalytics.api.charts import radial_tree, relax_overlaps
    from geoanalytics.context.events import top_impacts_for_asset
    from geoanalytics.context.graph import factors_for_asset, sector_macro_factors

    t = ticker.upper().strip()
    graph = None
    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == t)).first() if t else None
        if asset is not None:
            branches: list[dict] = []
            factors = factors_for_asset(session, asset)
            # Сектор → пиры (внешними листьями ветви). A2: размер пира = его собственный вес
            # (давление+сентимент+TA), а не константа; сектор = агрегат весов пиров.
            peer_tickers = factors.peers[:8]
            if factors.sector and peer_tickers:
                peers = list(session.scalars(
                    select(Asset).where(Asset.ticker.in_(peer_tickers))))
                children = [{"label": a.ticker, "css": "gn-peer",
                             "size": asset_node_weight(session, a.id, with_ta=True),
                             "title": f"Пир по сектору: {a.name or a.ticker}",
                             "url": f"/ui/graph?ticker={a.ticker}"} for a in peers]
                branches.append({"label": factors.sector, "css": "gn-sector",
                                 "size": aggregate_weight([c["size"] for c in children]),
                                 "title": f"Сектор: {factors.sector}", "children": children})
            elif factors.sector:
                branches.append({"label": factors.sector, "css": "gn-sector", "size": 0.55,
                                 "title": f"Сектор: {factors.sector}", "url": None})
            # События → агрегаты по направлению (A: один зелёный/красный узел)
            groups: dict[str, list[dict]] = {"positive": [], "negative": [], "neutral": []}
            for e in top_impacts_for_asset(session, asset.id, hours=720, limit=20):
                groups.setdefault(e["direction"], groups["neutral"]).append(e)
            # Размер агрегата = СУММАРНАЯ сила группы (Σ|magnitude|), нормированная на самую
            # «тяжёлую» группу: доминирующее направление — самый крупный узел (передаёт, НАСКОЛЬКО
            # сильно влияют события, а не их среднее). Подпись несёт точное число: «↓14 · Σ7.3».
            totals = {d: sum(float(e["magnitude"] or 0.0) for e in evs)
                      for d, evs in groups.items()}
            max_total = max(totals.values()) or 1.0
            for dirn, evs in groups.items():
                if not evs:
                    continue
                css, word, arrow = _EVENT_AGG[dirn]
                children = [{
                    "label": f"{e['magnitude']:.2f}",
                    "title": (f"[{e['type']}] " if e.get("type") else "") + (e["title"] or ""),
                    "css": css, "size": min(max(float(e["magnitude"] or 0.0), 0.0), 1.0),
                    "url": e.get("url")} for e in evs]
                total = totals[dirn]
                branches.append({"label": f"{arrow}{len(evs)} · Σ{total:.1f}", "css": css,
                                 "size": min(total / max_total, 1.0),
                                 "title": f"{word}: {len(evs)} соб., Σсила {total:.2f}",
                                 "children": children})
            # Факторы (C) и макро — отдельными АГРЕГАТАМИ (как события ↑/↓), а не одиночными
            # ветвями у центра: их листья уходят на внешнее кольцо с подписями НАРУЖУ, иначе
            # 9 факторов/макро лепятся в один сектор у центра и подписи наслаиваются.
            cors = correlate_asset(session, asset)
            ranked = sorted(((k, v) for k, v in cors.items() if k in _FACTOR_CSS),
                            key=lambda kv: abs(kv[1]), reverse=True)[:6]
            if ranked:
                fchildren = [{"label": f"{_FACTOR_RU[k]} {r:+.2f}", "css": _FACTOR_CSS[k],
                              "size": min(abs(r), 1.0), "url": None,
                              "title": f"Корреляция доходности с {_FACTOR_RU[k]}: {r:+.2f}"}
                             for k, r in ranked]
                branches.append({"label": "Факторы", "css": "gn-commodity",
                                 "size": min(abs(ranked[0][1]), 1.0),
                                 "title": "Сырьё и валюты (вес = |корреляция| доходности)",
                                 "children": fchildren})
            macro = sector_macro_factors(factors.sector)[:3]
            if macro:
                mchildren = [{"label": mf, "css": "gn-macro", "size": MACRO_WEIGHT, "url": None,
                              "title": f"Макро-драйвер сектора: {mf}"} for mf in macro]
                branches.append({"label": "Макро", "css": "gn-macro",
                                 "size": min(MACRO_WEIGHT + 0.1, 1.0),
                                 "title": "Макро-драйверы сектора", "children": mchildren})
            graph = relax_overlaps(radial_tree(t, branches))
    return {"ticker": t, "graph": graph, "assets": list_assets()}


def _market_graph_context() -> dict:
    """Большой граф рынка (B): дерево ИНДЕКС → секторы → топ-активы → важные события.

    4 уровня: корень IMOEX → сектора («сферы») → по топ-активов сектора (ранг = новостное
    давление news_pressure за 7д) → по топ-событий актива (top_impacts, размер=magnitude).
    Узлы крупнее у тех, у кого выше важность. Раскладка `radial_layout` (произвольная глубина,
    тот же контракт, что граф тикера → шаблон `_graph_svg.html` общий). Тяжеловато (десятки
    активов), потому отдельная страница с автообновлением раз в 60с.
    """
    from geoanalytics.analytics.graph_weight import (
        asset_node_weight,
        normalize_weight,
        recent_turnover,
    )
    from geoanalytics.analytics.pressure import news_pressure
    from geoanalytics.api.charts import radial_layout, relax_overlaps
    from geoanalytics.context.events import top_impacts_for_asset
    from geoanalytics.context.graph import assets_in_sector
    from geoanalytics.storage.models import Sector

    _DIR_CSS = {"positive": "up", "negative": "down", "neutral": "muted"}
    graph = None
    with session_scope() as session:
        sectors = session.scalars(select(Sector).order_by(Sector.name)).all()
        sector_assets = {sec: assets_in_sector(session, sec.id) for sec in sectors}
        # Вес сектора на индексе = Σ оборота его активов (близко к рыночной доле). Считаем
        # обороты по всем активам разом, затем нормируем сектора к самому «тяжёлому».
        turnover = recent_turnover(
            session, [a.id for assets in sector_assets.values() for a in assets])
        sec_turn = {sec: sum(turnover.get(a.id, 0.0) for a in assets)
                    for sec, assets in sector_assets.items()}
        peak_turn = max(sec_turn.values(), default=0.0)
        sector_branches: list[dict] = []
        for sec in sectors:
            ranked = sorted(
                ((a, news_pressure(session, a.id, window=7)) for a in sector_assets[sec]),
                key=lambda av: av[1], reverse=True)
            asset_nodes: list[dict] = []
            for a, pressure in ranked[:6]:        # топ-6 активов сектора по давлению
                ev_nodes = [{
                    "label": f"{e['magnitude']:.2f}",
                    "title": (f"[{e['type']}] " if e.get("type") else "") + (e["title"] or ""),
                    "css": _DIR_CSS.get(e["direction"], "muted"),
                    "size": min(max(float(e["magnitude"] or 0.0), 0.0), 1.0),
                    "url": e.get("url"),
                    # Прорежаем кольцо событий: только значимые (magnitude ≥ 0.15), до 3 на актив,
                    # иначе внешнее кольцо переполнено и точки налезают.
                } for e in top_impacts_for_asset(session, a.id, hours=720, limit=3)
                    if float(e["magnitude"] or 0.0) >= 0.15]
                # A2: размер актива = давление+сентимент (TA пропускаем — десятки активов на
                # автообновлении дорого); давление переиспользуем из ранжирования.
                w = asset_node_weight(session, a.id, pressure=pressure)
                asset_nodes.append({
                    "label": a.ticker, "css": "gn-peer",
                    "size": w, "url": f"/ui/graph?ticker={a.ticker}",
                    "title": f"{a.name} · давление {pressure:.2f}", "children": ev_nodes})
            if asset_nodes:
                sector_branches.append({
                    "label": sec.name, "css": "gn-sector",
                    # Размер сектора = его вес на индексе (оборот), а не агрегат новостей.
                    "size": normalize_weight(sec_turn[sec], peak_turn),
                    "title": f"Сектор: {sec.name} · вес на индексе по обороту",
                    "children": asset_nodes})
        if sector_branches:
            root = {"label": "IMOEX", "children": sector_branches}
            # Крупнее холст → больше окружность внешнего кольца при тех же радиусах узлов →
            # меньше слипания (плюс анизотропный relax добивает остаток).
            graph = relax_overlaps(radial_layout(root, width=1600, height=1600, pad=190))
    return {"graph": graph, "is_market": True}


def _market_heatmap_context() -> dict:
    """Карта рынка под графом индекса (Finviz-стиль): секторы→активы, площадь ∝ оборот, цвет ∝ Δ%.

    Размер плитки — последний дневной оборот (close·volume, «текущий» объём торгов), цвет —
    дневное изменение цены (зелёный рост / красный падение). Группировка по секторам с подписью.
    Тяжеловато (обороты+изменения по всем активам), поэтому TTL-кэш и автообновление 60с.
    """
    def _build() -> dict:
        from geoanalytics.analytics.graph_weight import turnover_and_change
        from geoanalytics.analytics.prices import latest_live_market
        from geoanalytics.api.charts import market_heatmap
        from geoanalytics.context.graph import assets_in_sector
        from geoanalytics.storage.models import Sector

        with session_scope() as session:
            sectors = session.scalars(select(Sector).order_by(Sector.name)).all()
            sec_assets = {sec: assets_in_sector(session, sec.id) for sec in sectors}
            all_assets = [a for assets in sec_assets.values() for a in assets]
            # Сегодняшние данные — из живого среза MOEX (VALTODAY-оборот + изменение к закрытию);
            # для бумаг вне live-фида (фонды/неликвид) — фолбэк на последнюю дневную свечу.
            live = latest_live_market(session, [a.ticker for a in all_assets])
            eod = turnover_and_change(session, [a.id for a in all_assets])
            groups = []
            for sec in sectors:
                items = []
                for a in sec_assets[sec]:
                    lv = live.get(a.ticker)
                    if lv and lv[0]:                      # есть сегодняшний оборот
                        turnover, pct = lv
                    elif a.id in eod and eod[a.id][0] > 0:  # иначе вчерашняя свеча
                        turnover, pct = eod[a.id]
                    else:
                        continue
                    items.append({"label": a.ticker, "value": turnover, "pct": pct})
                if items:
                    groups.append({"label": sec.name, "items": items})
        return {"heatmap": market_heatmap(groups)}

    return _cached("market_heatmap", _build)


def _factors_context() -> dict:
    """Страница «Факторы»: сырьё (Brent, драгметаллы в USD) и валюты (курсы ЦБ + кросс).

    Для каждого ряда — мини-спарклайн за год, последний уровень и изменение за период.
    Данные уже есть в БД (macro/fx/учётные цены ЦБ), но UI для их просмотра не было; ряды
    сводит единый слой `analytics.factors`. Тяжёлые полные загрузки серий кэшируются (TTL).
    """
    def _build() -> dict:
        from geoanalytics.analytics.factors import factor_series
        from geoanalytics.storage.repositories import MarketRegimeRepository
        cards: list[dict] = []
        with session_scope() as session:
            for fs in factor_series(session, lookback_days=365):
                spark = sparkline(fs.values, width=320, height=80) if len(fs.values) >= 2 else None
                cards.append({
                    "key": fs.key, "label": fs.label, "unit": fs.unit, "group": fs.group,
                    "last": round(fs.last, 2) if fs.last is not None else None,
                    "change_pct": fs.change_pct, "spark": spark, "n": len(fs.values)})
            regime = _regime_strip(MarketRegimeRepository(session).series(days=180))
        return {"cards": cards, "regime": regime}

    return _cached("factors_report", _build)


def _regime_strip(rows: list, width: int = 640, height: int = 14) -> dict | None:
    """L5: история режимов рынка → цветовая полоса для дашборда (спокойный↑/переходный/кризис↓)."""
    if not rows:
        return None
    n = len(rows)
    max_state = max((r.state for r in rows), default=0) or 1

    def _cls(state: int) -> str:
        frac = state / max_state
        return "up" if frac < 0.34 else "flat" if frac < 0.67 else "down"

    cells = [{"x": round(width * i / n, 1), "w": round(width / n, 1) + 0.6,
              "cls": _cls(r.state), "label": f"{r.day}: {r.label}"}
             for i, r in enumerate(rows)]
    last = rows[-1]
    return {"current": last.label, "day": last.day, "vol": last.vol,
            "cells": cells, "width": width, "height": height,
            "first_day": rows[0].day}


@router.get("/ui/factors", response_class=HTMLResponse)
def factors_page(request: Request):
    """Страница факторов: сырьё и валютные пары с мини-графиками и динамикой."""
    return templates.TemplateResponse(request, "factors.html", _factors_context())


# Трек 2: панель песочницы фьючерсного бумажного счёта (наблюдение за созреванием).
_TRACK2_ACCOUNT = "demo"


def _attr_rows(by: dict) -> tuple[list[dict], float]:
    """Атрибуция P&L {имя→₽} → строки для бар-чарта (по убыванию) + макс. модуль для ширины."""
    rows = sorted(by.items(), key=lambda kv: kv[1], reverse=True)
    mx = max((abs(v) for _, v in rows), default=0.0)
    return ([{"label": k, "pnl": v, "pct": (abs(v) / mx * 100.0 if mx else 0.0)}
             for k, v in rows], mx)


def _track2_context() -> dict:
    """Панель Трека 2: трек-рекорд бумажного счёта (read-only наблюдение за созреванием).

    Зеркало CLI `track-record`/`risk-status`/`drift`. Всё считают готовые раннеры futrader —
    ничего нового тут не вычисляем. СТРОГО READ-ONLY: дрейф вызываем с `auto_halt=False`, иначе
    раннер ВЗВЁЛ БЫ kill-switch и слал алерт. ORM-объекты разворачиваем в простые dict внутри
    сессии (TTL-кэш переживает её закрытие). Тяжёлое (трек-рекорд+дрейф) — через TTL-кэш.
    """
    def _build() -> dict:
        from geoanalytics.futrader.decisions import SIGNAL_FNS
        from geoanalytics.futrader.monitoring import run_drift_monitor
        from geoanalytics.futrader.risk_limits import RiskLimits
        from geoanalytics.futrader.track import track_record
        from geoanalytics.storage.repositories import (
            FuturesPaperRepository,
            FuturesRiskStateRepository,
        )

        account = _TRACK2_ACCOUNT
        with session_scope() as session:
            rec = track_record(session, account=account)
            repo = FuturesPaperRepository(session)
            all_positions = repo.positions(account)
            positions = []
            for p in all_positions:
                if p.net_qty == 0:
                    continue
                # duration_bars: сколько 1h-баров с момента последнего входа
                last_entry = repo.last_entry_ts(account, p.asset_code, p.interval, p.source)
                duration_bars = None
                if last_entry:
                    elapsed = (datetime.now(UTC) - last_entry.replace(tzinfo=UTC)
                               if last_entry.tzinfo is None else
                               datetime.now(UTC) - last_entry)
                    duration_bars = max(0, int(elapsed.total_seconds() / 3600))
                # unrealized P&L %
                unreal_pct = None
                if p.avg_price and p.last_price and p.avg_price > 0:
                    unreal_pct = round((p.last_price / p.avg_price - 1) * 100, 2)
                positions.append({
                    "asset_code": p.asset_code, "interval": p.interval, "source": p.source,
                    "net_qty": p.net_qty, "avg_price": p.avg_price, "last_price": p.last_price,
                    "realized_pnl": p.realized_pnl, "duration_bars": duration_bars,
                    "unreal_pct": unreal_pct,
                })
            all_trades = repo.recent_trades(account, limit=50)
            trades = [
                {"ts": t.ts, "asset_code": t.asset_code, "source": t.source, "action": t.action,
                 "signed_qty": t.signed_qty, "price": t.price, "p_win": t.p_win,
                 "realized_pnl": t.realized_pnl, "reason": t.reason,
                 "conviction": t.conviction}
                for t in all_trades[:15]]

            # --- Диагностика выходов (причины + avg P&L) ---
            exit_counts: dict[str, int] = {}
            exit_pnl: dict[str, list[float]] = {}
            for t in all_trades:
                reason = t.reason or "other"
                # группировка: stop_loss / take_profit / time_stop / entry / other
                if reason in ("stop_loss", "take_profit", "time_stop", "entry",
                              "session_flat", "barrier_exit"):
                    key = reason
                else:
                    key = "other"
                exit_counts[key] = exit_counts.get(key, 0) + 1
                if t.realized_pnl is not None:
                    exit_pnl.setdefault(key, []).append(t.realized_pnl)
            exit_diag = []
            for key, cnt in sorted(exit_counts.items(), key=lambda kv: -kv[1]):
                pnls = exit_pnl.get(key, [])
                avg = round(sum(pnls) / len(pnls), 0) if pnls else None
                exit_diag.append({"reason": key, "count": cnt, "avg_pnl": avg,
                                  "pct": round(cnt / max(sum(exit_counts.values()), 1) * 100)})
            time_stop_pct = (exit_counts.get("time_stop", 0) /
                             max(sum(exit_counts.values()), 1) * 100)

            # --- daily_pnl: P&L по дням за 30 дней (для heat-strip) ---
            curve = repo.equity_curve(account, days=30)
            eq_vals = [e.equity for e in curve]
            eq_dates = [e.ts for e in curve]
            # агрегируем equity snapshot → дневной P&L (изменение эквити за день)
            daily_pnl: list[dict] = []
            if curve:
                from collections import defaultdict
                day_eq: dict = defaultdict(list)
                for e in curve:
                    day_key = e.ts.date() if hasattr(e.ts, "date") else str(e.ts)[:10]
                    day_eq[day_key].append(e.equity)
                day_keys = sorted(day_eq.keys())
                for i, dk in enumerate(day_keys):
                    vals = day_eq[dk]
                    if i == 0:
                        pnl_d = 0.0
                    else:
                        prev_vals = day_eq[day_keys[i - 1]]
                        pnl_d = round(vals[-1] - prev_vals[-1], 2)
                    daily_pnl.append({"date": str(dk), "pnl": pnl_d})

            halt = FuturesRiskStateRepository(session).get(account)
            halt_d = ({"halted": halt.halted, "reason": halt.reason,
                       "updated_at": halt.updated_at} if halt else None)
            drift = run_drift_monitor(session, sources=list(SIGNAL_FNS), account=account,
                                      interval="1h", auto_halt=False)

        value_chart = None
        if len(eq_vals) >= 2:
            value_chart = sparkline(eq_vals, width=820, height=200,
                                    labels=date_labels(eq_dates, width=820), dates=eq_dates)
        by_strategy, strat_max = _attr_rows(rec.by_strategy)
        by_instrument, instr_max = _attr_rows(rec.by_instrument)
        return {"account": account, "rec": rec, "metrics": rec.metrics, "risk": rec.risk,
                "limits": RiskLimits(), "halt": halt_d, "value_chart": value_chart,
                "positions": positions, "trades": trades, "drift": drift,
                "by_strategy": by_strategy, "strat_max": strat_max,
                "by_instrument": by_instrument, "instr_max": instr_max,
                "daily_pnl": daily_pnl, "exit_diag": exit_diag,
                "time_stop_pct": round(time_stop_pct)}

    return _cached("track2_report", _build)


@router.get("/ui/track2", response_class=HTMLResponse)
def track2_page(request: Request):
    """Трек 2: песочница фьючерсного бумажного счёта — эквити/метрики/риск/позиции/дрейф."""
    return templates.TemplateResponse(request, "track2.html", _track2_context())


@router.get("/ui/partials/track2", response_class=HTMLResponse)
def track2_partial(request: Request):
    """HTMX-фрагмент панели Трека 2 (автообновление раз в 60с)."""
    return templates.TemplateResponse(request, "_track2.html", _track2_context())


@router.get("/ui/graph/market", response_class=HTMLResponse)
def market_graph_page(request: Request):
    """Большой граф рынка (B): дерево IMOEX → секторы → активы → события."""
    return templates.TemplateResponse(request, "graph_market.html", _market_graph_context())


@router.get("/ui/partials/graph/market", response_class=HTMLResponse)
def market_graph_partial(request: Request):
    """HTMX-фрагмент большого графа (D): автообновление раз в 60с."""
    return templates.TemplateResponse(request, "_graph_svg.html", _market_graph_context())


@router.get("/ui/partials/graph/heatmap", response_class=HTMLResponse)
def market_heatmap_partial(request: Request):
    """HTMX-фрагмент карты рынка (объём/изменение по секторам): автообновление раз в 60с."""
    return templates.TemplateResponse(request, "_market_heatmap.html", _market_heatmap_context())


def _status_context() -> dict:
    """Статус-фид пайплайна (Волна 6в): свежесть ингеста/бэклог обработки/последний алерт.
    Короткий TTL — почти live, без нагрузки на БД при автообновлении."""
    def _build() -> dict:
        from geoanalytics.query.status import pipeline_status
        with session_scope() as session:
            return {"status": pipeline_status(session)}

    return _cached("pipeline_status", _build, ttl=20.0)


@router.get("/ui/partials/status", response_class=HTMLResponse)
def status_partial(request: Request):
    """HTMX-фрагмент статус-фида (автообновление раз в 30с на дашборде)."""
    return templates.TemplateResponse(request, "_status.html", _status_context())


def _pulse_context() -> dict:
    """Прототип «Пульс рынка» (Направление A): 14-дн ряд рыночного `sent_ewma` → пульс-линия героя.

    Серия из `market_sentiment` (scope="market"); рисуем через готовый `charts.sparkline`. Нулевая
    линия (нейтраль) показывается, только если 0 попадает в диапазон ряда. Сбой/мало точек → None
    (шаблон деградирует к консенсус-блоку без пульса)."""
    from geoanalytics.analytics import market_sentiment
    from geoanalytics.api.charts import sparkline
    from geoanalytics.core.logging import get_logger
    from geoanalytics.storage.db import session_scope

    try:
        with session_scope() as session:
            vals = [r.sent_ewma for r in market_sentiment.series(session, "market", days=14)]
    except Exception as exc:  # noqa: BLE001 — пульс не валит дашборд
        get_logger("api.web").warning("pulse_context_failed", error=str(exc))
        return {"pulse": None}
    pad, height = 10, 92
    chart = sparkline(vals, width=680, height=height, pad=pad)
    if chart is None:
        return {"pulse": None}
    lo, hi, span = chart["min"], chart["max"], (chart["max"] - chart["min"]) or 1.0
    zero_y = (round(pad + (height - 2 * pad) * (1 - (0 - lo) / span), 1)
              if lo <= 0 <= hi else None)
    return {"pulse": {"chart": chart, "up": vals[-1] >= 0, "zero_y": zero_y, "days": len(vals)}}


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, hours: int = 24):
    """Дашборд: сводка рынка «что по новостям» + статус-фид пайплайна."""
    snap = build_snapshot(hours=hours, use_llm=False)
    return templates.TemplateResponse(request, "dashboard.html",
                                      {"snap": snap, "hours": hours,
                                       **_pulse_context(), **_status_context()})


@router.get("/ui/portfolio", response_class=HTMLResponse)
def portfolio_page(request: Request):
    """Страница портфеля (J1): позиции, P&L, риск, факторная экспозиция, режим."""
    return templates.TemplateResponse(request, "portfolio.html", _portfolio_context())


@router.post("/ui/portfolio/add", response_class=HTMLResponse)
def portfolio_add(request: Request, ticker: str = Form(...), quantity: float = Form(...),
                  price: float | None = Form(None)):
    """Добавить/нарастить позицию формой → перерисовать страницу (#4).

    Неверный ввод (qty≤0 → ValueError, нет тикера → None) глушим: просто показываем
    страницу без изменений, как `alert_mute`.
    """
    try:
        _add_position(ticker, quantity, price)
    except ValueError:
        pass  # qty≤0 — не пишем молча нулевую/короткую позицию
    _invalidate_cache("portfolio_report")
    _invalidate_cache("portfolio_stance")
    return templates.TemplateResponse(request, "portfolio.html", _portfolio_context())


@router.post("/ui/portfolio/remove", response_class=HTMLResponse)
def portfolio_remove(request: Request, ticker: str = Form(...)):
    """Удалить позицию формой → перерисовать страницу (#4)."""
    _remove_position(ticker)
    _invalidate_cache("portfolio_report")
    _invalidate_cache("portfolio_stance")
    return templates.TemplateResponse(request, "portfolio.html", _portfolio_context())


@router.post("/ui/portfolio/cash", response_class=HTMLResponse)
def portfolio_cash(request: Request, currency: str = Form(...), amount: float = Form(...)):
    """Задать/удалить (amount≤0) валютный баланс владельца формой → перерисовать страницу."""
    from geoanalytics.storage.repositories import CashBalanceRepository
    with session_scope() as session:
        CashBalanceRepository(session).set_balance(currency, amount)
    _invalidate_cache("portfolio_report")
    _invalidate_cache("portfolio_stance")
    return templates.TemplateResponse(request, "portfolio.html", _portfolio_context())


@router.get("/ui/graph", response_class=HTMLResponse)
def graph_page(request: Request, ticker: str = "SBER"):
    """Граф влияния: отдельная страница — дерево «актив → сектор/события/факторы»."""
    return templates.TemplateResponse(request, "graph.html", _graph_context(ticker))


@router.get("/ui/partials/graph", response_class=HTMLResponse)
def graph_partial(request: Request, ticker: str = "SBER"):
    """HTMX-фрагмент графа (D): автообновление точек влияния по hx-trigger every 60s."""
    return templates.TemplateResponse(request, "_graph_svg.html", _graph_context(ticker))


@router.get("/ui/partials/ask", response_class=HTMLResponse)
def ask_partial(request: Request, q: str = ""):
    """HTMX-фрагмент (и GET-фолбэк без JS): ответ на свободный вопрос поверх аналитики."""
    if not q.strip():
        return HTMLResponse('<p class="muted">Задайте вопрос — например, '
                            '«как дела у Сбербанка?».</p>')
    result = ask_answer(q)
    return templates.TemplateResponse(request, "_ask_result.html", {"r": result})


@router.get("/ui/partials/news", response_class=HTMLResponse)
def news_partial(request: Request, hours: int = 24, limit: int = 15):
    """HTMX-фрагмент: лента свежих заголовков (для авто-обновления дашборда)."""
    headlines = recent_headlines(hours=hours, limit=limit)
    return templates.TemplateResponse(
        request, "_news_feed.html", {"headlines": headlines, "hours": hours, "limit": limit}
    )


@router.get("/ui/asset", response_class=HTMLResponse)
def asset_page(request: Request, ticker: str | None = None):
    """Страница актива (полная). При наличии `ticker` сразу показывает отчёт. По умолчанию IMOEX."""
    if not ticker or not ticker.strip():
        ticker = "IMOEX"
    ctx: dict = {"ticker": ticker, "assets": list_assets()}
    ctx.update(_asset_context(ticker))
    return templates.TemplateResponse(request, "asset.html", ctx)


@router.get("/ui/partials/asset", response_class=HTMLResponse)
def asset_partial(request: Request, ticker: str = ""):
    """HTMX-фрагмент с отчётом по активу."""
    if not ticker or not ticker.strip():
        return HTMLResponse("<p class=\"muted\">Введите тикер</p>")
    return templates.TemplateResponse(request, "_asset_result.html", _asset_context(ticker))


@router.get("/ui/partials/asset/chart", response_class=HTMLResponse)
def asset_chart_partial(request: Request, ticker: str = "", range: str = "6m",
                        period: str = "D", kind: str = "line",
                        ovl: int = 1, vol: int = 1, osc: int = 1):
    """HTMX-фрагмент графика актива (диапазон/период/тип + тумблеры индикаторов)."""
    if not ticker.strip():
        return HTMLResponse('<p class="muted">Введите тикер.</p>')
    return templates.TemplateResponse(
        request, "_asset_chart.html",
        _chart_context(ticker, range, period, kind, bool(ovl), bool(vol), bool(osc)),
    )


@router.get("/ui/partials/asset/indicators", response_class=HTMLResponse)
def asset_indicators_partial(request: Request, ticker: str = "", period: str = "D"):
    """HTMX-фрагмент панели индикаторов на таймфрейме D/W/M (A7)."""
    if not ticker.strip():
        return HTMLResponse('<p class="muted">Введите тикер.</p>')
    return templates.TemplateResponse(
        request, "_indicators.html", _indicators_context(ticker, period))


@router.get("/ui/backtest", response_class=HTMLResponse)
def backtest_page(request: Request, ticker: str | None = None, strategy: str = "sma_cross"):
    """Экран бэктеста (полный). При наличии `ticker` сразу показывает результат."""
    ctx: dict = {"ticker": ticker, "strategy": strategy, "strategies": _STRATEGIES,
                 "assets": list_assets()}
    if ticker and ticker.strip():
        ctx.update(_backtest_context(ticker, strategy))
    return templates.TemplateResponse(request, "backtest.html", ctx)


@router.get("/ui/partials/backtest", response_class=HTMLResponse)
def backtest_partial(request: Request, ticker: str = "", strategy: str = "sma_cross"):
    """HTMX-фрагмент с результатом бэктеста."""
    if not ticker.strip():
        return HTMLResponse('<p class="muted">Введите тикер.</p>')
    return templates.TemplateResponse(request, "_backtest_result.html",
                                      _backtest_context(ticker, strategy))


# --------------------------------------------------------------------------- #
# Алерты: лента + управление (ack/mute/unmute).
# --------------------------------------------------------------------------- #
def _alerts_context(hours: int, severity: str, alert_type: str, ticker: str,
                    only_unacked: bool) -> dict:
    """Отфильтрованная лента алертов + значения фильтров для UI."""
    alerts = recent_alerts(
        hours=hours, severity=severity or None, alert_type=alert_type or None,
        ticker=ticker or None, only_unacked=only_unacked,
    )
    return {"alerts": alerts, "hours": hours, "severity": severity,
            "alert_type": alert_type, "ticker": ticker, "only_unacked": only_unacked,
            "alert_types": _ALERT_TYPES, "severities": _SEVERITIES}


@router.get("/ui/alerts", response_class=HTMLResponse)
def alerts_page(request: Request, hours: int = 168, severity: str = "",
                alert_type: str = "", ticker: str = "", only_unacked: bool = False):
    """Страница алертов: лента с фильтрами + панель правил подавления."""
    ctx = _alerts_context(hours, severity, alert_type, ticker, only_unacked)
    ctx.update({"mutes": manage.list_mutes(), "scope_types": manage.SCOPE_TYPES})
    return templates.TemplateResponse(request, "alerts.html", ctx)


@router.get("/ui/partials/alerts", response_class=HTMLResponse)
def alerts_partial(request: Request, hours: int = 168, severity: str = "",
                   alert_type: str = "", ticker: str = "", only_unacked: bool = False):
    """HTMX-фрагмент: отфильтрованная лента алертов."""
    ctx = _alerts_context(hours, severity, alert_type, ticker, only_unacked)
    return templates.TemplateResponse(request, "_alerts_feed.html", ctx)


@router.post("/ui/alerts/{alert_id}/ack", response_class=HTMLResponse)
def alert_ack(request: Request, alert_id: int):
    """Подтвердить (ack) алерт → вернуть обновлённую строку (HTMX outerHTML-своп)."""
    manage.acknowledge(alert_id)
    alert = get_alert(alert_id)
    if alert is None:
        return HTMLResponse("", status_code=404)
    return templates.TemplateResponse(request, "_alert_row.html", {"a": alert})


@router.post("/ui/alerts/mute", response_class=HTMLResponse)
def alert_mute(request: Request, scope_type: str = Form(...), scope_value: str = Form(...),
               days: int | None = Form(None), reason: str = Form("")):
    """Создать правило подавления → вернуть обновлённую панель mutes."""
    try:
        manage.mute_for_days(scope_type, scope_value, days, reason=reason or None)
    except ValueError:
        pass  # пустой/неверный scope — просто перерисуем панель без изменений
    return templates.TemplateResponse(
        request, "_alert_mutes.html",
        {"mutes": manage.list_mutes(), "scope_types": manage.SCOPE_TYPES},
    )


@router.post("/ui/alerts/unmute/{mute_id}", response_class=HTMLResponse)
def alert_unmute(request: Request, mute_id: int):
    """Снять правило подавления → вернуть обновлённую панель mutes."""
    manage.unmute(mute_id)
    return templates.TemplateResponse(
        request, "_alert_mutes.html",
        {"mutes": manage.list_mutes(), "scope_types": manage.SCOPE_TYPES},
    )
