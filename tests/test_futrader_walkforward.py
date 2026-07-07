"""Трек 2 / Фаза B: тест DB-раннера walk-forward на синтетике (репозиторий заглушён, без БД)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import geoanalytics.storage.repositories as repos
from geoanalytics.futrader.evaluation import run_walk_forward


@dataclass
class _Row:
    ts: datetime
    interval: str
    features: dict
    signed_qty: int
    label: str
    outcome_return_pct: float
    outcome_pnl_rub: float
    asset_code: str = "BR"
    outcome_ts: datetime | None = None


def _make_rows(n=200):
    """Зашумлённая обучаемая синтетика: P(win) растёт с ret_1, но не детерминирована."""
    import numpy as np

    rng = np.random.default_rng(0)
    rows = []
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(n):
        r1 = float(rng.normal(0, 1))
        p = 1.0 / (1.0 + np.exp(-1.5 * r1))   # сигмоида: сигнал есть, но не детерминирован
        win = rng.random() < p
        ret = float(rng.normal(1.2 if win else -1.0, 0.6))   # шум в исходе
        ts = base + timedelta(hours=i)
        rows.append(_Row(
            ts=ts, interval="1h",
            features={"ret_1": r1, "ret_5": float(rng.normal(0, 1)),
                      "rsi_14": 50 + r1 * 8},
            signed_qty=1, label="win" if win else "loss",
            outcome_return_pct=ret, outcome_pnl_rub=ret * 100,
            asset_code="BR", outcome_ts=ts + timedelta(hours=10)))
    return rows


class _FakeRepo:
    def __init__(self, session):
        pass

    def labeled(self, *, asset_code=None, source=None):
        return _make_rows()


def test_walk_forward_learns_and_reports_metrics(monkeypatch):
    monkeypatch.setattr(repos, "FuturesDecisionRepository", _FakeRepo)
    res = run_walk_forward(None, source="momentum", interval="1h",
                           threshold=0.55, n_splits=4, min_train=40, n_trials=5)
    assert res.n_samples == 200
    assert res.n_folds >= 1
    assert res.n_taken > 0
    # есть обучаемый сигнал → AUC заметно выше случайного.
    assert res.auc is not None and res.auc > 0.6
    # все метрики посчитаны на зашумлённых исходах (есть дисперсия).
    assert res.sharpe is not None
    assert res.sortino is not None
    assert res.max_drawdown is not None
    assert res.profit_factor is not None
    assert res.deflated_sharpe is not None
    assert 0.0 <= res.deflated_sharpe <= 1.0


def test_walk_forward_too_few_samples_noted(monkeypatch):
    class _Tiny(_FakeRepo):
        def labeled(self, *, asset_code=None, source=None):
            return _make_rows(10)

    monkeypatch.setattr(repos, "FuturesDecisionRepository", _Tiny)
    res = run_walk_forward(None, source="momentum", interval="1h", min_train=40)
    assert res.n_folds == 0
    assert "мало данных" in res.note
