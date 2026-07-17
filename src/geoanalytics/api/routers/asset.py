from datetime import UTC, datetime, timedelta
import bisect
from collections import defaultdict
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from geoanalytics.api import web
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Asset, Article, ArticleEntity, Event, EventImpact
from geoanalytics.core.types import EntityType
from geoanalytics.analytics.prices import ohlcv_series, asset_indicators, apply_live_last
from geoanalytics.analytics.resample import resample_ohlcv
from geoanalytics.analytics.indicators import sma, bollinger
from geoanalytics.api.charts import candles, sparkline, volume_bars, rsi_panel, date_labels, sentiment_strip

from geoanalytics.query.assets_feed import list_assets
from geoanalytics.query.asset_report import build_report

router = APIRouter()


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
    by_day = defaultdict(list)
    for pub, score in rows:
        by_day[pub.date()].append(float(score))
    return [{"label": d.strftime("%d.%m"), "score": sum(v) / len(v)}
            for d, v in sorted(by_day.items())]


def _price_overlays(closes: list[float]) -> list[dict]:
    """Наложения на цену: линии SMA20/50/200 и полосы Bollinger (C1)."""
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
    """Маркеры событий влияния для графика актива (#5): событие → ближайший бар."""
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
    """Данные графика по выбранным диапазону/периоду/типу (для HTMX-партиала)."""
    rows = web._asset_ohlcv(ticker, web._CHART_RANGES.get(rng))
    if period in ("W", "M"):
        rows = resample_ohlcv(rows, period)
    ohlc = [(r[0], r[1], r[2], r[3], r[4]) for r in rows]   # без объёма — для candles/sparkline
    closes = [r[4] for r in rows]
    volumes = [r[5] for r in rows]
    ups = [r[4] >= r[1] for r in rows]                      # close ≥ open
    bar_dates = [r[0] for r in rows]
    labels = date_labels(bar_dates)
    overlays = web._price_overlays(closes) if ovl else []
    markers = web._chart_event_markers(ticker, bar_dates)       # #5: события на графике
    if kind == "candles":
        chart = candles(ohlc, height=260, labels=labels, overlays=overlays, markers=markers)
    else:
        chart = sparkline(closes, height=240, labels=labels, overlays=overlays,
                          markers=markers, dates=bar_dates)
    return {"ticker": ticker.upper(), "chart": chart, "chart_kind": kind,
            "chart_range": rng, "chart_period": period, "ranges": list(web._CHART_RANGES),
            "volpanel": volume_bars(volumes, ups) if vol else None,
            "oscpanel": rsi_panel(closes) if osc else None,
            "chart_ovl": int(ovl), "chart_vol": int(vol), "chart_osc": int(osc)}


def _indicators_context(ticker: str, period: str = "D") -> dict:
    """Индикаторы актива на выбранном таймфрейме D/W/M (A7) — для панели и HTMX-тумблера."""
    period = period if period in ("D", "W", "M") else "D"
    with session_scope() as session:
        asset = session.scalars(
            select(Asset).where(Asset.ticker == ticker.upper())
        ).first()
        ind = asset_indicators(session, asset.id, period=period).as_dict() if asset else {}
        if asset:
            apply_live_last(session, ticker, ind, period)
    return {"ticker": ticker.upper(), "indicators": ind, "ind_period": period}


def _asset_context(ticker: str) -> dict:
    report = web.build_report(ticker, rebuild=False, use_llm=False)
    ctx = web._chart_context(ticker) if report.found else {
        "chart": None, "chart_kind": "line", "chart_range": "6m", "chart_period": "D",
        "ranges": list(web._CHART_RANGES), "volpanel": None, "oscpanel": None,
        "chart_ovl": 1, "chart_vol": 1, "chart_osc": 1}
    sentiment = sentiment_strip(web._sentiment_cells(ticker)) if report.found else None
    factor_trend = web._factor_trend(ticker) if report.found else None
    return {"report": report, "ticker": ticker.upper(), "sentiment": sentiment,
            "factor_trend": factor_trend,
            "indicators": report.indicators, "ind_period": "D", **ctx}


def _factor_trend(ticker: str):
    """L5: спарклайн композитного факторного z-скора во времени (None, если <2 дней истории)."""
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


@router.get("/ui/asset", response_class=HTMLResponse)
def asset_page(request: Request, ticker: str | None = None):
    """Страница актива (полная). При наличии `ticker` сразу показывает отчёт. По умолчанию IMOEX."""
    if not ticker or not ticker.strip():
        ticker = "IMOEX"
    ctx: dict = {"ticker": ticker, "assets": web.list_assets()}
    ctx.update(web._asset_context(ticker))
    return web.templates.TemplateResponse(request, "asset.html", ctx)


@router.get("/ui/partials/asset", response_class=HTMLResponse)
def asset_partial(request: Request, ticker: str = ""):
    """HTMX-фрагмент с отчётом по активу."""
    if not ticker or not ticker.strip():
        return HTMLResponse("<p class=\"muted\">Введите тикер</p>")
    return web.templates.TemplateResponse(request, "_asset_result.html", web._asset_context(ticker))


@router.get("/ui/partials/asset/chart", response_class=HTMLResponse)
def asset_chart_partial(request: Request, ticker: str = "", range: str = "6m",
                        period: str = "D", kind: str = "line",
                        ovl: int = 1, vol: int = 1, osc: int = 1):
    """HTMX-фрагмент графика актива (диапазон/период/тип + тумблеры индикаторов)."""
    if not ticker.strip():
        return HTMLResponse('<p class="muted">Введите тикер.</p>')
    return web.templates.TemplateResponse(
        request, "_asset_chart.html",
        web._chart_context(ticker, range, period, kind, bool(ovl), bool(vol), bool(osc)),
    )


@router.get("/ui/partials/asset/indicators", response_class=HTMLResponse)
def asset_indicators_partial(request: Request, ticker: str = "", period: str = "D"):
    """HTMX-фрагмент панели индикаторов на таймфрейме D/W/M (A7)."""
    if not ticker.strip():
        return HTMLResponse('<p class="muted">Введите тикер.</p>')
    return web.templates.TemplateResponse(
        request, "_indicators.html", web._indicators_context(ticker, period))
