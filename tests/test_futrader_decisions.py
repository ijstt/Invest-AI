"""Трек 2 / T2.3: тесты чистых ядер лога решений (без БД/сети)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from geoanalytics.futrader.decisions import (
    Decision,
    decisions_from_signals,
    enrich_features,
    extract_features,
    label_decisions,
)
from geoanalytics.futrader.execution import ContractSpec

SPEC = ContractSpec(secid="BRN6", tick_size=0.01, tick_value=7.34, initial_margin=12_000.0)
T0 = datetime(2026, 6, 19, 10, 0, tzinfo=UTC)


def _ts(i: int) -> datetime:
    return T0 + timedelta(hours=i)


class _Bar:
    def __init__(self, ts, close, high=None, low=None, secid="BRN6"):
        self.ts = ts
        self.close = close
        self.high = high if high is not None else close
        self.low = low if low is not None else close
        self.contract_secid = secid


class TestExtractFeatures:
    def test_warmup_skips_unavailable(self):
        closes = [100.0, 101.0, 102.0]
        feats = extract_features(closes, closes, closes, 2)
        # ret_1 доступен, но rsi_14/vol_20/sma20 — нет (мало истории).
        assert "ret_1" in feats
        assert "rsi_14" not in feats
        assert "vol_20" not in feats

    def test_computes_core_features_on_long_series(self):
        closes = [100.0 + i for i in range(60)]   # ровный рост
        feats = extract_features(closes, closes, closes, 59)
        assert "ret_1" in feats and "ret_5" in feats and "ret_20" in feats
        assert "rsi_14" in feats and feats["rsi_14"] == 100.0   # только рост → RSI 100
        assert feats["ret_1"] > 0
        assert "sma_gap_20" in feats and feats["sma_gap_20"] > 0   # цена выше SMA20
        assert "range_pos" in feats and feats["range_pos"] == 1.0  # на максимуме диапазона

    def test_vol_z_on_volume_spike(self):
        closes = [100.0 + i * 0.1 for i in range(30)]
        volumes = [100.0 + (i % 7) * 3 for i in range(29)] + [500.0]   # всплеск объёма на последнем
        feats = extract_features(closes, closes, closes, 29, volumes=volumes)
        assert "vol_z" in feats and feats["vol_z"] > 2.0               # большой положительный z

    def test_no_vol_z_without_volumes(self):
        closes = [100.0 + i for i in range(30)]
        assert "vol_z" not in extract_features(closes, closes, closes, 29)

    def test_no_candles_without_opens(self):
        closes = [100.0 + i for i in range(30)]
        feats = extract_features(closes, closes, closes, 29)
        assert "cdl_wick" not in feats and "cdl_engulf" not in feats

    def test_bullish_engulfing(self):
        # бар i−1 медвежий (o>c), бар i бычий (c>o) и тело покрывает предыдущее
        opens = [10.0, 10.0, 10.6]
        closes = [10.0, 9.4, 10.0]      # i−1: 10.0→9.4 (медв.), i: 10.6→10.0 ... нет, нужно c>o
        # подберём явно: i−1 медвежья (o=10.0,c=9.4); i бычья (o=9.3,c=10.1) — поглощает
        opens = [10.0, 10.0, 9.3]
        closes = [10.0, 9.4, 10.1]
        highs = [10.1, 10.1, 10.2]
        lows = [9.9, 9.3, 9.2]
        feats = extract_features(closes, highs, lows, 2, opens=opens)
        assert feats["cdl_engulf"] == 1.0

    def test_bearish_engulfing(self):
        opens = [10.0, 9.4, 10.1]
        closes = [10.0, 10.0, 9.3]      # i−1 бычья (9.4→10.0), i медв. (10.1→9.3) поглощает
        highs = [10.1, 10.1, 10.2]
        lows = [9.9, 9.3, 9.2]
        feats = extract_features(closes, highs, lows, 2, opens=opens)
        assert feats["cdl_engulf"] == -1.0

    def test_hammer_wick_positive(self):
        # молот: длинный нижний хвост, тело наверху → cdl_wick > 0
        opens = [10.0, 10.0]
        closes = [10.0, 9.95]
        highs = [10.0, 10.0]
        lows = [9.9, 9.0]               # глубокий нижний хвост на баре 1
        feats = extract_features(closes, highs, lows, 1, opens=opens)
        assert feats["cdl_wick"] > 0.0


class _StubEdge:
    def features_at(self, ts, *, intraday=True):
        return {"regime_state": 1.0, "sent_ewma": -0.2}

    def asset_features_at(self, ts, asset_code, *, intraday=True):
        return {"asset_sent_ewma": 0.3} if asset_code == "BR" else {}


class TestEnrichFeatures:
    def test_adds_full_context(self):
        ts = _ts(13)
        feats = enrich_features({"ret_1": 0.5}, ts, "BR", edge=_StubEdge(), term_map={ts: 0.8})
        assert feats["ret_1"] == 0.5
        assert feats["regime_state"] == 1.0       # из эджа
        assert feats["instr"] == 0.0              # код BR
        assert feats["term_slope"] == 0.8         # контанго
        assert feats["hour"] == float(ts.hour)

    def test_term_slope_absent_when_no_map_entry(self):
        ts = _ts(2)
        feats = enrich_features({}, ts, "GOLD", edge=_StubEdge(), term_map={})
        assert "term_slope" not in feats
        assert feats["instr"] == 1.0              # код GOLD
        assert "hour" in feats


class TestDecisionsFromSignals:
    def test_logs_at_signal_changes_only(self):
        bars = [_Bar(_ts(i), 100.0 + i) for i in range(10)]
        signals = [0, 0, 1, 1, 1, 0, 0, 1, 1, 0]
        decs = decisions_from_signals(bars, signals)
        # переходы: 0→1@2(buy), 1→0@5(close), 0→1@7(buy), 1→0@9(close).
        assert [(d.action, d.signed_qty) for d in decs] == [
            ("buy", 1), ("close", -1), ("buy", 1), ("close", -1)]
        assert [d.ts for d in decs] == [_ts(2), _ts(5), _ts(7), _ts(9)]

    def test_qty_scales_signed(self):
        bars = [_Bar(_ts(i), 100.0) for i in range(4)]
        decs = decisions_from_signals(bars, [0, 1, 1, 0], qty=3)
        assert decs[0].signed_qty == 3
        assert decs[1].signed_qty == -3

    def test_no_change_no_decisions(self):
        bars = [_Bar(_ts(i), 100.0) for i in range(5)]
        assert decisions_from_signals(bars, [0, 0, 0, 0, 0]) == []

    def test_starting_long_logs_entry_at_first_bar(self):
        # стартуем из позиции вне рынка (prev=0): сигнал=1 с бара 0 → один вход.
        bars = [_Bar(_ts(i), 100.0) for i in range(5)]
        decs = decisions_from_signals(bars, [1, 1, 1, 1, 1])
        assert [(d.action, d.ts) for d in decs] == [("buy", _ts(0))]


class TestDirectionalDecisions:
    def test_logs_long_short_entries_and_flips(self):
        bars = [_Bar(_ts(i), 100.0 + i) for i in range(8)]
        # 0→1@2 лонг-вход; 1→−1@4 разворот в шорт; −1→0@6 выход (не логируется).
        signals = [0, 0, 1, 1, -1, -1, 0, 0]
        decs = decisions_from_signals(bars, signals, qty=2, directional=True)
        assert [(d.action, d.signed_qty, d.ts) for d in decs] == [
            ("buy", 2, _ts(2)), ("sell", -2, _ts(4))]

    def test_exit_to_flat_not_logged(self):
        bars = [_Bar(_ts(i), 100.0) for i in range(5)]
        # только вход в шорт @1 логируется; −1→0@3 — выход, не ставка.
        decs = decisions_from_signals(bars, [0, -1, -1, 0, 0], directional=True)
        assert [(d.action, d.signed_qty) for d in decs] == [("sell", -1)]

    def test_short_first_bar(self):
        bars = [_Bar(_ts(i), 100.0) for i in range(4)]
        decs = decisions_from_signals(bars, [-1, -1, -1, -1], directional=True)
        assert [(d.action, d.signed_qty, d.ts) for d in decs] == [("sell", -1, _ts(0))]


class TestLabelDecisions:
    def _bars(self, closes):
        return [_Bar(_ts(i), c) for i, c in enumerate(closes)]

    def test_buy_win_when_price_rises(self):
        bars = self._bars([100.0, 101, 102, 103, 104])
        d = Decision(ts=_ts(0), action="buy", signed_qty=1, price=100.0, features={})
        label_decisions([d], bars, SPEC, horizon_bars=4, method="horizon", cost_aware=False)
        assert d.label == "win"
        assert d.outcome_return_pct == 4.0
        assert d.outcome_pnl_rub == SPEC.pnl_rub(4.0, 1)   # +4.00 пункта на 1 контракт
        assert d.outcome_ts == _ts(4)

    def test_close_is_win_when_price_falls(self):
        # close = знаковая ставка −1: «верный выход/разворот вниз» если цена упала.
        bars = self._bars([100.0, 99, 98, 97, 96])
        d = Decision(ts=_ts(0), action="close", signed_qty=-1, price=100.0, features={})
        label_decisions([d], bars, SPEC, horizon_bars=4, method="horizon", cost_aware=False)
        assert d.label == "win"
        assert d.outcome_pnl_rub > 0

    def test_flat_within_epsilon(self):
        bars = self._bars([100.0, 100.0, 100.02, 100.0, 100.03])
        d = Decision(ts=_ts(0), action="buy", signed_qty=1, price=100.0, features={})
        label_decisions([d], bars, SPEC, horizon_bars=4, flat_eps_pct=0.1, method="horizon",
                        cost_aware=False)
        assert d.label == "flat"

    def test_tail_decision_left_unlabeled(self):
        bars = self._bars([100.0, 101, 102])
        d = Decision(ts=_ts(2), action="buy", signed_qty=1, price=102.0, features={})
        label_decisions([d], bars, SPEC, horizon_bars=4)   # нет полного горизонта
        assert d.label is None
        assert d.outcome_return_pct is None

    def test_cost_aware_marginal_gain_becomes_loss(self):
        # Маргинальный рост (+0.15 пункта, валовый > flat) НЕ покрывает широкие издержки
        # (slippage 20 тиков) → cost-aware метит loss и net<0, хотя валовая доходность >0.
        bars = self._bars([100.0, 100.05, 100.1, 100.12, 100.15])
        d = Decision(ts=_ts(0), action="buy", signed_qty=1, price=100.0, features={})
        label_decisions([d], bars, SPEC, horizon_bars=4, method="horizon", cost_aware=True,
                        slippage_ticks=20.0)
        assert d.label == "loss"
        assert d.outcome_pnl_rub < 0            # чистый P&L отрицателен после издержек
        assert d.outcome_return_pct > 0         # валовая доходность всё ещё положительна

    def test_cost_aware_large_gain_stays_win(self):
        bars = self._bars([100.0, 101, 102, 103, 110])
        d = Decision(ts=_ts(0), action="buy", signed_qty=1, price=100.0, features={})
        label_decisions([d], bars, SPEC, horizon_bars=4, method="horizon", cost_aware=True,
                        slippage_ticks=20.0)
        assert d.label == "win"                 # крупный ход покрывает издержки
