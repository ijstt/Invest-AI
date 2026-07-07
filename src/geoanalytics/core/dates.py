"""Парсинг дат из разных источников в timezone-aware datetime (UTC)
и привязка новостей к торговым датам."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

# Московская зона и закрытие основной сессии МосБиржи. Дневная свеча фиксируется
# на закрытии — новость, вышедшая ПОСЛЕ него, не может повлиять на цену этого дня.
MOEX_TZ = ZoneInfo("Europe/Moscow")
MOEX_CLOSE_MSK = time(18, 50)


def trading_effective_date(published_at: datetime) -> date:
    """Торговая дата новости: дата по МСК; после закрытия сессии — следующий день.

    Б3 (Волна 1): без этого сдвига новость, опубликованная в 20:00 дня T, влияла на
    сигнал/исход дня T (lookahead — решение на закрытии T принималось «по будущей»
    новости). Используется бэктестом (sentiment_gate) и рыночной разметкой новостей
    (news_outcomes). Naive datetime считается UTC. Выходные НЕ сдвигаются здесь:
    потребители сами сопоставляют дату с ближайшим торговым днём (pub ≤ d).
    """
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=UTC)
    msk = published_at.astimezone(MOEX_TZ)
    d = msk.date()
    if msk.time() > MOEX_CLOSE_MSK:
        d += timedelta(days=1)
    return d


def parse_cbr_date(value: str | None) -> datetime | None:
    """Дата ЦБ в формате dd.mm.yyyy → datetime (UTC, полночь)."""
    if not value:
        return None
    try:
        dt = datetime.strptime(value.strip(), "%d.%m.%Y")
        return dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def parse_moex_systime(value: str | None, *, to_day: bool = True) -> datetime | None:
    """SYSTIME МосБиржи 'YYYY-MM-DD HH:MM:SS' → datetime (UTC).

    При to_day=True округляет до начала дня (для дневных свечей и дедупа).
    """
    if not value:
        return None
    try:
        dt = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    dt = dt.replace(tzinfo=UTC)
    if to_day:
        dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return dt


def parse_rss_date(value: str | None) -> datetime | None:
    """Дата из RSS (RFC 822) → datetime (UTC)."""
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
