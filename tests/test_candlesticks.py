"""Тесты свечных паттернов Нисона: геометрия, контекст тренда, торговый сигнал."""

from __future__ import annotations

from geoanalytics.analytics.candlesticks import (
    bearish_engulfing,
    bullish_engulfing,
    bullish_harami,
    candle_signals,
    candles_directional,
    detect_patterns,
    doji,
    evening_star,
    hammer_shape,
    morning_star,
    shooting_star_shape,
    sma_series,
    trend_context,
)


def _bars(*ohlc: tuple[float, float, float, float]):
    """Кортежи (o, h, l, c) → четыре списка."""
    opens = [b[0] for b in ohlc]
    highs = [b[1] for b in ohlc]
    lows = [b[2] for b in ohlc]
    closes = [b[3] for b in ohlc]
    return opens, highs, lows, closes


class TestShapes:
    def test_doji(self):
        o, h, lo, c = _bars((100, 105, 95, 100.4), (100, 105, 95, 103))
        assert doji(o, h, lo, c) == [True, False]

    def test_hammer_shape(self):
        # Длинная нижняя тень (тело у вершины), малая верхняя.
        o, h, lo, c = _bars((100, 101, 90, 100.5),   # молот
                           (100, 110, 99, 101))      # не молот: верхняя тень длинная
        assert hammer_shape(o, h, lo, c) == [True, False]

    def test_shooting_star_shape(self):
        o, h, lo, c = _bars((100, 110, 99.5, 100.5))
        assert shooting_star_shape(o, h, lo, c) == [True]

    def test_bullish_engulfing(self):
        # Чёрная 102→100, затем белая 99.5→103: тело накрывает тело.
        o, h, lo, c = _bars((102, 102.5, 99.8, 100), (99.5, 103.5, 99, 103))
        assert bullish_engulfing(o, h, lo, c) == [False, True]
        # Не накрывает (закрытие ниже открытия первой) — не паттерн.
        o, h, lo, c = _bars((102, 102.5, 99.8, 100), (99.5, 102, 99, 101.5))
        assert bullish_engulfing(o, h, lo, c) == [False, False]

    def test_bearish_engulfing(self):
        o, h, lo, c = _bars((100, 102.2, 99.8, 102), (102.5, 103, 99, 99.5))
        assert bearish_engulfing(o, h, lo, c) == [False, True]

    def test_bullish_harami(self):
        # Длинная чёрная 110→100, затем малое тело внутри (103→104).
        o, h, lo, c = _bars((110, 110.5, 99.5, 100), (103, 104.5, 102.5, 104))
        assert bullish_harami(o, h, lo, c) == [False, True]

    def test_morning_star(self):
        # Длинная чёрная 110→100 → звезда (100.5→100.2) → белая до 106 (> середины 105).
        o, h, lo, c = _bars((110, 110.5, 99.5, 100),
                           (100.5, 101, 99.8, 100.2),
                           (100.5, 106.5, 100.3, 106))
        assert morning_star(o, h, lo, c) == [False, False, True]

    def test_evening_star(self):
        o, h, lo, c = _bars((100, 110.5, 99.5, 110),
                           (110.2, 110.8, 109.7, 110.4),
                           (110, 110.2, 103.5, 104))
        assert evening_star(o, h, lo, c) == [False, False, True]


