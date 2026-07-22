"""HTMX/Jinja router for market dashboard, news feed, status, and Q&A."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from geoanalytics.api import web
from geoanalytics.storage.db import session_scope

router = APIRouter()


def _status_context() -> dict:
    """Статус-фид пайплайна (Волна 6в): свежесть ингеста/бэклог обработки/последний алерт.
    Короткий TTL — почти live, без нагрузки на БД при автообновлении."""
    def _build() -> dict:
        from geoanalytics.query.status import pipeline_status
        with session_scope() as session:
            return {"status": pipeline_status(session)}

    return web._cached("pipeline_status", _build, ttl=20.0)


def _pulse_context() -> dict:
    """Прототип «Пульс рынка» (Направление A): 14-дн ряд рыночного `sent_ewma` → пульс-линия героя.

    Серия из `market_sentiment` (scope="market"); рисуем через готовый `charts.sparkline`. Нулевая
    линия (нейтраль) показывается, только если 0 попадает в диапазон ряда. Сбой/мало точек → None
    (шаблон деградирует к консенсус-блоку без пульса)."""
    from geoanalytics.analytics import market_sentiment
    from geoanalytics.api.charts import sparkline
    from geoanalytics.core.logging import get_logger

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
    snap = web.build_snapshot(hours=hours, use_llm=False)
    return web.templates.TemplateResponse(request, "dashboard.html",
                                      {"snap": snap, "hours": hours,
                                       **web._pulse_context(), **web._status_context()})


@router.get("/ui/partials/status", response_class=HTMLResponse)
def status_partial(request: Request):
    """HTMX-фрагмент статус-фида (автообновление раз в 30с на дашборде)."""
    return web.templates.TemplateResponse(request, "_status.html", web._status_context())


@router.get("/ui/partials/news", response_class=HTMLResponse)
def news_partial(request: Request, hours: int = 24, limit: int = 15):
    """HTMX-фрагмент: лента свежих заголовков (для авто-обновления дашборда)."""
    headlines = web.recent_headlines(hours=hours, limit=limit)
    return web.templates.TemplateResponse(
        request, "_news_feed.html", {"headlines": headlines, "hours": hours, "limit": limit}
    )


@router.get("/ui/partials/ask", response_class=HTMLResponse)
def ask_partial(request: Request, q: str = ""):
    """HTMX-фрагмент (и GET-фолбэк без JS): ответ на свободный вопрос поверх аналитики."""
    if not q.strip():
        return HTMLResponse('<p class="muted">Задайте вопрос — например, '
                            '«как дела у Сбербанка?».</p>')
    result = web.ask_answer(q)
    return web.templates.TemplateResponse(request, "_ask_result.html", {"r": result})
