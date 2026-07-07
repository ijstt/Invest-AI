"""Трек 2 / T2.2: тесты чистого ядра симулятора исполнения (без БД/сети)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from geoanalytics.futrader.execution import (
    ContractSpec,
    ExecutionSimulator,
    Fill,
    Order,
    fill_price,
    slippage_liquidity_mult,
    slippage_ticks_for_qty,
)


class TestFillHelpers:
    def test_slippage_grows_with_size(self):
        assert slippage_ticks_for_qty(1, base_ticks=1.0, impact_per_contract=0.5) == 1.0
        assert slippage_ticks_for_qty(5, base_ticks=1.0, impact_per_contract=0.5) == 3.0

    def test_liquidity_mult_thin_session_costs_more(self):
        # объём в норме/выше → множитель 1.0; тонкая сессия (vol_z<0) → дороже, с капом
        assert slippage_liquidity_mult(None) == 1.0
        assert slippage_liquidity_mult(0.5) == 1.0
        assert slippage_liquidity_mult(-2.0, k=0.5) == 2.0
        assert slippage_liquidity_mult(-100.0, k=0.5, cap=3.0) == 3.0

    def test_slippage_scaled_by_liquidity(self):
        # тонкая ликвидность удваивает слипидж
        assert slippage_ticks_for_qty(1, base_ticks=1.0, liquidity_mult=2.0) == 2.0

    def test_fill_price_against_trader(self):
        # buy дороже на slip·tick, sell дешевле
        assert fill_price(100.0, "buy", tick_size=0.01, slip_ticks=2.0) == 100.02
        assert fill_price(100.0, "sell", tick_size=0.01, slip_ticks=2.0) == 99.98

# BR-подобная спека: шаг 0.01 пункта = 7.34₽; ГО 12000₽; комиссия 7.5₽/сделку.
SPEC = ContractSpec(secid="BRN6", tick_size=0.01, tick_value=7.34,
                    initial_margin=12_000.0, fee=7.5)
T0 = datetime(2026, 6, 19, 10, 0, tzinfo=UTC)


def _ts(i: int) -> datetime:
    return T0 + timedelta(hours=i)


class TestPnlConversion:
    def test_long_profit_in_rub(self):
        # +1.00 пункта = 100 шагов × 7.34₽ × 1 контракт.
        assert SPEC.pnl_rub(1.00, 1) == 734.0

    def test_short_sign(self):
        # шорт (qty<0) при росте цены — убыток.
        assert SPEC.pnl_rub(1.00, -1) == -734.0

    def test_zero_tick_size_safe(self):
        assert ContractSpec("X", 0.0, 1.0, 1.0).pnl_rub(5.0, 1) == 0.0


class TestLongRoundTrip:
    def test_buy_then_sell_realizes_pnl_minus_fees(self):
        sim = ExecutionSimulator(SPEC, starting_cash=100_000.0, slippage_ticks=0)
        sim.submit(Order("buy", 1), _ts(0), price=70.00)
        sim.submit(Order("sell", 1), _ts(1), price=71.00)
        # +1.00 пункта = 734₽, минус 2×7.5 комиссии.
        assert round(sim.result.realized_pnl, 2) == 734.0
        assert sim.result.fees_paid == 15.0
        assert round(sim.cash, 2) == round(100_000.0 + 734.0 - 15.0, 2)
        assert sim.net_qty == 0

    def test_slippage_works_against_trader(self):
        sim = ExecutionSimulator(SPEC, starting_cash=100_000.0, slippage_ticks=2)
        f = sim.submit(Order("buy", 1), _ts(0), price=70.00)
        assert isinstance(f, Fill)
        # покупка проскальзывает ВВЕРХ на 2 шага (0.02).
        assert round(f.price, 4) == 70.02
        s = sim.submit(Order("sell", 1), _ts(1), price=70.00)
        assert round(s.price, 4) == 69.98  # продажа — ВНИЗ


class TestShortAndFlip:
    def test_short_profits_when_price_falls(self):
        sim = ExecutionSimulator(SPEC, starting_cash=100_000.0, slippage_ticks=0)
        sim.submit(Order("sell", 1), _ts(0), price=70.00)
        assert sim.net_qty == -1
        sim.submit(Order("buy", 1), _ts(1), price=69.00)
        # шорт закрыт ниже на 1.00 → прибыль 734₽.
        assert round(sim.result.realized_pnl, 2) == 734.0

    def test_flip_long_to_short_realizes_only_closed_part(self):
        sim = ExecutionSimulator(SPEC, starting_cash=200_000.0, slippage_ticks=0)
        sim.submit(Order("buy", 2), _ts(0), price=70.00)
        # продаём 3: закрываем 2 лонга (+1.00 каждый), переворачиваемся в −1.
        sim.submit(Order("sell", 3), _ts(1), price=71.00)
        assert sim.net_qty == -1
        assert round(sim.avg_price, 2) == 71.00          # новая короткая по цене сделки
        assert round(sim.result.realized_pnl, 2) == 2 * 734.0

    def test_short_rejected_when_disallowed(self):
        sim = ExecutionSimulator(SPEC, starting_cash=100_000.0, allow_short=False)
        f = sim.submit(Order("sell", 1), _ts(0), price=70.00)
        assert f is None
        assert sim.net_qty == 0
        assert sim.result.rejected == 1


class TestMarginGate:
    def test_rejects_when_margin_exceeds_equity(self):
        # эквити 20k, ГО 12k/контракт → 1 можно, 2 уже нельзя.
        sim = ExecutionSimulator(SPEC, starting_cash=20_000.0, slippage_ticks=0)
        assert sim.submit(Order("buy", 1), _ts(0), price=70.00) is not None
        assert sim.submit(Order("buy", 1), _ts(1), price=70.00) is None
        assert sim.net_qty == 1
        assert sim.result.rejected == 1

    def test_leverage_capped_by_initial_margin(self):
        sim = ExecutionSimulator(SPEC, starting_cash=50_000.0, slippage_ticks=0)
        # 50k / 12k = 4 контракта максимум разом.
        f = sim.submit(Order("buy", 4), _ts(0), price=70.00)
        assert f is not None and sim.net_qty == 4
        assert sim.submit(Order("buy", 1), _ts(1), price=70.00) is None


class TestMarkAndLiquidation:
    def test_mark_tracks_drawdown(self):
        sim = ExecutionSimulator(SPEC, starting_cash=100_000.0, slippage_ticks=0)
        sim.submit(Order("buy", 1), _ts(0), price=70.00)
        sim.mark(_ts(1), 71.00)   # +734
        sim.mark(_ts(2), 69.00)   # −734 от входа
        assert sim.result.max_drawdown_rub > 0

    def test_liquidation_when_equity_below_margin(self):
        # маленькое эквити под одну позицию: резкое падение пробивает ГО → ликвидация.
        sim = ExecutionSimulator(SPEC, starting_cash=13_000.0, slippage_ticks=0)
        sim.submit(Order("buy", 1), _ts(0), price=70.00)
        # падение на 5 пунктов = −3670₽ → эквити ~9330 < ГО 12000.
        sim.mark(_ts(1), 65.00)
        assert sim.result.liquidated is True
        assert sim.net_qty == 0


class TestRunStrategy:
    def test_buy_and_hold_run(self):
        bars = [_FakeBar(_ts(i), 70.0 + i) for i in range(5)]
        sim = ExecutionSimulator(SPEC, starting_cash=100_000.0, slippage_ticks=0)
        state = {"open": False}

        def strat(_bar, _sim):
            if state["open"]:
                return None
            state["open"] = True
            return Order("buy", 1)

        res = sim.run(bars, strategy=strat)
        assert res.n_trades == 1
        assert len(res.equity_curve) == 5
        # цена выросла 70→74 (+4.00 = 2936₽), минус комиссия 7.5.
        assert round(res.final_equity, 2) == round(100_000.0 + 4 * 734.0 - 7.5, 2)
        assert res.return_pct > 0


class TestLimitOrders:
    def test_limit_buy_fills_only_when_crossed(self):
        sim = ExecutionSimulator(SPEC, starting_cash=100_000.0, slippage_ticks=0)
        # лимит 69.5: бар не дошёл (low 70.0) → не исполнен.
        f = sim.submit(Order("buy", 1, kind="limit", limit_price=69.5),
                       _ts(0), price=70.5, bar_low=70.0, bar_high=71.0)
        assert f is None
        # бар коснулся 69.0 → исполнен по лимиту 69.5 без проскальзывания.
        f = sim.submit(Order("buy", 1, kind="limit", limit_price=69.5),
                       _ts(1), price=70.5, bar_low=69.0, bar_high=71.0)
        assert f is not None
        assert f.price == 69.5
        assert f.slippage_ticks == 0.0


class _FakeBar:
    def __init__(self, ts, close):
        self.ts = ts
        self.open = close
        self.high = close
        self.low = close
        self.close = close
