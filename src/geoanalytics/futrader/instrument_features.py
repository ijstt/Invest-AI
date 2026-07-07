"""Трек 2 / Пул 6: признаки структуры срочности (контанго/бэквордация) по инструменту.

Спред ближнего и следующего контракта — КЛАССИЧЕСКИЙ фьючерсный фактор: контанго (дальний дороже)
vs бэквордация (дальний дешевле) кодирует carry/roll-yield и часто предсказывает доходность лучше
чистой цены. У нас в `futures_candles` лежат несколько контрактов на инструмент — считаем term-slope
прямо из них (по каждому ts берём два ближайших по экспирации контракта с данными).

Per-инструмент и time-varying (в отличие от рыночно-глобального `EdgeContext`). Строим карту
`{ts: term_slope%}` один раз на инструмент и джойним к решениям по ts.
"""

from __future__ import annotations

from datetime import datetime


def term_structure_map(session, asset_code: str, interval: str) -> dict[datetime, float]:
    """{ts: term_slope%} — спред (следующий/ближний − 1)·100 по двум ближайшим контрактам.

    Положительный = контанго (дальний дороже), отрицательный = бэквордация. На ts с <2 контрактами
    с данными пропуск. Читает `futures_candles` напрямую (per-инструмент).
    """
    from sqlalchemy import select

    from geoanalytics.storage.models import FuturesCandle

    rows = session.execute(
        select(FuturesCandle.ts, FuturesCandle.expiry, FuturesCandle.close)
        .where(FuturesCandle.asset_code == asset_code, FuturesCandle.interval == interval)
    ).all()
    by_ts: dict[datetime, list[tuple]] = {}
    for ts, expiry, close in rows:
        if expiry is not None and close:
            by_ts.setdefault(ts, []).append((expiry, close))

    out: dict[datetime, float] = {}
    for ts, items in by_ts.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda x: x[0])           # по экспирации: [0]=ближний, [1]=следующий
        front_close, next_close = items[0][1], items[1][1]
        if front_close:
            out[ts] = round((next_close / front_close - 1.0) * 100, 4)
    return out
