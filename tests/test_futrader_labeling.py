"""Трек 2 / Фаза A: тесты triple-barrier лейблинга (чистое ядро) и побарной σ."""

from __future__ import annotations

from geoanalytics.futrader.labeling import bar_return_std, triple_barrier


def _flat(prices):
    """highs=lows=closes=prices (для барьеров по close-пути)."""
    return prices, prices, prices


class TestBarReturnStd:
    def test_warmup_none(self):
        assert bar_return_std([100.0] * 5, 3, window=20) is None

    def test_positive_for_volatile_series(self):
        closes = [100, 102, 99, 103, 98, 104, 97, 105, 96, 106] * 3
        v = bar_return_std([float(x) for x in closes], 25, window=20)
        assert v is not None and v > 0


class TestTripleBarrier:
    def test_long_take_profit_first_is_win(self):
        # рост к верхнему барьеру (+1.5·0.02=3%) раньше нижнего.
        closes = [100, 101, 104, 100]
        h, lo, c = _flat([float(x) for x in closes])
        out = triple_barrier(h, lo, c, 0, 1, horizon=3, up_mult=1.5, down_mult=1.5, vol=0.02)
        assert out.label == "win"
        assert out.barrier == "up"
        assert out.touch_idx == 2

    def test_long_stop_loss_first_is_loss(self):
        closes = [100, 99, 96, 101]      # падение к нижнему барьеру (−3%)
        h, lo, c = _flat([float(x) for x in closes])
        out = triple_barrier(h, lo, c, 0, 1, horizon=3, up_mult=1.5, down_mult=1.5, vol=0.02)
        assert out.label == "loss"
        assert out.barrier == "down"

    def test_short_inverts_up_barrier_to_loss(self):
        # цена выросла (up) — для шорта это проигрыш.
        closes = [100, 101, 104, 100]
        h, lo, c = _flat([float(x) for x in closes])
        out = triple_barrier(h, lo, c, 0, -1, horizon=3, up_mult=1.5, down_mult=1.5, vol=0.02)
        assert out.label == "loss"
        assert out.barrier == "up"

    def test_vertical_barrier_uses_return_sign(self):
        # ни один барьер не задет (ход < 3%); итог по знаку доходности в сторону ставки.
        closes = [100, 100.5, 101.0, 101.5]
        h, lo, c = _flat([float(x) for x in closes])
        out = triple_barrier(h, lo, c, 0, 1, horizon=3, up_mult=5, down_mult=5, vol=0.02)
        assert out.barrier == "vertical"
        assert out.label == "win"        # +1.5% в сторону лонга

    def test_vertical_short_loss_on_price_rise(self):
        closes = [100, 100.5, 101.0, 101.5]
        h, lo, c = _flat([float(x) for x in closes])
        out = triple_barrier(h, lo, c, 0, -1, horizon=3, up_mult=5, down_mult=5, vol=0.02)
        assert out.barrier == "vertical"
        assert out.label == "loss"       # рост против шорта

    def test_flat_within_eps(self):
        closes = [100.0, 100.0, 100.0, 100.0]
        h, lo, c = _flat(closes)
        out = triple_barrier(h, lo, c, 0, 1, horizon=3, up_mult=5, down_mult=5, vol=0.02,
                             flat_eps=0.001)
        assert out.label == "flat"

    def test_pessimism_both_barriers_same_bar_long_takes_stop(self):
        # бар с широким диапазоном задевает ОБА барьера; для лонга берём неблагоприятный (стоп).
        highs = [100.0, 104.0]
        lows = [100.0, 96.0]
        closes = [100.0, 100.0]
        out = triple_barrier(highs, lows, closes, 0, 1, horizon=1, up_mult=1.5, down_mult=1.5,
                             vol=0.02)
        assert out.label == "loss"
        assert out.barrier == "down"

    def test_end_idx_caps_vertical_before_horizon(self):
        # Сессионная дисциплина: без касания барьеров end_idx обрезает вертикаль РАНЬШЕ горизонта
        # (форсированный флэт до закрытия сессии) — исход берётся на end_idx, не на i+horizon.
        c = [100.0, 100.5, 101.0, 110.0]              # рост к бару 3, но сессия закрылась на 1
        h = [x * 1.001 for x in c]
        lo = [x * 0.999 for x in c]
        out = triple_barrier(h, lo, c, 0, 1, horizon=3, up_mult=9, down_mult=9, vol=0.02, end_idx=1)
        assert out.barrier == "vertical"
        assert out.touch_idx == 1                      # вертикаль на end_idx, не на горизонте 3
