"""Трек 2 / Пул 9 / B: жёсткие риск-лимиты, kill-switch и детектор аномалий песочницы.

Песочницу оставляют работать БЕЗ ОПЕРАТОРА — значит нужны институциональные pre-trade-проверки и
аварийный стоп, а не только мягкие внутрицикловые брейкер/бюджет. Здесь — ЧИСТЫЕ предикаты (без БД):
дневной лимит убытка, жёсткий потолок брутто-маржи (выше мягкого бюджета сайзинга), лимит позиции на
инструмент и детекторы аномалий данных (устаревший бар, скачок цены). Состояние kill-switch
(halted) и алерты — на DB-слое поверх (`paper.py`, репозиторий, scheduler).

Принцип: лимит/аномалия БЛОКИРУЕТ новые входы (и поднимает halt+алерт), но НЕ закрывает позиции
силой — дериск идёт через обычные выходы/брейкер.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class RiskLimits:
    """Жёсткие лимиты счёта (консервативные дефолты для безоператорного созревания)."""

    max_daily_loss_pct: float = 6.0        # просадка эквити за день от дневного пика → halt
    max_gross_margin_pct: float = 80.0     # ЖЁСТКИЙ потолок брутто-маржи (мягкий бюджет 50%)
    max_position_per_instrument: int = 12  # макс. |контрактов| в одной позиции
    max_bar_staleness_hours: float = 72.0  # последний бар старше → фид, вероятно, мёртв
    max_price_jump_pct: float = 25.0       # скачок между соседними барами → аномалия цены
    entry_max_bar_age_mult: float = 3.0    # ВХОД: бар не старше mult×интервала (стейл/выходные)
    min_entry_vol_z: float = -1.5          # ВХОД: объём не ниже z (тонкая сессия → пропуск)


def daily_loss_breached(day_peak: float, equity: float, *, max_daily_loss_pct: float) -> bool:
    """Просадка эквити в пределах текущего дня от дневного пика достигла лимита?"""
    if day_peak <= 0:
        return False
    return (day_peak - equity) >= max_daily_loss_pct / 100.0 * day_peak


def gross_margin_breached(margin_used: float, equity: float, *,
                          max_gross_margin_pct: float) -> bool:
    """Валовая занятая маржа пробила ЖЁСТКИЙ потолок плеча (аварийный, не мягкий бюджет)?"""
    if equity <= 0:
        return False
    return margin_used >= max_gross_margin_pct / 100.0 * equity


def position_limit_breached(net_qty: int, *, max_position: int) -> bool:
    """Размер позиции превысил лимит контрактов на инструмент?"""
    return abs(net_qty) > max_position


def bar_stale(last_ts: datetime | None, now: datetime, *, max_hours: float) -> bool:
    """Последний бар слишком стар (фид данных, вероятно, мёртв) — не торгуем на устаревшем."""
    if last_ts is None:
        return False
    return (now - last_ts).total_seconds() / 3600.0 > max_hours


def price_jump_anomaly(prev_close: float | None, close: float, *, max_move_pct: float) -> bool:
    """Неправдоподобный скачок цены между соседними барами (битый тик / роллова артефакт)?"""
    if not prev_close or prev_close <= 0:
        return False
    return abs(close / prev_close - 1.0) * 100.0 > max_move_pct


_INTERVAL_HOURS = {"1m": 1 / 60, "5m": 5 / 60, "10m": 10 / 60, "1h": 1.0, "1d": 24.0}


def interval_hours(interval: str) -> float:
    """Длительность бара в часах (для оценки свежести). Неизвестный → 1ч (консервативно)."""
    return _INTERVAL_HOURS.get(interval, 1.0)


def entry_bar_too_stale(last_ts: datetime | None, now: datetime, *,
                        interval: str, mult: float) -> bool:
    """Свежесть бара для ВХОДА (строже грубого фид-гейта `bar_stale`): последний бар не должен быть
    старше mult×длительности интервала. На выходных / вне торговой сессии свежих баров нет → входы
    блокируются (выходы по-прежнему разрешены). Чинит фиктивную торговлю на залежавшемся баре."""
    if last_ts is None:
        return False
    return bar_stale(last_ts, now, max_hours=interval_hours(interval) * mult)


def thin_liquidity(volume: float | None, vol_z: float | None, *, min_vol_z: float) -> bool:
    """Сессия слишком тонкая для входа: нет объёма (None/0) или объём аномально низок (vol_z<порог).

    `vol_z` нормирован по инструменту (z-оценка относительно окна) → порог инструмент-агностичен.
    Тонкая ликвидность также повышает проскальзывание (см. `execution.slippage_liquidity_mult`)."""
    if volume is None or volume <= 0:
        return True
    return vol_z is not None and vol_z < min_vol_z


@dataclass
class RiskCheck:
    halt: bool = False
    reasons: tuple = ()


def pre_trade_check(*, day_peak: float, equity: float, margin_used: float,
                    limits: RiskLimits) -> RiskCheck:
    """Счётные pre-trade-проверки (дневной убыток + жёсткая маржа) → нужно ли уйти в halt."""
    reasons: list[str] = []
    if daily_loss_breached(day_peak, equity, max_daily_loss_pct=limits.max_daily_loss_pct):
        reasons.append("дневной убыток")
    if gross_margin_breached(margin_used, equity, max_gross_margin_pct=limits.max_gross_margin_pct):
        reasons.append("брутто-маржа")
    return RiskCheck(halt=bool(reasons), reasons=tuple(reasons))
