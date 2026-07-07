"""Технические индикаторы — чистые функции над рядами цен.

Реализованы на чистом Python (списки float), без pandas: так модуль не тянет
тяжёлых зависимостей, детерминирован и легко тестируется. Ряд закрытий ожидается
в хронологическом порядке (старое → новое).
"""

from __future__ import annotations

from dataclasses import dataclass


def sma(values: list[float], window: int) -> float | None:
    """Простая скользящая средняя по последним `window` значениям."""
    if len(values) < window or window <= 0:
        return None
    return sum(values[-window:]) / window


def _ema_series(values: list[float], window: int) -> list[float]:
    """Ряд EMA: одно значение на каждый бар начиная с `window`-го (старт от SMA).

    Возвращает список длиной ``len(values) - window + 1`` (пусто, если данных мало).
    Используется индикаторами, которым нужна не последняя EMA, а вся траектория
    (например, сигнальная линия MACD — EMA от ряда MACD).
    """
    if len(values) < window or window <= 0:
        return []
    k = 2 / (window + 1)
    e = sum(values[:window]) / window  # старт — SMA первых `window` значений
    series = [e]
    for v in values[window:]:
        e = v * k + e * (1 - k)
        series.append(e)
    return series


def ema(values: list[float], window: int) -> float | None:
    """Экспоненциальная скользящая средняя (последнее значение ряда EMA)."""
    series = _ema_series(values, window)
    return series[-1] if series else None


def macd(values: list[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[float, float, float] | None:
    """MACD: (линия, сигнальная, гистограмма) по последнему бару.

    Линия = EMA(fast) − EMA(slow); сигнальная = EMA(signal) от ряда линии;
    гистограмма = линия − сигнальная. None, если данных < ``slow + signal``
    (не хватает на ряд линии длиной ≥ `signal`). Классические параметры 12/26/9.
    """
    fast_series = _ema_series(values, fast)
    slow_series = _ema_series(values, slow)
    if not slow_series:
        return None
    # Выравниваем по общему хвосту (ряд slow короче): берём столько fast, сколько slow.
    fast_tail = fast_series[-len(slow_series):]
    macd_line = [f - s for f, s in zip(fast_tail, slow_series, strict=True)]
    signal_series = _ema_series(macd_line, signal)
    if not signal_series:
        return None
    macd_val = macd_line[-1]
    signal_val = signal_series[-1]
    return round(macd_val, 4), round(signal_val, 4), round(macd_val - signal_val, 4)


def macd_hist_series(values: list[float], fast: int = 12, slow: int = 26,
                     signal: int = 9) -> list[float | None]:
    """Гистограмма MACD (линия − сигнальная) на каждый бар; None в прогреве.

    Выровнено по `values`: значение на баре доступно, начиная с ``slow + signal - 2``
    (когда хватает данных и на ряд линии, и на сигнальную EMA). Эффективно — один проход
    `_ema_series`, без пере-вычислений. Базис для стратегии MACD-cross и осцилляторных панелей.
    """
    out: list[float | None] = [None] * len(values)
    fast_s = _ema_series(values, fast)
    slow_s = _ema_series(values, slow)
    if not slow_s:
        return out
    fast_tail = fast_s[-len(slow_s):]            # хвост fast выровнен по барам slow
    macd_line = [f - s for f, s in zip(fast_tail, slow_s, strict=True)]
    sig = _ema_series(macd_line, signal)
    if not sig:
        return out
    base = slow + signal - 2                      # бар, которому соответствует sig[0]
    for k, sg in enumerate(sig):
        out[base + k] = macd_line[(signal - 1) + k] - sg
    return out


def bollinger(values: list[float], window: int = 20,
              k: float = 2.0) -> tuple[float, float, float] | None:
    """Полосы Боллинджера: (нижняя, средняя, верхняя) по последним `window` барам.

    Средняя = SMA(window); полосы = средняя ∓ k·σ, где σ — выборочное (n−1)
    стандартное отклонение последних `window` значений. None при нехватке данных.
    """
    if len(values) < window or window <= 1:
        return None
    win = values[-window:]
    mid = sum(win) / window
    var = sum((v - mid) ** 2 for v in win) / (window - 1)
    std = var ** 0.5
    return round(mid - k * std, 2), round(mid, 2), round(mid + k * std, 2)


def rsi(values: list[float], window: int = 14) -> float | None:
    """Индекс относительной силы (RSI) по методу Уайлдера."""
    if len(values) <= window:
        return None
    gains, losses = [], []
    for prev, cur in zip(values[-window - 1:], values[-window:], strict=False):
        change = cur - prev
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def returns_pct(values: list[float], periods: int) -> float | None:
    """Доходность за `periods` баров назад, в процентах."""
    if len(values) <= periods or periods <= 0:
        return None
    past, last = values[-periods - 1], values[-1]
    if past == 0:
        return None
    return round((last - past) / past * 100, 2)


def volatility(values: list[float], window: int = 20) -> float | None:
    """Годовая волатильность как ст. отклонение дневных доходностей (в %)."""
    if len(values) < window + 1:
        return None
    window_vals = values[-(window + 1):]
    rets = [
        (window_vals[i] - window_vals[i - 1]) / window_vals[i - 1]
        for i in range(1, len(window_vals))
        if window_vals[i - 1] != 0
    ]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    daily_std = var ** 0.5
    return round(daily_std * (252 ** 0.5) * 100, 2)  # годовая, в %


def ewma_volatility(values: list[float], lam: float = 0.94,
                    min_obs: int = 30) -> float | None:
    """ДНЕВНАЯ EWMA-волатильность доходностей, в % (G1, RiskMetrics λ=0.94).

    Для vol-нормализации price_move алертов: 5% для голубой фишки и третьего
    эшелона — события разного масштаба, сравнивать движение надо со СВОЕЙ
    волатильностью (z = move/σ). EWMA отдаёт больший вес свежим дням — σ быстро
    подстраивается под смену режима. None — меньше `min_obs` доходностей.
    """
    rets = [
        (values[i] - values[i - 1]) / values[i - 1]
        for i in range(1, len(values))
        if values[i - 1] != 0
    ]
    if len(rets) < min_obs:
        return None
    var = rets[0] ** 2
    for r in rets[1:]:
        var = lam * var + (1 - lam) * r ** 2
    return round((var ** 0.5) * 100, 4)


def high_low(values: list[float]) -> tuple[float, float] | None:
    """Максимум и минимум ряда (например, 52-недельный диапазон)."""
    if not values:
        return None
    return max(values), min(values)


def atr(highs: list[float], lows: list[float], closes: list[float],
        window: int = 14) -> float | None:
    """Average True Range — средний истинный диапазон по OHLC, мера волатильности.

    True Range бара = max(high−low, |high−prev_close|, |low−prev_close|): учитывает
    гэпы между барами, в отличие от простого (high−low). ATR — простое среднее последних
    `window` TR. None, если данных < window+1 или длины рядов не совпадают.
    """
    n = len(closes)
    if n != len(highs) or n != len(lows) or n <= window:
        return None
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, n)
    ]
    return round(sum(trs[-window:]) / window, 4)


