"""FastAPI-приложение: тонкая HTTP-обёртка над слоем `query`/`analytics` (M5).

API ничего не считает сам — он вызывает те же функции, что и CLI
(`build_snapshot`, `build_report`, `backtest_asset`, `recent_events`), и отдаёт
результат как JSON по Pydantic-схемам. Это даёт основу для веб-дашборда (M5.2) и
алертов (M5.3).

Запуск:
    uvicorn geoanalytics.api.app:app --reload
Документация (Swagger): http://localhost:8000/docs
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from geoanalytics.analytics.backtest import backtest_asset
from geoanalytics.api import web
from geoanalytics.api.schemas import (
    AlertResponse,
    AssetInfo,
    AssetResponse,
    BacktestResponse,
    EventResponse,
    HealthResponse,
    NewsResponse,
    SourceInfo,
    TradeSchema,
)
from geoanalytics.connectors import all_connectors, available
from geoanalytics.core.logging import get_logger
from geoanalytics.query.alerts_feed import recent_alerts
from geoanalytics.query.asset_report import build_report
from geoanalytics.query.assets_feed import list_assets
from geoanalytics.query.events_feed import recent_events
from geoanalytics.query.news_summary import build_snapshot

app = FastAPI(
    title="geoanalytics API",
    version="0.1.0",
    description="Аналитика рынка РФ: новости, активы, бэктест, события.",
)

# Веб-дашборд (HTMX/Jinja): /, /ui/asset, /ui/backtest.
app.include_router(web.router)

log = get_logger("api")

_ERROR_HTML = (
    "<main style='font-family:system-ui,sans-serif;max-width:680px;margin:48px auto;'>"
    "<h1>Что-то пошло не так</h1>"
    "<p>Внутренняя ошибка при обработке запроса. Детали — в логах сервера.</p>"
    "<p><a href='/'>← На дашборд</a></p></main>"
)


@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception):
    """Непойманное исключение в раннере → вежливая страница/JSON вместо 500-стека.

    Раннеры аналитики в основном возвращают graceful `.error`, но не каждый путь гарантирован
    (LLM-синтез, единичные DB-хиккапы) — этот предохранитель держит дашборд живым."""
    log.error("api_unhandled", path=str(request.url.path), error=str(exc))
    if "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(_ERROR_HTML, status_code=500)
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Проверка живости и числа зарегистрированных источников."""
    return HealthResponse(status="ok", sources=len(available()))


@app.get("/sources", response_model=list[SourceInfo])
def sources() -> list[SourceInfo]:
    """Список доступных источников данных."""
    return [SourceInfo(name=c.name, kind=str(c.kind)) for c in all_connectors()]


@app.get("/assets", response_model=list[AssetInfo])
def assets() -> list[AssetInfo]:
    """Список активов (для автодополнения тикеров в дашборде)."""
    return [AssetInfo.model_validate(a) for a in list_assets()]


@app.get("/news", response_model=NewsResponse)
def news(hours: int = 24, use_llm: bool = False) -> NewsResponse:
    """Сводка «что по новостям»: макро, топ-движения, тональность, заголовки.

    LLM-синтез по умолчанию выключен (медленный на CPU) — включается `use_llm=true`.
    """
    snap = build_snapshot(hours=hours, use_llm=use_llm)
    return NewsResponse.model_validate(snap, from_attributes=True)


@app.get("/asset/{ticker}", response_model=AssetResponse)
def asset(ticker: str, rebuild: bool = False, use_llm: bool = False) -> AssetResponse:
    """Аналитический отчёт по активу. 404, если тикер не найден."""
    report = build_report(ticker, rebuild=rebuild, use_llm=use_llm)
    if not report.found:
        raise HTTPException(status_code=404, detail=report.note or "актив не найден")
    return AssetResponse.model_validate(report, from_attributes=True)


@app.get("/backtest/{ticker}", response_model=BacktestResponse)
def backtest(ticker: str, strategy: str = "sma_cross") -> BacktestResponse:
    """Бэктест сигнала по истории актива (sma_cross | momentum | rsi | sentiment)."""
    try:
        result = backtest_asset(ticker, strategy=strategy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"актив {ticker.upper()} не найден")
    return BacktestResponse(
        ticker=ticker.upper(),
        strategy=strategy,
        bars=result.bars,
        total_return_pct=result.total_return_pct,
        buy_hold_return_pct=result.buy_hold_return_pct,
        cagr_pct=result.cagr_pct,
        sharpe=result.sharpe,
        max_drawdown_pct=result.max_drawdown_pct,
        hit_rate=result.hit_rate,
        num_trades=result.num_trades,
        exposure=result.exposure,
        equity_curve=result.equity_curve,
        trades=[TradeSchema.model_validate(t, from_attributes=True) for t in result.trades],
    )


@app.get("/events", response_model=list[EventResponse])
def events(hours: int = 168, limit: int = 20) -> list[EventResponse]:
    """Последние значимые события и их влияние на активы."""
    return [EventResponse.model_validate(e) for e in recent_events(hours=hours, limit=limit)]


@app.get("/alerts", response_model=list[AlertResponse])
def alerts(hours: int = 168, limit: int = 50) -> list[AlertResponse]:
    """Лента сработавших алертов (движения цен, всплески негатива, новые события)."""
    return [AlertResponse.model_validate(a) for a in recent_alerts(hours=hours, limit=limit)]
