"""Трек 2 / Пул 9 / D: тесты live-дрейфа (PSI / калибровка / decay / пары сделок) — без БД."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from geoanalytics.futrader.monitoring import (
    _pair_paper_trades,
    decay,
    feature_psi,
    paper_calibration,
    psi,
)


class TestPSI:
    def test_identical_distribution_low(self):
        base = [float(i % 10) for i in range(100)]
        assert psi(base, list(base)) < 0.1

    def test_shifted_distribution_high(self):
        base = [float(i % 10) for i in range(100)]
        live = [v + 20.0 for v in base]            # полностью сдвинуто вверх
        assert psi(base, live) > 0.25

    def test_too_few_none(self):
        assert psi([1.0] * 5, [1.0] * 5) is None


class TestFeaturePSI:
    def test_per_feature(self):
        base = [{"a": float(i % 10), "b": 1.0} for i in range(60)]
        live = [{"a": float(i % 10) + 20.0, "b": 1.0} for i in range(60)]
        out = feature_psi(base, live, ("a", "b"))
        assert out["a"] > 0.25               # сдвинут
        assert out["b"] < 0.1                # стабилен


class TestPaperCalibration:
    def test_well_calibrated(self):
        # p=0.6 и ровно 60% выигрышей → calib_gap ≈ 0
        pairs = [(0.6, 1)] * 30 + [(0.6, 0)] * 20
        brier, gap, n = paper_calibration(pairs)
        assert n == 50
        assert gap is not None and gap < 0.05

    def test_overconfident(self):
        pairs = [(0.9, 0)] * 25 + [(0.9, 1)] * 25     # обещает 0.9, выигрывает 0.5
        _, gap, _ = paper_calibration(pairs)
        assert gap is not None and gap > 0.3

    def test_too_few_none(self):
        brier, gap, n = paper_calibration([(0.6, 1)] * 5)
        assert brier is None and gap is None and n == 5


class TestDecay:
    def test_positive_decay(self):
        assert decay(0.45, 0.60) == 0.15      # реализ. хуже ожидания

    def test_no_decay(self):
        assert decay(0.62, 0.60) == -0.02

    def test_none_inputs(self):
        assert decay(None, 0.6) is None


@dataclass
class _Trade:
    ts: datetime
    asset_code: str
    interval: str
    source: str
    reason: str
    p_win: float | None = None
    realized_pnl: float | None = None


class TestPairPaperTrades:
    def test_pairs_entry_to_exit(self):
        t0 = datetime(2026, 6, 21, tzinfo=UTC)
        trades = [
            _Trade(t0, "BR", "1h", "rsi", "entry", p_win=0.7),
            _Trade(t0 + timedelta(hours=2), "BR", "1h", "rsi", "exit", realized_pnl=120.0),
            _Trade(t0 + timedelta(hours=3), "BR", "1h", "rsi", "entry", p_win=0.4),
            _Trade(t0 + timedelta(hours=5), "BR", "1h", "rsi", "exit", realized_pnl=-50.0),
        ]
        pairs = _pair_paper_trades(trades)
        assert pairs == [(0.7, 1), (0.4, 0)]

    def test_unpaired_entry_ignored(self):
        t0 = datetime(2026, 6, 21, tzinfo=UTC)
        trades = [_Trade(t0, "BR", "1h", "rsi", "entry", p_win=0.7)]   # без выхода
        assert _pair_paper_trades(trades) == []
