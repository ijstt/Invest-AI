"""Трек 2 / Tier A#1: тесты барьер-осознанного выхода (та же дисциплина, что у метки обучения)."""

from __future__ import annotations

from geoanalytics.futrader.exits import HORIZON_BARS, barrier_exit


class TestBarrierExitLong:
    def test_take_profit_on_upper_touch(self):
        # +1.5·0.02 = +3% → TP при high ≥ 103.
        d = barrier_exit(1, 100.0, 0.02, [101.0, 104.0], [100.5, 103.0])
        assert d.should_exit and d.reason == "take_profit"

    def test_stop_loss_on_lower_touch(self):
        d = barrier_exit(1, 100.0, 0.02, [100.0, 98.0], [99.0, 96.0])
        assert d.should_exit and d.reason == "stop_loss"

    def test_pessimism_both_in_one_bar_takes_stop(self):
        # бар задевает ОБА барьера (low 96 ≤ 97, high 104 ≥ 103) — берём стоп.
        d = barrier_exit(1, 100.0, 0.02, [104.0], [96.0])
        assert d.should_exit and d.reason == "stop_loss"

    def test_hold_when_inside_barriers(self):
        d = barrier_exit(1, 100.0, 0.02, [100.5, 101.0], [99.8, 100.2])
        assert not d.should_exit and d.reason is None


class TestBarrierExitShort:
    def test_take_profit_on_price_fall(self):
        # шорт прибылен при падении: low ≤ 97 = take-profit.
        d = barrier_exit(-1, 100.0, 0.02, [100.0, 99.0], [99.0, 96.5])
        assert d.should_exit and d.reason == "take_profit"

    def test_stop_loss_on_price_rise(self):
        # шорт: рост к верхнему барьеру (high ≥ 103) = stop-loss.
        d = barrier_exit(-1, 100.0, 0.02, [101.0, 103.5], [100.5, 102.0])
        assert d.should_exit and d.reason == "stop_loss"


class TestTimeStop:
    def test_time_stop_at_horizon(self):
        d = barrier_exit(1, 100.0, 0.02, [100.1] * HORIZON_BARS, [99.9] * HORIZON_BARS)
        assert d.should_exit and d.reason == "time_stop"

    def test_no_time_stop_before_horizon(self):
        d = barrier_exit(1, 100.0, 0.02, [100.1] * (HORIZON_BARS - 1),
                         [99.9] * (HORIZON_BARS - 1))
        assert not d.should_exit

    def test_zero_vol_falls_back_to_time_stop_only(self):
        # без σ барьеры цены не определены — только тайм-стоп.
        d = barrier_exit(1, 100.0, 0.0, [200.0] * HORIZON_BARS, [50.0] * HORIZON_BARS)
        assert d.should_exit and d.reason == "time_stop"
        d2 = barrier_exit(1, 100.0, 0.0, [200.0], [50.0])
        assert not d2.should_exit            # нет вола и не дотянули до горизонта → держим


class TestBarIndexHelper:
    def test_finds_entry_bar_by_ts(self):
        from datetime import UTC, datetime

        from geoanalytics.futrader.paper import _bar_index

        class _B:
            def __init__(self, h):
                self.ts = datetime(2026, 6, 26, h, tzinfo=UTC)

        bars = [_B(10), _B(11), _B(12), _B(13)]
        assert _bar_index(bars, datetime(2026, 6, 26, 12, tzinfo=UTC)) == 2
        assert _bar_index(bars, datetime(2026, 6, 26, 9, tzinfo=UTC)) is None   # нет такого бара
        assert _bar_index(bars, None) is None
