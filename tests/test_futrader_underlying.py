"""Трек 2 / Объективный вход (A0): тесты резолвера фьючерс→базовый и чистых ядер."""

from __future__ import annotations

from geoanalytics.futrader.underlying import (
    UNDERLYING_MAP,
    _regime_contribution,
    _trend_contribution,
    resolve_underlying,
)


class TestResolve:
    def test_known_codes(self):
        assert resolve_underlying("BR") == ("factor", "brent")
        assert resolve_underlying("GOLD") == ("factor", "gold")
        assert resolve_underlying("Si") == ("fx", "USD")
        assert resolve_underlying("RTS") == ("index", "IMOEX")

    def test_unknown_code(self):
        assert resolve_underlying("ZZZ") is None

    def test_map_covers_all_futures_codes(self):
        # каждый код в карте — валидный вид базового
        for kind, _key in UNDERLYING_MAP.values():
            assert kind in ("factor", "fx", "index")


class TestTrendContribution:
    def test_uptrend_positive(self):
        values = [float(x) for x in range(100, 140)]      # стабильный рост
        c = _trend_contribution(values, lookback=20)
        assert c is not None and c > 0

    def test_downtrend_negative(self):
        values = [float(x) for x in range(140, 100, -1)]   # стабильное падение
        c = _trend_contribution(values, lookback=20)
        assert c is not None and c < 0

    def test_flat_near_zero(self):
        values = [100.0] * 40
        c = _trend_contribution(values, lookback=20)
        assert c is not None and abs(c) < 0.05

    def test_too_short_none(self):
        assert _trend_contribution([100.0] * 5, lookback=20) is None

    def test_clamped(self):
        values = [1.0] * 21 + [100.0]      # резкий скачок → вклад зажат в [−1,1]
        c = _trend_contribution(values, lookback=20)
        assert c is not None and -1.0 <= c <= 1.0


class TestRegimeContribution:
    def test_calm_bullish(self):
        assert _regime_contribution("спокойный") > 0

    def test_crisis_bearish(self):
        assert _regime_contribution("кризис") < 0

    def test_unknown_neutral(self):
        assert _regime_contribution("прочее") == 0.0
