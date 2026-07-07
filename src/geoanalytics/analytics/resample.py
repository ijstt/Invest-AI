"""Агрегация дневных свечей в недельные/месячные (M6).

Чистая функция (без БД), тестируемая: схлопывает OHLC-ряд по календарным неделям
или месяцам. В каждом ведре: open — от первой свечи, close — от последней, high —
максимум, low — минимум; timestamp ведра — последняя дата периода (для подписи оси).
"""

from __future__ import annotations

from collections import OrderedDict

from geoanalytics.analytics.prices import OhlcRow, OhlcvRow


def _bucket_key(ts, period: str) -> tuple:
    """Ключ ведра: ISO-неделя (W) или (год, месяц) (M)."""
    if period == "W":
        iso = ts.isocalendar()
        return (iso.year, iso.week)
    return (ts.year, ts.month)


def resample_ohlcv(rows: list[OhlcvRow], period: str) -> list[OhlcvRow]:
    """Агрегирует дневные свечи С ОБЪЁМОМ в недельные/месячные.

    OHLC схлопывается как в `resample_ohlc` (open первой, close последней, high/low —
    экстремумы), объём — СУММА за период (None-объёмы не учитываются; если все None →
    объём ведра None). Неизвестный период → ряд без изменений.
    """
    if period not in ("W", "M") or not rows:
        return list(rows)
    buckets: OrderedDict[tuple, list] = OrderedDict()
    for ts, o, h, low, c, v in rows:
        key = _bucket_key(ts, period)
        cur = buckets.get(key)
        if cur is None:
            buckets[key] = [ts, o, h, low, c, v]
        else:
            cur[0], cur[2], cur[3], cur[4] = ts, max(cur[2], h), min(cur[3], low), c
            if v is not None:
                cur[5] = (cur[5] or 0.0) + v
    return [tuple(b) for b in buckets.values()]


def resample_ohlc(rows: list[OhlcRow], period: str) -> list[OhlcRow]:
    """Агрегирует дневные свечи в недельные ("W") или месячные ("M").

    Вход — свечи в порядке старое → новое. Неизвестный период → ряд без изменений.
    """
    if period not in ("W", "M") or not rows:
        return list(rows)

    buckets: OrderedDict[tuple, list] = OrderedDict()
    for ts, o, h, low, c in rows:
        if period == "W":
            iso = ts.isocalendar()
            key = (iso.year, iso.week)
        else:
            key = (ts.year, ts.month)
        cur = buckets.get(key)
        if cur is None:
            buckets[key] = [ts, o, h, low, c]  # open фиксируем по первой свече
        else:
            cur[0] = ts                       # ts ведра = последняя дата периода
            cur[2] = max(cur[2], h)           # high
            cur[3] = min(cur[3], low)         # low
            cur[4] = c                        # close = последняя свеча
    return [(b[0], b[1], b[2], b[3], b[4]) for b in buckets.values()]
