"""Трек 2 / Pool 2: двусторонние (лонг+шорт) сигналы для фьючерсов.

Фьючерс симметричен — шорт без borrow-ограничений, маржа двусторонняя. Лонг-онли сигналы
(`backtest.*_signals`, 0/1) теряют половину возможностей. Здесь — направленные версии −1/0/+1
(шорт/вне/лонг) тех же стратегий, переиспользующие индикаторы (`analytics.indicators`), без
дублирования матчасти. Чистые (на вход — closes).

Трендовые (sma_cross/momentum/macd) — всегда в рынке по знаку тренда. Возвратные (rsi/bollinger) —
стейтфул: вход у экстремума в сторону возврата к центру, выход к средней, симметрично по обе
стороны. Модель T2.4 берёт направление как признак (`dir`) — учит P(win) условно по стороне.
"""

from __future__ import annotations

from geoanalytics.analytics.candlesticks import candles_directional
from geoanalytics.analytics.indicators import bollinger, macd_hist_series, rsi


def sma_cross_directional(closes: list[float], fast: int = 20, slow: int = 50) -> list[int]:
    """Тренд по SMA: +1 пока быстрая выше медленной, иначе −1 (в рынке после прогрева)."""
    out = []
    for i in range(len(closes)):
        end = i + 1
        if end < slow:
            out.append(0)
            continue
        fast_ma = sum(closes[end - fast:end]) / fast
        slow_ma = sum(closes[end - slow:end]) / slow
        out.append(1 if fast_ma > slow_ma else -1)
    return out


def momentum_directional(closes: list[float], lookback: int = 20) -> list[int]:
    """Моментум: +1 если цена выше, чем `lookback` назад, иначе −1."""
    out = []
    for i in range(len(closes)):
        if i < lookback:
            out.append(0)
            continue
        out.append(1 if closes[i] > closes[i - lookback] else -1)
    return out


def macd_directional(closes: list[float], fast: int = 12, slow: int = 26,
                     signal: int = 9) -> list[int]:
    """Тренд по MACD: +1 при положительной гистограмме, −1 при отрицательной (вне — в прогреве)."""
    hist = macd_hist_series(closes, fast, slow, signal)
    return [0 if h is None else (1 if h > 0 else -1) for h in hist]


def rsi_directional(closes: list[float], window: int = 14, low: float = 30.0,
                    high: float = 70.0, mid: float = 50.0) -> list[int]:
    """Возврат к среднему по RSI: лонг при перепроданности (<low), шорт при перекупленности (>high),
    выход к середине (mid). Состояние переносится между барами."""
    out, pos = [], 0
    for i in range(len(closes)):
        value = rsi(closes[:i + 1], window)
        if value is None:
            out.append(0)
            continue
        if pos == 0:
            if value < low:
                pos = 1
            elif value > high:
                pos = -1
        elif pos == 1 and value >= mid:
            pos = 0
        elif pos == -1 and value <= mid:
            pos = 0
        out.append(pos)
    return out


def bollinger_directional(closes: list[float], window: int = 20, k: float = 2.0) -> list[int]:
    """Возврат по Боллинджеру: лонг у нижней полосы, шорт у верхней, выход к средней. Стейтфул."""
    out, pos = [], 0
    for i in range(len(closes)):
        band = bollinger(closes[:i + 1], window, k)
        if band is None:
            out.append(0)
            continue
        lower, midband, upper = band
        price = closes[i]
        if pos == 0:
            if price <= lower:
                pos = 1
            elif price >= upper:
                pos = -1
        elif pos == 1 and price >= midband:
            pos = 0
        elif pos == -1 and price <= midband:
            pos = 0
        out.append(pos)
    return out


# имя стратегии → функция направленных сигналов (−1/0/+1). Ключи == SIGNAL_FNS.
# Свечные (candles) принимают OHLC, индикаторные — только closes; ветвление в apply_strategy.
DIRECTIONAL_FNS = {
    "sma_cross": sma_cross_directional,
    "momentum": momentum_directional,
    "rsi": rsi_directional,
    "macd": macd_directional,
    "bollinger": bollinger_directional,
    "candles": candles_directional,
}

# Стратегии, которым на вход нужны OHLC (свечные паттерны Нисона), а не только closes.
OHLC_STRATEGIES = frozenset({"candles"})


def apply_strategy(fn, strat: str, closes, *, opens=None, highs=None, lows=None) -> list[int]:
    """Единый диспетч сигнал-функции: свечные стратегии получают OHLC (kwargs), индикаторные —
    только closes. Используется paper/decisions/policy, чтобы не дублировать ветвление по входу."""
    if strat in OHLC_STRATEGIES:
        return fn(closes, opens=opens, highs=highs, lows=lows)
    return fn(closes)


def cross_sectional_signals(closes_by_code: dict[str, list[float]], *, lookback: int = 20,
                            top_frac: float = 0.34) -> dict[str, list[int]]:
    """Кросс-секционный моментум (институциональный класс альфы): на каждом ВЫРОВНЕННОМ баре
    ранжировать инструменты по доходности за `lookback` и поставить ЛОНГ на топ-фракцию, ШОРТ на
    нижнюю, 0 в середине — портфельно-нейтральная ставка «сильные обгонят слабых».

    На вход — выровненные по времени ряды `{code: closes}` ОДНОЙ длины. Возвращает `{code: signals}`
    той же длины (−1/0/+1). Это НЕ одно-инструментная стратегия: сигнал инструмента зависит от ВСЕХ.
    """
    codes = list(closes_by_code)
    n = min((len(v) for v in closes_by_code.values()), default=0)
    out: dict[str, list[int]] = {c: [0] * n for c in codes}
    for t in range(lookback, n):
        mom = {c: closes_by_code[c][t] / closes_by_code[c][t - lookback] - 1.0
               for c in codes if closes_by_code[c][t - lookback] > 0}
        if len(mom) < 3:                       # ранг бессмыслен на <3 инструментах
            continue
        ranked = sorted(mom, key=lambda c: mom[c])
        k = max(1, int(len(ranked) * top_frac))
        for c in ranked[-k:]:
            out[c][t] = 1                      # лучшие по моментуму → лонг
        for c in ranked[:k]:
            out[c][t] = -1                     # худшие → шорт
    return out


# Кросс-секционные стратегии (сигнал зависит от ВСЕХ инструментов; вне per-instrument FNS).
CROSS_SECTIONAL = ("xsec_mom",)