def stochastic(highs: list[float], lows: list[float], closes: list[float],
               k_window: int = 14, d_window: int = 3) -> tuple[float, float] | None:
    """Стохастик-осциллятор (%K, %D): где закрытие в диапазоне последних `k_window` баров.

    %K = 100·(close − min_low)/(max_high − min_low) по окну `k_window`; %D — SMA(%K) за
    `d_window`. Высокий %K (>80) — перекупленность, низкий (<20) — перепроданность. None
    при нехватке данных или несовпадении длин.
    """
    n = len(closes)
    if n != len(highs) or n != len(lows) or n < k_window + d_window - 1:
        return None

    def k_at(end: int) -> float | None:
        hh = max(highs[end - k_window:end])
        ll = min(lows[end - k_window:end])
        span = hh - ll
        if span == 0:
            return None
        return 100.0 * (closes[end - 1] - ll) / span

    ks = [k_at(end) for end in range(n - d_window + 1, n + 1)]
    if any(k is None for k in ks):
        return None
    return round(ks[-1], 2), round(sum(ks) / len(ks), 2)


def obv(closes: list[float], volumes: list[float | None]) -> float | None:
    """On-Balance Volume — накопленный объём со знаком направления цены.

    К сумме прибавляем объём бара при росте закрытия и вычитаем при падении (флэт — без
    изменения). Растущий OBV подтверждает тренд объёмом. Бары с None-объёмом не двигают
    OBV. None, если данных < 2 или весь объём отсутствует.
    """
    if len(closes) != len(volumes) or len(closes) < 2:
        return None
    if all(v is None for v in volumes):
        return None
    total = 0.0
    for i in range(1, len(closes)):
        v = volumes[i] or 0.0
        if closes[i] > closes[i - 1]:
            total += v
        elif closes[i] < closes[i - 1]:
            total -= v
    return round(total, 2)


