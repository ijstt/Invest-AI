"""Лента сработавших алертов — единая выборка для CLI (`geo alerts`), API и веб-UI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, or_, select

from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import AlertRecord

# Сентинел: «без фильтра по владельцу» (вид владельца/админа — все алерты, и broadcast, и личные).
_UNSCOPED = object()


def _to_dict(a: AlertRecord) -> dict:
    """Запись алерта → словарь для CLI/API/UI."""
    return {
        "id": a.id,
        "alert_type": a.alert_type,
        "ticker": a.ticker,
        "user_id": a.user_id,
        "severity": a.severity,
        "title": a.title,
        "message": a.message,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "acknowledged_at": a.acknowledged_at.isoformat() if a.acknowledged_at else None,
        "channels": a.channels or [],
        "payload": a.payload or {},
    }


def recent_alerts(hours: int = 168, limit: int = 50, *, severity: str | None = None,
                  alert_type: str | None = None, ticker: str | None = None,
                  only_unacked: bool = False, user_id=_UNSCOPED) -> list[dict]:
    """Последние алерты за `hours` часов (новые → старые) с опциональными фильтрами.

    `user_id` (5c-изоляция): сентинел `_UNSCOPED` (дашборд/CLI/админ) → все алерты, включая
    чужие персональные; конкретный id → только broadcast (`alerts.user_id IS NULL`) И свои
    (`== user_id`) — чтобы обычный бот-пользователь не видел чужих персональных портфельных
    алертов. Возвращает словари `{id, alert_type, ticker, user_id, severity, title, message,
    created_at (ISO), acknowledged_at (ISO|None), channels, payload}`.
    """
    since = datetime.now(UTC) - timedelta(hours=hours)
    with session_scope() as session:
        stmt = select(AlertRecord).where(AlertRecord.created_at >= since)
        if severity:
            stmt = stmt.where(AlertRecord.severity == severity)
        if alert_type:
            stmt = stmt.where(AlertRecord.alert_type == alert_type)
        if ticker:
            stmt = stmt.where(AlertRecord.ticker == ticker.upper())
        if only_unacked:
            stmt = stmt.where(AlertRecord.acknowledged_at.is_(None))
        if user_id is not _UNSCOPED:
            stmt = stmt.where(or_(AlertRecord.user_id.is_(None),
                                  AlertRecord.user_id == user_id))
        rows = session.scalars(
            stmt.order_by(desc(AlertRecord.created_at)).limit(limit)
        )
        return [_to_dict(a) for a in rows]


def get_alert(alert_id: int) -> dict | None:
    """Один алерт по id (для HTMX-свапа строки после ack) или None."""
    with session_scope() as session:
        a = session.get(AlertRecord, alert_id)
        return _to_dict(a) if a else None
