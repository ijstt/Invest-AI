"""Единый вес узла графа влияния из ВСЕХ доступных зависимостей (не только новостей).

Раньше размер узла-пира/сектора был хардкод-константой (0.4/0.7/0.8), а реальный сигнал
несли только события (magnitude) и факторы (|корреляция|). Здесь вес узла-актива
складывается из нормированных в [0,1] сигналов: новостное давление, тональный моментум и
сила технической картины. Прозрачные веса-константы; недоступные сигналы пропускаются
(взвешенное среднее доступных). Используется раскладкой графа и анти-перекрытием (A3):
тяжёлые узлы держат позицию, лёгкие — расталкиваются.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

# Прозрачные веса вкладов (сумма не обязана равняться 1 — нормируем по доступным).
_W = {"news": 0.5, "sentiment": 0.25, "ta": 0.15, "corr": 0.1}
# Вес узла-макро (нет числового сигнала — драйвер сектора без величины). Не «магия», а константа.
MACRO_WEIGHT = 0.3


def _clamp01(x: float) -> float:
    return min(max(float(x or 0.0), 0.0), 1.0)


def combine(signals: dict[str, float | None]) -> float:
    """Взвешенное среднее доступных нормированных [0,1] сигналов → вес узла [0,1].

    Ключи signals — из `_W` (news/sentiment/ta/corr). None-сигналы пропускаются; если все
    None — вес 0. Так узел с одним лишь давлением и узел с давлением+сентиментом сопоставимы
    (не штрафуем за отсутствие сигнала, а усредняем по тому, что есть).
    """
    num = den = 0.0
    for key, val in signals.items():
        if val is None:
            continue
        w = _W.get(key, 0.0)
        num += w * _clamp01(val)
        den += w
    return num / den if den else 0.0


def ta_strength(indicators: dict) -> float | None:
    """Сила технической картины ∈ [0,1]: насколько актив в экстремуме (None — нет данных).

    RSI: |RSI−50|/50 (перекупленность/перепроданность = сильная картина). MACD-гистограмма:
    |hist| относительно ~2% цены (нормировка к масштабу инструмента). Среднее доступных.
    """
    parts: list[float] = []
    rsi = indicators.get("rsi14")
    if rsi is not None:
        parts.append(min(abs(float(rsi) - 50.0) / 50.0, 1.0))
    hist = indicators.get("macd_hist")
    last = indicators.get("last")
    if hist is not None and last:
        parts.append(min(abs(float(hist)) / (0.02 * abs(float(last))), 1.0))
    return sum(parts) / len(parts) if parts else None


def asset_node_weight(session: Session, asset_id: int, *, pressure: float | None = None,
                      with_sentiment: bool = True, with_ta: bool = False) -> float:
    """Вес узла-актива из его собственных зависимостей (давление + сентимент [+ TA]).

    `pressure` можно передать заранее (большой граф рынка уже считает его для ранжирования —
    не дублируем запрос). `with_ta` дорог (тянет ценовой ряд) — включать на малых графах.
    """
    from geoanalytics.analytics.pressure import news_pressure
    from geoanalytics.analytics.sentiment_trend import latest_momentum

    if pressure is None:
        pressure = news_pressure(session, asset_id, window=7)
    sent = abs(latest_momentum(session, asset_id, span=14) or 0.0) if with_sentiment else None
    ta = None
    if with_ta:
        from geoanalytics.analytics.prices import asset_indicators
        ta = ta_strength(asset_indicators(session, asset_id).as_dict())
    return combine({"news": min(pressure, 1.0), "sentiment": sent, "ta": ta})


def recent_turnover(session: Session, asset_ids: list[int], days: int = 30) -> dict[int, float]:
    """Средний дневной оборот (close·volume) по активам за `days` дней → asset_id→оборот.

    Оборот — справедливый прокси «веса на индексе» (ликвидность/денежный поток через бумагу),
    т.к. реального free-float market cap у нас нет. Одним запросом по `prices`. Пустой словарь,
    если данных нет; активы без оборота просто отсутствуют в результате (вес 0).
    """
    if not asset_ids:
        return {}
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from geoanalytics.storage.models import Price

    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(
        select(Price.asset_id, func.avg(Price.close * Price.volume))
        .where(Price.asset_id.in_(asset_ids), Price.ts >= since,
               Price.volume.is_not(None), Price.close.is_not(None))
        .group_by(Price.asset_id)
    ).all()
    return {aid: float(t) for aid, t in rows if t is not None}


def turnover_and_change(session: Session, asset_ids: list[int],
                        days: int = 10) -> dict[int, tuple[float, float]]:
    """По активам: (последний дневной оборот close·volume, дневное изменение %) для карты рынка.

    Оборот последнего дня — «текущий» объём торгов (размер плитки); изменение = (last−prev)/prev·100
    (цвет). Одним запросом по `prices` за `days` дней; в Python берём две последние свечи на актив.
    Активы без двух свечей/объёма отсутствуют (нет плитки). Чистое чтение.
    """
    if not asset_ids:
        return {}
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from geoanalytics.storage.models import Price

    since = datetime.now(UTC) - timedelta(days=days)
    rows = session.execute(
        select(Price.asset_id, Price.ts, Price.close, Price.volume)
        .where(Price.asset_id.in_(asset_ids), Price.ts >= since, Price.close.is_not(None))
        .order_by(Price.asset_id, Price.ts)
    ).all()
    by_asset: dict[int, list] = {}
    for aid, _ts, close, vol in rows:
        by_asset.setdefault(aid, []).append((float(close), vol))
    out: dict[int, tuple[float, float]] = {}
    for aid, seq in by_asset.items():
        last_close, last_vol = seq[-1]
        turnover = last_close * float(last_vol) if last_vol else 0.0
        prev_close = seq[-2][0] if len(seq) >= 2 else last_close
        change = (last_close - prev_close) / prev_close * 100.0 if prev_close else 0.0
        out[aid] = (turnover, change)
    return out


def normalize_weight(value: float, peak: float, floor: float = 0.2) -> float:
    """Нормирует величину к пику в [floor, 1] (узлы не схлопываются в точку при малом весе)."""
    if peak <= 0:
        return floor
    return _clamp01(floor + (1.0 - floor) * min(value / peak, 1.0))


def aggregate_weight(weights: list[float]) -> float:
    """Вес узла-агрегата (сектор) из весов потомков: блендим доминанту и средний фон.

    0.6·max + 0.4·mean — сектор с одним «горячим» активом крупный, но и широкий ровный фон
    повышает вес. Пустой список → 0.
    """
    if not weights:
        return 0.0
    mx = max(weights)
    mean = sum(weights) / len(weights)
    return _clamp01(0.6 * mx + 0.4 * mean)
