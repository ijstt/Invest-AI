"""Тесты технических индикаторов (чистая математика, без БД)."""

from __future__ import annotations

from geoanalytics.analytics.indicators import (
    atr,
    bollinger,
    compute_technical,
    ema,
    high_low,
    macd,
    obv,
    returns_pct,
    rsi,
    sma,
    stochastic,
    volatility,
    volume_sma,
    volume_spike,
)


def test_sma_basic():
    assert sma([1, 2, 3, 4, 5], 5) == 3.0
    assert sma([1, 2, 3, 4, 5], 2) == 4.5


def test_sma_insufficient():
    assert sma([1, 2], 5) is None


def test_ema_runs():
    val = ema([1, 2, 3, 4, 5, 6, 7, 8], 3)
    assert val is not None and val > 0


def test_rsi_all_gains_is_100():
    # Монотонный рост → RSI = 100.
    assert rsi(list(range(1, 20)), 14) == 100.0


def test_rsi_range():
    prices = [44, 44.3, 44.1, 44.5, 43.9, 44.6, 44.8, 45.1, 45.0, 45.3,
              45.6, 45.4, 45.8, 46.0, 46.2]
    val = rsi(prices, 14)
    assert val is not None and 0 <= val <= 100


def test_returns_pct():
    # 5 баров назад: с 100 до 110 → +10%
    assert returns_pct([100, 102, 104, 106, 108, 110], 5) == 10.0


def test_returns_insufficient():
    assert returns_pct([100, 110], 5) is None


def test_volatility_positive():
    prices = [100 + (i % 3) for i in range(40)]
    val = volatility(prices, 20)
    assert val is not None and val >= 0


def test_high_low():
    assert high_low([3, 1, 4, 1, 5, 9, 2]) == (9, 1)


def test_macd_uptrend_positive_hist():
    # Ускоряющийся рост → MACD-линия растёт быстрее сигнальной, гистограмма положительна.
    # (На строго линейном ряду MACD выходит на стационар и hist→0, поэтому берём экспоненту.)
    closes = [100 * 1.02 ** i for i in range(60)]
    res = macd(closes)
    assert res is not None
    macd_val, signal_val, hist = res
    assert macd_val > signal_val
    assert hist > 0
    assert abs(hist - (macd_val - signal_val)) < 1e-9


def test_macd_insufficient():
    # Меньше slow + signal баров → None.
    assert macd([float(i) for i in range(30)]) is None


def test_bollinger_band_order():
    prices = [100, 102, 98, 101, 99, 103, 97, 100, 102, 98,
              101, 99, 103, 97, 100, 102, 98, 101, 99, 103]
    res = bollinger(prices, window=20, k=2.0)
    assert res is not None
    lower, mid, upper = res
    assert lower < mid < upper
    assert abs((upper - mid) - (mid - lower)) < 1e-9  # полосы симметричны вокруг средней


def test_bollinger_insufficient():
    assert bollinger([1, 2, 3], window=20) is None


def test_compute_technical_fills_macd_bollinger():
    # На достаточном ряду новые поля заполнены и согласованы.
    closes = [100 + (i % 5) for i in range(60)]
    ind = compute_technical(closes)
    assert ind.macd is not None and ind.macd_signal is not None
    assert ind.macd_hist is not None
    assert ind.boll_lower is not None and ind.boll_upper is not None
    assert ind.boll_lower < ind.boll_mid < ind.boll_upper


def test_atr_basic():
    # TR на каждом баре = high−low = 2 (гэпов нет) → ATR = 2.
    highs = [float(i + 2) for i in range(20)]
    lows = [float(i) for i in range(20)]
    closes = [float(i + 1) for i in range(20)]
    assert atr(highs, lows, closes, 14) == 2.0


def test_atr_insufficient_or_mismatched():
    assert atr([1, 2], [0, 1], [1, 2], 14) is None        # данных < window+1
    assert atr([1, 2, 3], [0, 1], [1, 2, 3], 1) is None   # длины не совпали


