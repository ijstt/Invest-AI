"""HTMX/Jinja router for strategy backtesting."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from geoanalytics.api import web
from geoanalytics.api.charts import equity_chart

router = APIRouter()


def _backtest_context(ticker: str, strategy: str) -> dict:
    base = {"ticker": ticker.upper(), "strategy": strategy}
    try:
        result = web.backtest_asset_cached(ticker, strategy=strategy)
    except ValueError as exc:
        return {**base, "error": str(exc)}
    if result is None:
        return {**base, "error": f"Актив {ticker.upper()} не найден"}
    return {**base, "result": result,
            "equity": equity_chart(result.equity_curve, result.trades)}


@router.get("/ui/backtest", response_class=HTMLResponse)
def backtest_page(request: Request, ticker: str | None = None, strategy: str = "sma_cross"):
    """Экран бэктеста (полный). При наличии `ticker` сразу показывает результат."""
    ctx: dict = {"ticker": ticker, "strategy": strategy, "strategies": web._STRATEGIES,
                 "assets": web.list_assets()}
    if ticker and ticker.strip():
        ctx.update(web._backtest_context(ticker, strategy))
    return web.templates.TemplateResponse(request, "backtest.html", ctx)


@router.get("/ui/partials/backtest", response_class=HTMLResponse)
def backtest_partial(request: Request, ticker: str = "", strategy: str = "sma_cross"):
    """HTMX-фрагмент с результатом бэктеста."""
    if not ticker.strip():
        return HTMLResponse('<p class="muted">Введите тикер.</p>')
    return web.templates.TemplateResponse(request, "_backtest_result.html",
                                      web._backtest_context(ticker, strategy))
