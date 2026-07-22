"""HTMX/Jinja router for market factors and regime history."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from geoanalytics.api import web
from geoanalytics.api.charts import sparkline
from geoanalytics.storage.db import session_scope

router = APIRouter()


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
            regime = web._regime_strip(MarketRegimeRepository(session).series(days=180))
        return {"cards": cards, "regime": regime}

    return web._cached("factors_report", _build)


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
    return web.templates.TemplateResponse(request, "factors.html", web._factors_context())