class TestTrendContext:
    def test_sma_series_warmup_and_values(self):
        assert sma_series([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]

    def test_trend_by_prev_bar_vs_sma(self):
        closes = [10, 9, 8, 7, 6, 5]          # падение: бар ниже своей SMA
        ctx = trend_context(closes, window=3)
        assert ctx[-1] == -1
        closes = [5, 6, 7, 8, 9, 10]
        assert trend_context(closes, window=3)[-1] == 1


class TestDetectPatterns:
    def _downtrend_hammer(self):
        """Нисходящий ряд, завершающийся молотом."""
        bars = [(100 - i, 100.6 - i, 99.2 - i, 99.5 - i) for i in range(8)]
        bars.append((92.0, 92.3, 88.0, 92.1))   # молот: нижняя тень ~4, тело 0.1
        return _bars(*bars)

    def test_hammer_needs_downtrend(self):
        o, h, lo, c = self._downtrend_hammer()
        names = {hit.name for hit in detect_patterns(o, h, lo, c, trend=5)}
        assert "молот" in names
        assert "повешенный" not in names      # та же геометрия, но тренд нисходящий

    def test_hanging_man_in_uptrend(self):
        bars = [(100 + i, 100.8 + i, 99.6 + i, 100.5 + i) for i in range(8)]
        bars.append((108.4, 108.7, 104.0, 108.5))
        o, h, lo, c = _bars(*bars)
        names = {hit.name for hit in detect_patterns(o, h, lo, c, trend=5)}
        assert "повешенный" in names and "молот" not in names

    def test_hits_sorted_by_index(self):
        o, h, lo, c = self._downtrend_hammer()
        hits = detect_patterns(o, h, lo, c, trend=5)
        assert [x.index for x in hits] == sorted(x.index for x in hits)


class TestCandleSignals:
    def test_entry_on_bull_exit_by_hold(self):
        o, h, lo, c = TestDetectPatterns()._downtrend_hammer()
        # Хвост без паттернов: позиция должна закрыться по таймеру hold=2.
        for _ in range(4):
            o.append(o[-1])
            h.append(h[-1] + 0.3)
            lo.append(lo[-1])
            c.append(c[-1])
        sig = candle_signals(c, opens=o, highs=h, lows=lo, hold=2, trend=5)
        assert sig[8] == 1                     # вход на баре молота
        assert sig[9] == 1 and sig[10] == 1    # удержание
        assert sig[11] == 0                    # выход по hold
        assert all(s == 0 for s in sig[:8])

    def test_no_signal_without_trend_context(self):
        # Молот без нисходящего тренда (флэт) сигнала не даёт.
        bars = [(100, 100.4, 99.7, 100.1), (100.1, 100.5, 99.8, 100.0)] * 5
        bars.append((100.0, 100.2, 96.0, 100.1))
        o, h, lo, c = _bars(*bars)
        sig = candle_signals(c, opens=o, highs=h, lows=lo, hold=5, trend=5)
        assert sig[-1] in (0, 1)               # не падает; вход только при тренде
        ctx = trend_context(c, 5)
        if ctx[-1] != -1:
            assert sig[-1] == 0


class TestWalkForwardIntegration:
    def test_candles_strategy_in_walk_forward(self):
        """Стратегия candles ходит через walk_forward как callable с OHLC."""
        import random

        from geoanalytics.analytics.backtest import (
            DEFAULT_GRIDS,
            _ohlc_strategy_fn,
            walk_forward,
        )

        rnd = random.Random(7)
        closes, price = [], 100.0
        opens, highs, lows = [], [], []
        for _ in range(300):
            o = price
            price = max(1.0, price * (1 + rnd.gauss(0, 0.02)))
            hi = max(o, price) * (1 + abs(rnd.gauss(0, 0.005)))
            lo = min(o, price) * (1 - abs(rnd.gauss(0, 0.005)))
            opens.append(o)
            highs.append(hi)
            lows.append(lo)
            closes.append(price)
        fn = _ohlc_strategy_fn("candles", opens, highs, lows)
        res = walk_forward(closes, fn, DEFAULT_GRIDS["candles"],
                           train=120, test=40)
        assert res.strategy == "candles"
        assert len(res.folds) >= 3             # механика фолдов отработала


class TestCandlesDirectional:
    def test_long_on_bullish_reversal_then_hold_expiry(self):
        # Тот же сетап, что у candle_signals: нисходящий тренд → молот на баре 8 → ЛОНГ, держим,
        # выход по таймеру hold (хвост без паттернов).
        o, h, lo, c = TestDetectPatterns()._downtrend_hammer()
        for _ in range(4):
            o.append(o[-1])
            h.append(h[-1] + 0.3)
            lo.append(lo[-1])
            c.append(c[-1])
        sig = candles_directional(c, opens=o, highs=h, lows=lo, hold=2, trend=5)
        assert sig[8] == 1                     # лонг на баре молота
        assert sig[9] == 1 and sig[10] == 1    # удержание
        assert sig[11] == 0                    # выход по hold
        assert all(s == 0 for s in sig[:8])
        assert set(sig) <= {-1, 0, 1}

    def test_degenerate_ohlc_all_flat(self):
        # OHLC по умолчанию = closes (вырожденные бары) → нет паттернов, все 0; не падает.
        c = [100.0 + (i % 5) for i in range(60)]
        assert candles_directional(c) == [0] * 60
