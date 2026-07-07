"""Трек 2 / Пул 8: тесты mark-to-market и метрик трек-рекорда (чистые ядра, без БД)."""

from __future__ import annotations

from dataclasses import dataclass

from geoanalytics.futrader.paper import mark_to_market
from geoanalytics.futrader.track import compute_track_metrics


@dataclass
class _Spec:
    mult: float = 10.0

    def pnl_rub(self, price_delta: float, qty: int) -> float:
        return price_delta * qty * self.mult


@dataclass
class _Pos:
    asset_code: str
    net_qty: int
    avg_price: float | None
    last_price: float | None
    realized_pnl: float = 0.0


class TestMarkToMarket:
    def test_realized_and_unrealized_long(self):
        positions = [_Pos("BR", 2, avg_price=100.0, last_price=103.0, realized_pnl=500.0)]
        realized, unreal = mark_to_market(positions, {"BR": _Spec()})
        assert realized == 500.0
        assert unreal == (103.0 - 100.0) * 2 * 10.0    # +60

    def test_unrealized_short_profits_on_drop(self):
        positions = [_Pos("BR", -1, avg_price=100.0, last_price=95.0)]
        _, unreal = mark_to_market(positions, {"BR": _Spec()})
        assert unreal == (95.0 - 100.0) * -1 * 10.0     # +50 (шорт прибылен на падении)

    def test_flat_position_no_unrealized(self):
        positions = [_Pos("BR", 0, avg_price=None, last_price=100.0, realized_pnl=200.0)]
        realized, unreal = mark_to_market(positions, {"BR": _Spec()})
        assert realized == 200.0 and unreal == 0.0

    def test_missing_spec_skips_unrealized(self):
        positions = [_Pos("XX", 5, avg_price=100.0, last_price=110.0, realized_pnl=10.0)]
        realized, unreal = mark_to_market(positions, {})
        assert realized == 10.0 and unreal == 0.0


class TestComputeTrackMetrics:
    def test_empty_curve(self):
        m = compute_track_metrics([], [], starting_cash=100_000.0)
        assert m.n_points == 0
        assert m.total_return_pct is None
        assert m.n_trades == 0

    def test_return_and_drawdown(self):
        curve = [100_000.0, 110_000.0, 104_500.0]   # +10% затем просадка с пика 110k до 104.5k = 5%
        m = compute_track_metrics(curve, [], starting_cash=100_000.0)
        assert m.total_return_pct == 4.5
        assert m.max_drawdown_pct == 5.0

    def test_trade_stats(self):
        pnls = [300.0, -100.0, 200.0, -50.0]
        m = compute_track_metrics([100_000.0, 100_350.0], pnls, starting_cash=100_000.0)
        assert m.n_trades == 4
        assert m.win_rate == 0.5
        assert m.profit_factor == round(500.0 / 150.0, 3)
        assert m.avg_win == 250.0
        assert m.avg_loss == -75.0

    def test_sharpe_present_on_varied_curve(self):
        m = compute_track_metrics([100_000.0, 101_000.0, 100_500.0, 102_000.0], [],
                                  starting_cash=100_000.0)
        assert m.sharpe is not None
