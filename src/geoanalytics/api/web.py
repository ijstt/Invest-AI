"""Веб-дашборд на HTMX + Jinja (M5.2).

Server-rendered страницы поверх той же логики, что у CLI/JSON-API: дашборд рынка,
страница актива (с SVG-графиком цены) и экран бэктеста (с кривой капитала).
HTMX подменяет фрагменты результата без перезагрузки; формы — обычный GET, поэтому
страницы работают и без JavaScript (прогрессивное улучшение).
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

from geoanalytics.alerts import manage
from geoanalytics.analytics.backtest import PRICE_STRATEGIES, backtest_asset_cached
from geoanalytics.query.alerts_feed import get_alert, recent_alerts
from geoanalytics.query.ask import answer as ask_answer
from geoanalytics.query.asset_report import build_report
from geoanalytics.query.assets_feed import list_assets
from geoanalytics.query.news_summary import build_snapshot, recent_headlines

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


# Import sub-routers
from geoanalytics.api.routers import (
    alerts,
    asset,
    backtest,
    dashboard,
    factors,
    graph,
    portfolio,
    track2,
)

# Re-exports for test compatibility and monkeypatching
_status_context = dashboard._status_context
_pulse_context = dashboard._pulse_context
_asset_ohlcv = asset._asset_ohlcv
_sentiment_cells = asset._sentiment_cells
_price_overlays = asset._price_overlays
_chart_event_markers = asset._chart_event_markers
_chart_context = asset._chart_context
_indicators_context = asset._indicators_context
_asset_context = asset._asset_context
_factor_trend = asset._factor_trend
_backtest_context = backtest._backtest_context
_portfolio_context = portfolio._portfolio_context
_compute_portfolio_stance = portfolio._compute_portfolio_stance
_add_position = portfolio._add_position
_remove_position = portfolio._remove_position
_compute_portfolio_report = portfolio._compute_portfolio_report
_FACTOR_CSS = graph._FACTOR_CSS
_FACTOR_RU = graph._FACTOR_RU
_EVENT_AGG = graph._EVENT_AGG
_graph_context = graph._graph_context
_market_graph_context = graph._market_graph_context
_market_heatmap_context = graph._market_heatmap_context
_factors_context = factors._factors_context
_regime_strip = factors._regime_strip
_TRACK2_ACCOUNT = track2._TRACK2_ACCOUNT
_attr_rows = track2._attr_rows
_track2_context = track2._track2_context
_alerts_context = alerts._alerts_context

# Register sub-routers
router.include_router(dashboard.router)
router.include_router(asset.router)
router.include_router(backtest.router)
router.include_router(portfolio.router)
router.include_router(graph.router)
router.include_router(factors.router)
router.include_router(track2.router)
router.include_router(alerts.router)
