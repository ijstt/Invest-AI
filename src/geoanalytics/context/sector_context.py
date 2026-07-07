"""Контекст отрасли/сектора как объекта анализа.

Агрегирует сигналы по всем активам сектора: средние технические индикаторы и breadth
(сколько растёт/падает), макро-драйверы сектора, общий новостной фон и значимые события.
По образцу context/asset_context.build_context, переиспользует те же кирпичи (indicators,
macro, news_background, события). Результат — drivers для render_grounding (секция AGGREGATE).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from geoanalytics.analytics.macro import macro_snapshot
from geoanalytics.analytics.prices import asset_indicators
from geoanalytics.context.asset_context import _news_background
from geoanalytics.context.events import top_impacts_for_asset
from geoanalytics.context.graph import assets_in_sector, sector_macro_factors


def aggregate_indicators(indicators: list) -> dict:
    """Агрегат технических индикаторов по активам сектора (чистая функция).

    Возвращает count, средние ret_1m/rsi14 и breadth (сколько активов в up/down тренде).
    """
    rets = [i.ret_1m for i in indicators if i.ret_1m is not None]
    rsis = [i.rsi14 for i in indicators if i.rsi14 is not None]
    up = sum(1 for i in indicators if i.trend == "up")
    down = sum(1 for i in indicators if i.trend == "down")
    out: dict = {"count": len(indicators), "breadth_up": up, "breadth_down": down}
    if rets:
        out["avg_ret_1m"] = round(sum(rets) / len(rets), 2)
    if rsis:
        out["avg_rsi14"] = round(sum(rsis) / len(rsis), 1)
    return out


def build_sector_context(session: Session, sector_id: int, sector_name: str) -> dict:
    """Собирает drivers сектора для grounding. Без LLM (нарратив строит ask поверх)."""
    assets = assets_in_sector(session, sector_id)
    asset_ids = [a.id for a in assets]
    inds = [asset_indicators(session, a.id) for a in assets]

    # События сектора: объединяем влияющие события по всем активам, дедуп по заголовку.
    events, seen = [], set()
    for a in assets:
        for e in top_impacts_for_asset(session, a.id):
            key = e.get("title")
            if key and key not in seen:
                seen.add(key)
                events.append(e)
    events.sort(key=lambda e: e.get("magnitude", 0), reverse=True)

    bg = _news_background(session, asset_ids)
    return {
        "factors": {
            "sector": sector_name,
            "macro_factors": sector_macro_factors(sector_name),
            "peers": [a.ticker for a in assets][:10],
        },
        "aggregate": aggregate_indicators(inds),
        "macro": macro_snapshot(session).as_dict(),
        "impacting_events": events[:5],
        "news": {"recent_count": bg.recent_count, "sentiment": bg.sentiment,
                 "top_events": bg.top_events},
    }