def test_stochastic_at_top_and_bottom():
    # Закрытие на максимуме окна → %K = 100.
    highs = [10.0] * 20
    lows = [0.0] * 20
    closes = [5.0] * 19 + [10.0]
    st = stochastic(highs, lows, closes, 14, 3)
    assert st is not None
    k, d = st
    assert k == 100.0 and 0 <= d <= 100


def test_stochastic_flat_range_is_none():
    # Нулевой диапазон (hh==ll) → %K не определён → None.
    assert stochastic([5.0] * 20, [5.0] * 20, [5.0] * 20, 14, 3) is None


def test_obv_direction():
    # Рост→+v, падение→−v, флэт→0. closes=[10,11,10,10], v=[_,5,3,7] → +5−3+0 = 2.
    assert obv([10, 11, 10, 10], [None, 5, 3, 7]) == 2.0
    assert obv([10], [5]) is None                          # < 2 баров
    assert obv([10, 11], [None, None]) is None             # объём отсутствует


def test_volume_sma_and_spike():
    vols = [10.0] * 19 + [20.0]
    assert volume_sma(vols, 20) == 10.5                    # (19·10+20)/20
    assert volume_spike(vols, 20) == round(20.0 / 10.5, 2)  # последний / средний
    assert volume_sma([10.0] * 5, 20) is None              # данных мало
    assert volume_sma([None] + [10.0] * 19, 20) is None    # пропуск в окне


def test_compute_technical_ohlcv_fields():
    n = 60
    closes = [100 + (i % 5) for i in range(n)]
    highs = [c + 2 for c in closes]
    lows = [c - 2 for c in closes]
    volumes = [1000.0 + i for i in range(n)]
    ind = compute_technical(closes, highs=highs, lows=lows, volumes=volumes)
    assert ind.atr14 is not None
    assert ind.stoch_k is not None and ind.stoch_d is not None
    assert ind.obv is not None
    assert ind.vol_sma20 is not None and ind.vol_ratio is not None
    assert ind.pct_from_52w_high is not None and ind.pct_from_52w_low is not None


def test_compute_technical_closes_only_skips_ohlcv():
    # Без OHLC/volume — поля ATR/стохастик/объём остаются None (обратная совместимость).
    ind = compute_technical([100 + i for i in range(60)])
    assert ind.atr14 is None and ind.stoch_k is None and ind.obv is None
    assert ind.pct_from_52w_high is not None   # дистанция считается по закрытиям


def test_compute_technical_trend_up():
    # Растущий ряд из 250 точек → тренд up, last определён.
    closes = [100 + i for i in range(250)]
    ind = compute_technical(closes)
    assert ind.last == closes[-1]
    assert ind.trend == "up"
    assert ind.sma50 is not None
    assert ind.high_52w == closes[-1]


def test_compute_technical_empty():
    ind = compute_technical([])
    assert ind.last is None
    assert ind.as_dict() == {}


def test_ewma_volatility_constant_series_zero():
    from geoanalytics.analytics.indicators import ewma_volatility

    assert ewma_volatility([100.0] * 60) == 0.0


def test_ewma_volatility_insufficient_history_none():
    from geoanalytics.analytics.indicators import ewma_volatility

    assert ewma_volatility([100.0, 101.0] * 10) is None  # 19 доходностей < 30


def test_ewma_volatility_reacts_to_recent_regime():
    """EWMA даёт больший вес свежим дням: тихая история + бурный хвост → σ выше,
    чем у равномерно тихого ряда."""
    from geoanalytics.analytics.indicators import ewma_volatility

    calm = [100 * (1.001 ** i) for i in range(80)]
    stormy = list(calm[:70])
    for i in range(10):
        stormy.append(stormy[-1] * (1.05 if i % 2 == 0 else 0.95))
    assert ewma_volatility(stormy) > ewma_volatility(calm) * 5
