"""Контекст страны/экономики как объекта анализа.

Для РФ — самый полный объект: макро (ставка ЦБ, валюты, сырьё) + срез рынка (движения,
тональность) + секторный срез + геополитика. Для внешних стран (США/ЕС/Китай) данных по
активам нет — graceful: только релевантное макро (внешние ставки) и связанные новости.
Переиспользует build_snapshot (рынок), assets_in_sector+aggregate_indicators (секторный
срез), recent_events (геополитика). Возвращает (drivers, related-briefs) для grounding.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from geoanalytics.analytics.macro import macro_snapshot
from geoanalytics.analytics.prices import asset_indicators
from geoanalytics.context.graph import assets_in_sector
from geoanalytics.context.sector_context import aggregate_indicators
from geoanalytics.query.events_feed import recent_events
from geoanalytics.query.news_summary import build_snapshot
from geoanalytics.storage.models import Country, Sector

_GEO_EVENT_TYPES = {"sanctions", "geopolitics"}


def build_country_context(session: Session, country_id: int,
                          country_name: str) -> tuple[dict, list[str]]:
    """drivers + related-briefs для страны. РФ — полный; прочие — макро + новости."""
    country = session.get(Country, country_id)
    country_code = country.code if country else ""
    drivers: dict = {"macro": macro_snapshot(session).as_dict()}
    related: list[str] = []

    if country_code != "RUS":
        # Внешние экономики: нет рынка РФ — отдаём макро (внешние ставки релевантны).
        return drivers, related

    snap = build_snapshot(hours=24, use_llm=False)
    sb = snap.sentiment_breakdown
    drivers["news"] = {"recent_count": sum(sb.values()), "sentiment": sb,
                       "top_events": snap.top_events}
    if snap.top_gainers:
        related.append("Рынок РФ, растут: "
                       + ", ".join(m["ticker"] for m in snap.top_gainers[:5]))
    if snap.top_losers:
        related.append("Рынок РФ, падают: "
                       + ", ".join(m["ticker"] for m in snap.top_losers[:5]))

    # Секторный срез: средняя месячная доходность по каждому сектору, топ-3 и анти-топ.
    sectors = []
    for sec in session.scalars(select(Sector)):
        inds = [asset_indicators(session, a.id) for a in assets_in_sector(session, sec.id)]
        agg = aggregate_indicators(inds)
        if agg.get("avg_ret_1m") is not None:
            sectors.append((sec.name, agg["avg_ret_1m"]))
    sectors.sort(key=lambda x: x[1], reverse=True)
    for name, ret in sectors[:3]:
        related.append(f"Сектор {name}: {ret:+}% за месяц")

    # Геополитика: значимые события санкций/геополитики.
    geo = [e for e in recent_events(hours=168, limit=20)
           if e.get("event_type") in _GEO_EVENT_TYPES]
    for e in geo[:4]:
        related.append(f"Геополитика: {e.get('title', '')}")

    return drivers, related
