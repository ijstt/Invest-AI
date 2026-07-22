"""HTMX/Jinja router for portfolio management, positions, risk, and cash balance."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from geoanalytics.api import web
from geoanalytics.api.charts import date_labels, pie, sparkline, treemap
from geoanalytics.storage.db import session_scope

router = APIRouter()


def _portfolio_context() -> dict:
    """Отчёт по портфелю (J1) для веб-страницы — зеркало `geo portfolio`.

    Корреляции/экспозицию заранее раскладываем в списки: ключи-кортежи неудобны в Jinja.
    """
    report = web._cached("portfolio_report", web._compute_portfolio_report)
    # Среднесрочная сводка-стойка (недельный ТФ) — отдельный TTL (тяжелее: ресемпл+стойки).
    stance = web._cached("portfolio_stance", lambda: web._compute_portfolio_stance(report), ttl=300.0)
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
            "assets": web.list_assets()}


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

    with session_scope() as session:
        return live_portfolio_report(session)


@router.get("/ui/portfolio", response_class=HTMLResponse)
def portfolio_page(request: Request):
    """Страница портфеля (J1): позиции, P&L, риск, факторная экспозиция, режим."""
    return web.templates.TemplateResponse(request, "portfolio.html", web._portfolio_context())


@router.post("/ui/portfolio/add", response_class=HTMLResponse)
def portfolio_add(request: Request, ticker: str = Form(...), quantity: float = Form(...),
                  price: float | None = Form(None)):
    """Добавить/нарастить позицию формой → перерисовать страницу (#4).

    Неверный ввод (qty≤0 → ValueError, нет тикера → None) глушим: просто показываем
    страницу без изменений, как `alert_mute`.
    """
    try:
        web._add_position(ticker, quantity, price)
    except ValueError:
        pass  # qty≤0 — не пишем молча нулевую/короткую позицию
    web._invalidate_cache("portfolio_report")
    web._invalidate_cache("portfolio_stance")
    return web.templates.TemplateResponse(request, "portfolio.html", web._portfolio_context())


@router.post("/ui/portfolio/remove", response_class=HTMLResponse)
def portfolio_remove(request: Request, ticker: str = Form(...)):
    """Удалить позицию формой → перерисовать страницу (#4)."""
    web._remove_position(ticker)
    web._invalidate_cache("portfolio_report")
    web._invalidate_cache("portfolio_stance")
    return web.templates.TemplateResponse(request, "portfolio.html", web._portfolio_context())


@router.post("/ui/portfolio/cash", response_class=HTMLResponse)
def portfolio_cash(request: Request, currency: str = Form(...), amount: float = Form(...)):
    """Задать/удалить (amount≤0) валютный баланс владельца формой → перерисовать страницу."""
    from geoanalytics.storage.repositories import CashBalanceRepository
    with session_scope() as session:
        CashBalanceRepository(session).set_balance(currency, amount)
    web._invalidate_cache("portfolio_report")
    web._invalidate_cache("portfolio_stance")
    return web.templates.TemplateResponse(request, "portfolio.html", web._portfolio_context())
