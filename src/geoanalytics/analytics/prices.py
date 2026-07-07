"""Доступ к ценовым рядам из БД и сборка индикаторов по активу."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from geoanalytics.analytics.indicators import TechnicalIndicators, compute_technical
from geoanalytics.storage.models import Price, RawDocument

# Тип одной OHLC-свечи: (timestamp, open, high, low, close).
OhlcRow = tuple[datetime, float, float, float, float]
# То же с объёмом: (timestamp, open, high, low, close, volume|None).
OhlcvRow = tuple[datetime, float, float, float, float, float | None]


def _latest_live_payloads(session: Session, tickers: list[str]) -> dict[str, dict]:
    """Свежий (по `fetched_at`) payload среза MOEX на каждый запрошенный тикер из `raw_documents`.

    Общий сканер живого среза: payload несёт `last`/`volume` (VALTODAY, оборот ₽ за сегодня)/
    `change_pct` (к вчерашнему закрытию). Берём ПЕРВЫЙ (самый свежий) на тикер. Чистое чтение.
    """
    if not tickers:
        return {}
    want = set(tickers)
    stmt = (
        select(RawDocument.payload)
        .where(RawDocument.source == "moex", RawDocument.payload.isnot(None))
        .order_by(desc(RawDocument.fetched_at))
        .limit(2000)
    )
    out: dict[str, dict] = {}
    for (p,) in session.execute(stmt):
        t = p.get("ticker")
        if t in want and t not in out:
            out[t] = p
            if len(out) == len(want):
                break
    return out


def latest_live_prices(session: Session, tickers: list[str]) -> dict[str, float]:
    """Свежие интрадей-котировки (LAST) из последнего среза MOEX в `raw_documents`.

    Дашборд («топ-движения») и оценка портфеля должны показывать ОДНУ цену: оба берут
    последний живой LAST, записанный ингестом, а не дневную свечу из `prices` (она может
    отставать на часы — внутри дня свеча не переписывается). Возвращает тикер→цена только
    для тикеров со свежим срезом; для остальных оценщик берёт EOD-закрытие. Чистое чтение.
    """
    out: dict[str, float] = {}
    for t, p in _latest_live_payloads(session, tickers).items():
        try:
            out[t] = float(p["last"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def latest_live_market(session: Session,
                       tickers: list[str]) -> dict[str, tuple[float | None, float | None]]:
    """Живой интрадей-срез: тикер → (оборот за сегодня ₽ = VALTODAY, изменение % к закрытию).

    Для карты рынка по СЕГОДНЯШНИМ данным (площадь — оборот, цвет — изменение). Тикеры без среза
    отсутствуют — вызывающий берёт EOD-фолбэк. Чистое чтение.
    """
    out: dict[str, tuple[float | None, float | None]] = {}
    for t, p in _latest_live_payloads(session, tickers).items():
        try:
            turnover = float(p["volume"])
        except (KeyError, TypeError, ValueError):
            turnover = None
        try:
            chg = float(p["change_pct"])
        except (KeyError, TypeError, ValueError):
            chg = None
        out[t] = (turnover, chg)
    return out


def latest_live_price(session: Session, ticker: str) -> float | None:
    """Свежий интрадей-LAST одного тикера (или None, если среза нет). Чистое чтение."""
    return latest_live_prices(session, [ticker.upper()]).get(ticker.upper())


def apply_live_last(session: Session, ticker: str, indicators: dict,
                    period: str = "D") -> float | None:
    """Переопределяет отображаемую цену `last` живым LAST из среза MOEX (баг свод-панели).

    Свод-карточка показывала `last` = закрытие последней дневной свечи (`prices`), а дашборд
    «топ-движения» и портфель — живой LAST (`latest_live_prices`); внутри дня свеча отстаёт на
    часы, поэтому цены расходились. Здесь подменяем ТОЛЬКО показываемую `last` (RSI/тренд/SMA
    считаются по закрытиям и не трогаются — это правильно). Только дневной таймфрейм: на W/M
    `last` — закрытие сжатого бара, живой тик там не к месту. Возвращает живую цену или None.
    """
    if period != "D" or not indicators:
        return None
    live = latest_live_price(session, ticker)
    if live is not None:
        indicators["last"] = round(live, 2)
    return live


def close_series(session: Session, asset_id: int, interval: str = "1d",
                 limit: int = 300) -> list[float]:
    """Возвращает ряд закрытий (старое → новое) по активу.

    limit отбирает ПОСЛЕДНИЕ свечи (desc + разворот), а не первые: иначе при истории
    длиннее limit индикаторы (last/rsi/тренд) считались бы по устаревшему окну, а `last`
    оказывался бы ценой ~limit дней назад, а не текущей.
    """
    stmt = (
        select(Price.close)
        .where(Price.asset_id == asset_id, Price.interval == interval)
        .order_by(desc(Price.ts))
        .limit(limit)
    )
    closes = [float(c) for (c,) in session.execute(stmt)]
    return closes[::-1]


def ohlc_series(session: Session, asset_id: int, interval: str = "1d",
                since: datetime | None = None, limit: int = 2000) -> list[OhlcRow]:
    """OHLC-свечи (старое → новое) по активу. `since` ограничивает диапазон по дате.

    open/high/low подстраховываются close, если в источнике их не было.
    """
    stmt = select(Price.ts, Price.open, Price.high, Price.low, Price.close).where(
        Price.asset_id == asset_id, Price.interval == interval
    )
    if since is not None:
        stmt = stmt.where(Price.ts >= since)
    # desc + limit + разворот: при истории длиннее limit берём ПОСЛЕДНИЕ свечи, а не
    # первые (иначе график показывал бы устаревший хвост). Порядок на выходе — старое→новое.
    stmt = stmt.order_by(desc(Price.ts)).limit(limit)
    out: list[OhlcRow] = []
    for ts, o, h, low, c in session.execute(stmt):
        close = float(c)
        out.append((ts, float(o) if o is not None else close,
                    float(h) if h is not None else close,
                    float(low) if low is not None else close, close))
    out.reverse()
    return out


def ohlcv_series(session: Session, asset_id: int, interval: str = "1d",
                 since: datetime | None = None, limit: int = 2000) -> list[OhlcvRow]:
    """OHLC-свечи с объёмом (старое → новое). Как `ohlc_series`, но +volume (может быть None).

    open/high/low подстраховываются close; volume отдаётся как есть (None — если в источнике
    его не было). Нужен графику для сабпанели объёма (C2).
    """
    stmt = select(
        Price.ts, Price.open, Price.high, Price.low, Price.close, Price.volume
    ).where(Price.asset_id == asset_id, Price.interval == interval)
    if since is not None:
        stmt = stmt.where(Price.ts >= since)
    stmt = stmt.order_by(desc(Price.ts)).limit(limit)
    out: list[OhlcvRow] = []
    for ts, o, h, low, c, v in session.execute(stmt):
        close = float(c)
        out.append((ts, float(o) if o is not None else close,
                    float(h) if h is not None else close,
                    float(low) if low is not None else close, close,
                    float(v) if v is not None else None))
    out.reverse()
    return out


def asset_indicators(session: Session, asset_id: int, interval: str = "1d",
                     limit: int = 300, period: str = "D") -> TechnicalIndicators:
    """Считает технические индикаторы по истории цен актива (включая OHLC и объём).

    `period` (A7): "D" — дневной таймфрейм (как раньше); "W"/"M" — недельный/месячный
    (дневные свечи сжимаются `resample_ohlcv` перед расчётом). `compute_technical`
    таймфрейм-агностична. На W/M тянем всю доступную дневную историю и берём последние
    `limit` сжатых баров; индикаторы с длинным окном (SMA200) останутся None, если
    истории не хватает — это честно, без бэкфилла глубины не будет.
    """
    from geoanalytics.analytics.resample import resample_ohlcv

    if period in ("W", "M"):
        # Нужна вся дневная история, чтобы было что сжимать; затем последние `limit` баров.
        bars = resample_ohlcv(ohlcv_series(session, asset_id, interval=interval, limit=5000),
                              period)[-limit:]
        if not bars:
            return compute_technical([])
        closes = [c for _, _, _, _, c, _ in bars]
        highs = [h for _, _, h, _, _, _ in bars]
        lows = [low for _, _, _, low, _, _ in bars]
        volumes = [v for _, _, _, _, _, v in bars]
        return compute_technical(closes, highs=highs, lows=lows, volumes=volumes)

    stmt = (
        select(Price.high, Price.low, Price.close, Price.volume)
        .where(Price.asset_id == asset_id, Price.interval == interval)
        .order_by(desc(Price.ts))
        .limit(limit)
    )
    rows = list(session.execute(stmt))[::-1]  # старое → новое
    if not rows:
        return compute_technical([])
    closes = [float(c) for _, _, c, _ in rows]
    highs = [float(h) if h is not None else closes[i] for i, (h, _, _, _) in enumerate(rows)]
    lows = [float(low) if low is not None else closes[i] for i, (_, low, _, _) in enumerate(rows)]
    volumes = [float(v) if v is not None else None for _, _, _, v in rows]
    return compute_technical(closes, highs=highs, lows=lows, volumes=volumes)
