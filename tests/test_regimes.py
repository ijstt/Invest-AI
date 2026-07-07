"""Тесты режимов рынка (G2): EWMA-серия, HMM на синтетике, упорядочивание состояний."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from geoanalytics.analytics.regimes import (
    detect_regimes,
    ewma_vol_series,
    fit_hmm,
    viterbi,
)

_RNG = np.random.default_rng(42)


def _synthetic_features(calm: int = 300, crisis: int = 100, tail: int = 200):
    """log-vol с явной сменой режима: спокойно → кризис → спокойно."""
    vol = np.concatenate([
        _RNG.normal(np.log(0.8), 0.1, calm),
        _RNG.normal(np.log(3.0), 0.15, crisis),
        _RNG.normal(np.log(0.8), 0.1, tail),
    ])
    fx = vol + _RNG.normal(0, 0.1, len(vol))  # vol RUB ходит вместе с рынком
    feats = np.column_stack([vol, fx])
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(len(vol))]
    return feats, days, calm, crisis


class TestEwmaVolSeries:
    def test_constant_returns(self):
        # При постоянной |доходности| EWMA сходится к ней (в %).
        series = ewma_vol_series([0.01] * 300)
        assert abs(series[-1] - 1.0) < 0.01

    def test_one_point_per_return(self):
        assert len(ewma_vol_series([0.01, -0.02, 0.005])) == 3

    def test_empty(self):
        assert ewma_vol_series([]) == []


class TestHmm:
    def test_recovers_regime_switch(self):
        feats, _, calm, crisis = _synthetic_features()
        means, variances, trans, pi, _ = fit_hmm(feats, n_states=2)
        path = viterbi(feats, means, variances, trans, pi)
        hi = int(np.argmax(means[:, 0]))  # состояние с большей vol
        in_crisis = path[calm:calm + crisis]
        outside = np.concatenate([path[:calm], path[calm + crisis:]])
        assert (in_crisis == hi).mean() > 0.95
        assert (outside == hi).mean() < 0.05

    def test_transitions_are_sticky(self):
        feats, _, _, _ = _synthetic_features()
        _, _, trans, _, _ = fit_hmm(feats, n_states=2)
        assert trans[0, 0] > 0.9 and trans[1, 1] > 0.9


class TestDetectRegimes:
    def test_crisis_window_labeled(self):
        feats, days, calm, crisis = _synthetic_features()
        r = detect_regimes(feats, days, n_states=2)
        assert r.error is None
        assert r.labels == ["спокойный", "кризис"]
        window = r.states[calm:calm + crisis]
        assert window.count(1) / len(window) > 0.95
        assert r.current == "спокойный"           # хвост спокойный
        assert r.state_vol["кризис"] > r.state_vol["спокойный"]

    def test_current_since_points_to_segment_start(self):
        feats, days, calm, crisis = _synthetic_features()
        r = detect_regimes(feats, days, n_states=2)
        seg_start = len(days) - 1
        while seg_start > 0 and r.states[seg_start - 1] == r.states[-1]:
            seg_start -= 1
        assert r.current_since == days[seg_start]

    def test_too_few_points(self):
        feats, days, _, _ = _synthetic_features(calm=50, crisis=20, tail=30)
        r = detect_regimes(feats, days, n_states=2)
        assert r.error is not None

    def test_three_states_ordered_by_vol(self):
        feats, days, _, _ = _synthetic_features()
        r = detect_regimes(feats, days, n_states=3)
        vols = [r.state_vol[name] for name in r.labels]
        assert vols == sorted(vols)
