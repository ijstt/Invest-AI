"""Трек 2 / Pool 2: тесты двусторонних (−1/0/+1) сигналов фьючерсов."""

from __future__ import annotations

from geoanalytics.futrader.decisions import SIGNAL_FNS
from geoanalytics.futrader.signals import (
    DIRECTIONAL_FNS,
    bollinger_directional,
    cross_sectional_signals,
    macd_directional,
    momentum_directional,
    rsi_directional,
    sma_cross_directional,
)


class TestCrossSectional:
    def test_long_top_short_bottom(self):
        # 4 инструмента, lookback=2: A сильнее всех растёт, D падает → A лонг, D шорт
        closes = {
            "A": [100, 100, 100, 130],
            "B": [100, 100, 100, 110],
            "C": [100, 100, 100, 105],
            "D": [100, 100, 100, 80],
        }
        closes = {k: [float(x) for x in v] for k, v in closes.items()}
        sig = cross_sectional_signals(closes, lookback=2, top_frac=0.25)
        assert sig["A"][-1] == 1     # лучший моментум → лонг
        assert sig["D"][-1] == -1    # худший → шорт
        assert sig["B"][-1] == 0 and sig["C"][-1] == 0

    def test_warmup_zero(self):
        closes = {c: [100.0, 101.0, 102.0] for c in ("A", "B", "C")}
        sig = cross_sectional_signals(closes, lookback=20)
        assert all(v == 0 for s in sig.values() for v in s)

    def test_needs_three_instruments(self):
        closes = {"A": [100.0] * 30, "B": [100.0] * 30}
        sig = cross_sectional_signals(closes, lookback=5)
        assert all(v == 0 for s in sig.values() for v in s)   # <3 инструментов → нет ранга


def test_keys_match_long_only():
    assert set(DIRECTIONAL_FNS) == set(SIGNAL_FNS)


def test_outputs_in_minus_one_zero_one():
    closes = [100 + (i % 7) - 3 for i in range(120)]
    for fn in DIRECTIONAL_FNS.values():
        assert set(fn([float(c) for c in closes])) <= {-1, 0, 1}


class TestMomentum:
    def test_rising_long_falling_short(self):
        rising = [float(100 + i) for i in range(60)]
        falling = [float(160 - i) for i in range(60)]
        assert momentum_directional(rising)[-1] == 1
        assert momentum_directional(falling)[-1] == -1

    def test_warmup_flat(self):
        assert momentum_directional([100.0] * 10, lookback=20)[-1] == 0


class TestSmaCross:
    def test_uptrend_long_downtrend_short(self):
        up = [float(100 + i) for i in range(80)]
        down = [float(180 - i) for i in range(80)]
        assert sma_cross_directional(up)[-1] == 1
        assert sma_cross_directional(down)[-1] == -1


class TestMacd:
    def test_two_sided_on_oscillation(self):
        # гистограмма MACD ловит смену моментума → на колебаниях даёт обе стороны (−1 и +1).
        import math

        wave = [100.0 + 10 * math.sin(i / 5.0) for i in range(120)]
        out = macd_directional(wave)
        assert 1 in out and -1 in out
        assert set(out) <= {-1, 0, 1}


class TestRsiDirectional:
    def test_oversold_long_overbought_short(self):
        down = [float(200 - i * 2) for i in range(40)]      # резкое падение → перепроданность
        up = [float(100 + i * 2) for i in range(40)]        # резкий рост → перекупленность
        assert rsi_directional(down)[-1] == 1
        assert rsi_directional(up)[-1] == -1


class TestBollingerDirectional:
    def test_below_lower_long_above_upper_short(self):
        base = [100.0] * 25
        drop = base + [90.0]            # резко ниже нижней полосы
        spike = base + [110.0]          # резко выше верхней полосы
        assert bollinger_directional(drop)[-1] == 1
        assert bollinger_directional(spike)[-1] == -1
