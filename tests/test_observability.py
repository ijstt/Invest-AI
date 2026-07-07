"""Тесты оси наблюдаемости I: I2 (continuous eval + дрейф) и I5 (active learning) — чистое ядро."""

from __future__ import annotations

import pytest

from geoanalytics.analytics.active_learning import confidence_proxy
from geoanalytics.analytics.continuous_eval import check_drift, market_agreement


# --- I2: согласие значимости с рынком ---
def test_market_agreement_precision_recall():
    """precision = двинувшихся среди помеченных; recall = помеченных среди двинувшихся."""
    # (significance, abn_1d%): gate=0.6, move=1.0%
    pairs = [
        (0.8, 2.0),    # помечена + двинулась → TP
        (0.7, 0.3),    # помечена + НЕ двинулась → FP
        (0.2, 1.5),    # НЕ помечена + двинулась → FN
        (0.1, 0.2),    # НЕ помечена + НЕ двинулась → TN
        (0.9, -1.2),   # помечена + двинулась (модуль) → TP
    ]
    a = market_agreement(pairs, gate=0.6, move_pct=1.0)
    assert a.n == 5 and a.n_flagged == 3 and a.n_moved == 3
    assert a.precision == pytest.approx(2 / 3)     # 2 TP из 3 помеченных
    assert a.recall == pytest.approx(2 / 3)        # 2 TP из 3 двинувшихся


def test_market_agreement_skips_nones_and_zero_denominator():
    a = market_agreement([(None, 1.0), (0.5, None)], gate=0.6)
    assert a.n == 0 and a.precision is None and a.recall is None
    # Ничего не помечено значимым → precision None, recall 0/1.
    b = market_agreement([(0.1, 2.0), (0.2, 0.1)], gate=0.6)
    assert b.n == 2 and b.n_flagged == 0 and b.precision is None
    assert b.recall == 0.0


# --- I2: дрейф относительно трейлинг-базы ---
def test_check_drift_triggers_on_degradation():
    d = check_drift(0.50, [0.80, 0.82, 0.78], rel_tol=0.15)   # база ~0.80, падение ~37%
    assert d.drifted and d.baseline == pytest.approx(0.80)
    assert d.drop_pct == pytest.approx(37.5, abs=0.5)


def test_check_drift_within_tolerance_and_insufficient_history():
    assert not check_drift(0.75, [0.80, 0.78, 0.79]).drifted     # в пределах 15%
    assert not check_drift(0.10, [0.80, 0.82]).drifted           # истории < min → не дрейф
    assert not check_drift(None, [0.8, 0.8, 0.8]).drifted        # нет значения


# --- I5: уверенность предсказания ---
def test_confidence_proxy_sentiment():
    assert confidence_proxy("sentiment", sentiment_score=0.05, significance=None) == 0.05
    assert confidence_proxy("sentiment", sentiment_score=-0.9, significance=None) == 0.9
    assert confidence_proxy("sentiment", sentiment_score=None, significance=0.5) is None


def test_confidence_proxy_significance_near_gate_is_low():
    # На гейте 0.6 — нулевая уверенность; дальше от гейта — выше.
    near = confidence_proxy("significance", sentiment_score=None, significance=0.6, gate=0.6)
    far = confidence_proxy("significance", sentiment_score=None, significance=0.05, gate=0.6)
    assert near == 0.0
    assert far > near
    assert confidence_proxy("significance", sentiment_score=None, significance=None) is None


def test_confidence_proxy_unknown_task():
    with pytest.raises(ValueError):
        confidence_proxy("nope", sentiment_score=0.1, significance=0.1)
