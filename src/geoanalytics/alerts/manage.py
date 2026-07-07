"""Операции управления алертами (UX-слой): подтверждение (ack) и подавление (mute).

Тонкие DB-операции поверх моделей `AlertRecord`/`AlertMute`. Используются веб-UI
(`api/web.py`) и при желании REST/CLI. Сами правила/движок не трогают —
подавление по mute применяется в `engine.evaluate_and_dispatch`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, desc, select, update

from geoanalytics.core.logging import get_logger
from geoanalytics.storage.db import session_scope
from geoanalytics.storage.models import AlertMute, AlertRecord

log = get_logger("alerts.manage")

SCOPE_TYPES = ("ticker", "type", "ticker_type")


def acknowledge(alert_id: int, *, now: datetime | None = None) -> bool:
    """Помечает алерт как просмотренный. True, если запись найдена."""
    now = now or datetime.now(UTC)
    with session_scope() as session:
        res = session.execute(
            update(AlertRecord)
            .where(AlertRecord.id == alert_id)
            .values(acknowledged_at=now)
        )
        ok = res.rowcount > 0
    if ok:
        log.info("alert_ack", alert_id=alert_id)
    return ok


def mute(scope_type: str, scope_value: str, *, until: datetime | None = None,
         reason: str | None = None, user_id: int | None = None) -> int:
    """Создаёт правило подавления. Возвращает id нового mute.

    `scope_type` ∈ {ticker, type, ticker_type}; `scope_value` — тикер (`SBER`),
    тип алерта (`price_move`) или пара (`SBER:price_move`). `until=None` — бессрочно.
    `user_id=None` (5b) — глобальный mute (для всех); иначе личный mute пользователя.
    """
    if scope_type not in SCOPE_TYPES:
        raise ValueError(f"scope_type must be one of {SCOPE_TYPES}")
    scope_value = scope_value.strip()
    if not scope_value:
        raise ValueError("scope_value is required")
    with session_scope() as session:
        m = AlertMute(scope_type=scope_type, scope_value=scope_value,
                      until=until, reason=reason or None, user_id=user_id)
        session.add(m)
        session.flush()
        mute_id = m.id
    log.info("alert_mute", scope_type=scope_type, scope_value=scope_value,
             until=until.isoformat() if until else None, user_id=user_id)
    return mute_id


def mute_for_days(scope_type: str, scope_value: str, days: int | None,
                  *, reason: str | None = None, now: datetime | None = None) -> int:
    """Удобная обёртка: подавить на `days` дней (None/0 — бессрочно)."""
    now = now or datetime.now(UTC)
    until = now + timedelta(days=days) if days else None
    return mute(scope_type, scope_value, until=until, reason=reason)


def unmute(mute_id: int, *, user_id: int | None = None) -> bool:
    """Удаляет правило подавления. True, если запись была.

    Если задан `user_id` — удалит только СВОЙ mute (защита: нельзя снять чужой/глобальный
    через бота). Без `user_id` (веб-UI/CLI админа) — по id без ограничений.
    """
    with session_scope() as session:
        stmt = delete(AlertMute).where(AlertMute.id == mute_id)
        if user_id is not None:
            stmt = stmt.where(AlertMute.user_id == user_id)
        res = session.execute(stmt)
        ok = res.rowcount > 0
    if ok:
        log.info("alert_unmute", mute_id=mute_id, user_id=user_id)
    return ok


def _mute_dict(m: AlertMute) -> dict:
    return {
        "id": m.id,
        "scope_type": m.scope_type,
        "scope_value": m.scope_value,
        "user_id": m.user_id,
        "reason": m.reason,
        "until": m.until.isoformat() if m.until else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


def list_mutes() -> list[dict]:
    """Все правила подавления (новые → старые) как словари для UI/CLI."""
    with session_scope() as session:
        rows = session.scalars(select(AlertMute).order_by(desc(AlertMute.created_at)))
        return [_mute_dict(m) for m in rows]


def list_user_mutes(user_id: int) -> list[dict]:
    """Личные mute-правила пользователя (новые → старые) — для бота (5b)."""
    with session_scope() as session:
        rows = session.scalars(
            select(AlertMute).where(AlertMute.user_id == user_id)
            .order_by(desc(AlertMute.created_at))
        )
        return [_mute_dict(m) for m in rows]
