"""HTMX/Jinja router for alerts feed, acknowledgement, and mute rules."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from geoanalytics.api import web

router = APIRouter()


def _alerts_context(hours: int, severity: str, alert_type: str, ticker: str,
                    only_unacked: bool) -> dict:
    """Отфильтрованная лента алертов + значения фильтров для UI."""
    alerts = web.recent_alerts(
        hours=hours, severity=severity or None, alert_type=alert_type or None,
        ticker=ticker or None, only_unacked=only_unacked,
    )
    return {"alerts": alerts, "hours": hours, "severity": severity,
            "alert_type": alert_type, "ticker": ticker, "only_unacked": only_unacked,
            "alert_types": web._ALERT_TYPES, "severities": web._SEVERITIES}


@router.get("/ui/alerts", response_class=HTMLResponse)
def alerts_page(request: Request, hours: int = 168, severity: str = "",
                alert_type: str = "", ticker: str = "", only_unacked: bool = False):
    """Страница алертов: лента с фильтрами + панель правил подавления."""
    ctx = web._alerts_context(hours, severity, alert_type, ticker, only_unacked)
    ctx.update({"mutes": web.manage.list_mutes(), "scope_types": web.manage.SCOPE_TYPES})
    return web.templates.TemplateResponse(request, "alerts.html", ctx)


@router.get("/ui/partials/alerts", response_class=HTMLResponse)
def alerts_partial(request: Request, hours: int = 168, severity: str = "",
                   alert_type: str = "", ticker: str = "", only_unacked: bool = False):
    """HTMX-фрагмент: отфильтрованная лента алертов."""
    ctx = web._alerts_context(hours, severity, alert_type, ticker, only_unacked)
    return web.templates.TemplateResponse(request, "_alerts_feed.html", ctx)


@router.post("/ui/alerts/{alert_id}/ack", response_class=HTMLResponse)
def alert_ack(request: Request, alert_id: int):
    """Подтвердить (ack) алерт → вернуть обновлённую строку (HTMX outerHTML-своп)."""
    web.manage.acknowledge(alert_id)
    alert = web.get_alert(alert_id)
    if alert is None:
        return HTMLResponse("", status_code=404)
    return web.templates.TemplateResponse(request, "_alert_row.html", {"a": alert})


@router.post("/ui/alerts/mute", response_class=HTMLResponse)
def alert_mute(request: Request, scope_type: str = Form(...), scope_value: str = Form(...),
               days: int | None = Form(None), reason: str = Form("")):
    """Создать правило подавления → вернуть обновлённую панель mutes."""
    try:
        web.manage.mute_for_days(scope_type, scope_value, days, reason=reason or None)
    except ValueError:
        pass  # пустой/неверный scope — просто перерисуем панель без изменений
    return web.templates.TemplateResponse(
        request, "_alert_mutes.html",
        {"mutes": web.manage.list_mutes(), "scope_types": web.manage.SCOPE_TYPES},
    )


@router.post("/ui/alerts/unmute/{mute_id}", response_class=HTMLResponse)
def alert_unmute(request: Request, mute_id: int):
    """Снять правило подавления → вернуть обновлённую панель mutes."""
    web.manage.unmute(mute_id)
    return web.templates.TemplateResponse(
        request, "_alert_mutes.html",
        {"mutes": web.manage.list_mutes(), "scope_types": web.manage.SCOPE_TYPES},
    )
