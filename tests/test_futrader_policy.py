"""Трек 2 / T2.4: тесты обучаемой политики — вектор признаков, гейт/сайз, smoke-обучение."""

from __future__ import annotations

import math

import numpy as np

from geoanalytics.futrader.policy import (
    FEATURE_ORDER,
    LearnedPolicy,
    vectorize,
)


class TestVectorize:
    def test_order_and_dir(self):
        feats = {k: float(i) for i, k in enumerate(FEATURE_ORDER[:-1])}
        vec = vectorize(feats, signed_qty=2)
        assert len(vec) == len(FEATURE_ORDER)
        assert vec[:-1] == [float(i) for i in range(len(FEATURE_ORDER) - 1)]
        assert vec[-1] == 1.0          # знак направления (buy +)

    def test_missing_features_become_nan(self):
        vec = vectorize({"ret_1": 0.5}, signed_qty=-1)
        assert vec[0] == 0.5
        assert all(math.isnan(v) for v in vec[1:-1])
        assert vec[-1] == -1.0         # sell/close → −1


class _StubModel:
    """Заглушка: P(win) растёт с первым признаком (ret_1). classes_ = [0, 1]."""

    classes_ = np.array([0, 1])

    def predict_proba(self, X):
        p = 1.0 / (1.0 + np.exp(-X[:, 0]))   # sigmoid(ret_1)
        return np.column_stack([1 - p, p])


class TestDecide:
    def _policy(self):
        return LearnedPolicy(model=_StubModel())

    def test_no_signal_no_trade(self):
        assert self._policy().decide(0, {"ret_1": 5.0}) == 0

    def test_below_threshold_skips(self):
        pol = self._policy()
        # ret_1 сильно отрицательный → P(win) низкий → пропуск.
        assert pol.decide(1, {"ret_1": -5.0}, threshold=0.55) == 0

    def test_above_threshold_takes_and_sizes(self):
        pol = self._policy()
        # ret_1 большой → P(win)→1 → берём и максимальный размер.
        qty = pol.decide(1, {"ret_1": 10.0}, threshold=0.55, max_qty=3)
        assert qty == 3

    def test_short_direction_preserved(self):
        pol = self._policy()
        qty = pol.decide(-1, {"ret_1": 10.0}, threshold=0.55, max_qty=2)
        assert qty < 0          # направление сохранено (шорт)

    def test_score_picks_win_class(self):
        pol = self._policy()
        assert pol.score({"ret_1": 10.0}, 1) > 0.9
        assert pol.score({"ret_1": -10.0}, 1) < 0.1


class TestTrainSmoke:
    def test_learns_separable_pattern(self):
        # синтетика: win, когда ret_1>0; модель должна обучиться и дать положительный lift.
        from sklearn.ensemble import HistGradientBoostingClassifier

        rng = np.random.default_rng(0)
        n = 400
        ret1 = rng.normal(0, 1, n)
        X = np.column_stack([ret1] + [rng.normal(0, 1, n) for _ in range(len(FEATURE_ORDER) - 1)])
        y = (ret1 > 0).astype(int)
        split = int(n * 0.7)
        model = HistGradientBoostingClassifier(max_iter=100, random_state=0)
        model.fit(X[:split], y[:split])
        pol = LearnedPolicy(model=model)
        # на явно-выигрышном примере (ret_1 большой) уверенность высока.
        feats_win = dict(zip(FEATURE_ORDER[:-1], [3.0] + [0.0] * (len(FEATURE_ORDER) - 2),
                             strict=False))
        assert pol.score(feats_win, 1) > 0.7
