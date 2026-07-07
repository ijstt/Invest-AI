"""Лента значимых событий и их влияния на активы.

Единая выборка для CLI (`geo events`) и API (`GET /events`): последние события
из таблицы `events` с присоединённым влиянием на активы (`event_impacts`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select

from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import Asset, Event, EventImpact


def recent_events(hours: int = 168, limit: int = 20) -> list[dict]:
    """Последние события за `hours` часов с топ-влиянием на активы.

    Возвращает список словарей:
    `{event_type, title, occurred_at (ISO), impacts: [{ticker, direction, magnitude}]}`.
    """
    since = datetime.now(UTC) - timedelta(hours=hours)
    out: list[dict] = []
    with session_scope() as session:
        events = session.scalars(
            select(Event).where(Event.occurred_at >= since)
            .order_by(desc(Event.occurred_at)).limit(limit)
        )
        for ev in events:
            rows = session.execute(
                select(Asset.ticker, EventImpact.direction, EventImpact.magnitude)
                .join(EventImpact, EventImpact.asset_id == Asset.id)
                .where(EventImpact.event_id == ev.id)
                .order_by(EventImpact.magnitude.desc())
            )
            impacts = [
                {"ticker": ticker, "direction": direction, "magnitude": magnitude}
                for ticker, direction, magnitude in rows
            ]
            out.append({
                "event_type": ev.event_type,
                "title": ev.title,
                "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
                "impacts": impacts,
            })
    return out
