"""Трек 2 / Фаза C: тесты сайзинга — vol-targeting, дробный Келли, circuit-breaker."""

from __future__ import annotations

from dataclasses import dataclass

from geoanalytics.futrader.sizing import (
    contract_risk_rub,
    conviction_weight,
    drawdown_breached,
    kelly_fraction,
    margin_budget_qty,
    portfolio_margin_used,
    position_margin,
    position_size,
    risk_scale_for_drawdown,
    vol_target_qty,
)


@dataclass
class _Spec:
    tick_size: float = 0.01
    tick_value: float = 7.34
    initial_margin: float = 12_000.0


SPEC = _Spec()


@dataclass
class _Pos:
    asset_code: str
    net_qty: int


class TestRiskScaleForDrawdown:
    def test_no_drawdown_full_size(self):
        assert risk_scale_for_drawdown(0.0, max_dd_pct=25.0) == 1.0

    def test_half_drawdown_half_size(self):
        assert risk_scale_for_drawdown(12.5, max_dd_pct=25.0) == 0.5

    def test_at_limit_zero(self):
        assert risk_scale_for_drawdown(25.0, max_dd_pct=25.0) == 0.0

    def test_beyond_limit_clamped(self):
        assert risk_scale_for_drawdown(40.0, max_dd_pct=25.0) == 0.0

    def test_disabled_limit_full(self):
        assert risk_scale_for_drawdown(50.0, max_dd_pct=0.0) == 1.0


class TestMargin:
    def test_position_margin_uses_abs_qty(self):
        assert position_margin(SPEC, -3) == 36_000.0

    def test_portfolio_margin_sums_positions(self):
        positions = [_Pos("BR", 2), _Pos("GOLD", -1)]
        specs = {"BR": _Spec(initial_margin=10_000.0), "GOLD": _Spec(initial_margin=5_000.0)}
        assert portfolio_margin_used(positions, specs) == 25_000.0

    def test_portfolio_margin_skips_unknown_spec(self):
        assert portfolio_margin_used([_Pos("XX", 5)], {}) == 0.0


class TestMarginBudgetQty:
    def test_fits_within_budget(self):
        # бюджет 50% от 100k = 50k; маржа 12k/контракт → влезает 4, просим 3 → 3
        assert margin_budget_qty(3, equity=100_000.0, margin_used=0.0, spec=SPEC,
                                 max_gross_margin_pct=50.0) == 3

    def test_shrinks_to_remaining_budget(self):
        # бюджет 50k, уже занято 36k → свободно 14k → влезает лишь 1 (12k), просим 3 → 1
        assert margin_budget_qty(3, equity=100_000.0, margin_used=36_000.0, spec=SPEC,
                                 max_gross_margin_pct=50.0) == 1

    def test_blocks_when_budget_exhausted(self):
        assert margin_budget_qty(3, equity=100_000.0, margin_used=50_000.0, spec=SPEC,
                                 max_gross_margin_pct=50.0) == 0


class TestContractRisk:
    def test_scales_with_vol_and_price(self):
        r1 = contract_risk_rub(80.0, 0.02, SPEC)
        r2 = contract_risk_rub(80.0, 0.04, SPEC)
        assert r2 == 2 * r1                    # вдвое волатильнее → вдвое риск
        assert contract_risk_rub(80.0, 0.0, SPEC) == 0.0


class TestKelly:
    def test_positive_edge_positive_fraction(self):
        assert kelly_fraction(0.6, 1.0) > 0
        assert kelly_fraction(0.5, 1.0) == 0.0
        assert kelly_fraction(0.4, 1.0) < 0    # отрицательный эдж

    def test_conviction_weight_monotone_clamped(self):
        w_lo = conviction_weight(0.56, threshold=0.55)
        w_hi = conviction_weight(0.9, threshold=0.55)
        assert 0 <= w_lo < w_hi <= 1.0
        assert conviction_weight(0.5, threshold=0.55) == 0.0   # ниже порога эджа


class TestVolTargetQty:
    def test_higher_vol_smaller_size(self):
        q_calm = vol_target_qty(100_000, 80.0, 0.01, SPEC, target_risk_pct=2.0, max_qty=50)
        q_wild = vol_target_qty(100_000, 80.0, 0.04, SPEC, target_risk_pct=2.0, max_qty=50)
        assert q_calm > q_wild                 # vol-targeting: волатильнее → меньше контрактов

    def test_capped_by_max_qty(self):
        q = vol_target_qty(10_000_000, 80.0, 0.001, SPEC, target_risk_pct=5.0, max_qty=3)
        assert q == 3


class TestPositionSize:
    def test_below_threshold_zero(self):
        assert position_size(0.50, equity=100_000, price=80.0, vol_fraction=0.02, spec=SPEC,
                             threshold=0.55) == 0

    def test_above_threshold_positive_and_capped(self):
        q = position_size(0.85, equity=100_000, price=80.0, vol_fraction=0.02, spec=SPEC,
                          threshold=0.55, target_risk_pct=2.0, max_qty=5)
        assert 1 <= q <= 5

    def test_higher_conviction_not_smaller(self):
        lo = position_size(0.6, equity=200_000, price=80.0, vol_fraction=0.02, spec=SPEC,
                           threshold=0.55, target_risk_pct=2.0, max_qty=20)
        hi = position_size(0.85, equity=200_000, price=80.0, vol_fraction=0.02, spec=SPEC,
                           threshold=0.55, target_risk_pct=2.0, max_qty=20)
        assert hi >= lo


class TestCircuitBreaker:
    def test_breached_on_deep_drawdown(self):
        assert drawdown_breached([100, 120, 80], limit_pct=25.0) is True   # просадка 33%
        assert drawdown_breached([100, 120, 110], limit_pct=25.0) is False  # просадка ~8%

    def test_disabled_when_limit_zero(self):
        assert drawdown_breached([100, 200, 1], limit_pct=0.0) is False