def volume_sma(volumes: list[float | None], window: int = 20) -> float | None:
    """Средний объём за последние `window` баров. None, если в окне есть пропуски."""
    if len(volumes) < window or window <= 0:
        return None
    win = volumes[-window:]
    if any(v is None for v in win):
        return None
    return round(sum(win) / window, 2)


def volume_spike(volumes: list[float | None], window: int = 20) -> float | None:
    """Отношение последнего объёма к среднему за `window`: >1 — всплеск, <1 — затишье."""
    avg = volume_sma(volumes, window)
    if avg is None or avg == 0 or volumes[-1] is None:
        return None
    return round(volumes[-1] / avg, 2)


@dataclass
class TechnicalIndicators:
    """Сводка технических индикаторов по активу."""

    last: float | None = None
    sma20: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    rsi14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    boll_lower: float | None = None
    boll_mid: float | None = None
    boll_upper: float | None = None
    atr14: float | None = None
    stoch_k: float | None = None
    stoch_d: float | None = None
    vol_annual: float | None = None
    obv: float | None = None
    vol_sma20: float | None = None
    vol_ratio: float | None = None        # объём бара / средний за 20 (>1 — всплеск)
    ret_1w: float | None = None
    ret_1m: float | None = None
    ret_3m: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    pct_from_52w_high: float | None = None   # % от годового хая (≤0)
    pct_from_52w_low: float | None = None    # % от годового лоя (≥0)
    trend: str | None = None  # up / down / flat

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def _trend(last: float | None, sma50: float | None, sma200: float | None) -> str | None:
    """Грубая оценка тренда по расположению цены относительно средних."""
    if last is None or sma50 is None:
        return None
    if sma200 is not None:
        if last > sma50 > sma200:
            return "up"
        if last < sma50 < sma200:
            return "down"
    return "up" if last > sma50 else "down"


def compute_technical(closes: list[float], highs: list[float] | None = None,
                      lows: list[float] | None = None,
                      volumes: list[float | None] | None = None) -> TechnicalIndicators:
    """Считает полный набор технических индикаторов из ряда закрытий.

    `highs`/`lows`/`volumes` (если переданы и выровнены с `closes`) включают индикаторы
    по OHLC и объёму: ATR, стохастик, OBV, средний объём и всплеск. Без них считаются
    только индикаторы по закрытиям — обратная совместимость с вызовом `compute_technical(closes)`.
    """
    if not closes:
        return TechnicalIndicators()
    hl = high_low(closes[-252:]) if len(closes) >= 1 else None
    macd_t = macd(closes)
    boll = bollinger(closes)
    ind = TechnicalIndicators(
        last=closes[-1],
        sma20=sma(closes, 20),
        sma50=sma(closes, 50),
        sma200=sma(closes, 200),
        rsi14=rsi(closes, 14),
        macd=macd_t[0] if macd_t else None,
        macd_signal=macd_t[1] if macd_t else None,
        macd_hist=macd_t[2] if macd_t else None,
        boll_lower=boll[0] if boll else None,
        boll_mid=boll[1] if boll else None,
        boll_upper=boll[2] if boll else None,
        vol_annual=volatility(closes, 20),
        ret_1w=returns_pct(closes, 5),
        ret_1m=returns_pct(closes, 21),
        ret_3m=returns_pct(closes, 63),
        high_52w=hl[0] if hl else None,
        low_52w=hl[1] if hl else None,
    )
    # A6: дистанция до годового хая/лоя (от тех же close-based экстремумов).
    if hl:
        h52, l52 = hl
        if h52:
            ind.pct_from_52w_high = round((ind.last - h52) / h52 * 100, 2)
        if l52:
            ind.pct_from_52w_low = round((ind.last - l52) / l52 * 100, 2)
    # A3/A4: индикаторы по OHLC.
    if highs and lows and len(highs) == len(closes) == len(lows):
        ind.atr14 = atr(highs, lows, closes, 14)
        st = stochastic(highs, lows, closes, 14, 3)
        if st:
            ind.stoch_k, ind.stoch_d = st
    # A5: индикаторы по объёму.
    if volumes and len(volumes) == len(closes):
        ind.obv = obv(closes, volumes)
        ind.vol_sma20 = volume_sma(volumes, 20)
        ind.vol_ratio = volume_spike(volumes, 20)
    ind.trend = _trend(ind.last, ind.sma50, ind.sma200)
    return ind
