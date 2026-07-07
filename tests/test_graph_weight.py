"""Тесты единого веса узла графа (A2): комбинация сигналов, TA-сила, агрегат сектора."""

from __future__ import annotations

from geoanalytics.analytics.graph_weight import (
    aggregate_weight,
    combine,
    normalize_weight,
    ta_strength,
)


def test_combine_averages_available_signals_only():
    # Один сигнал → его же значение; None пропускается, не штрафует.
    assert combine({"news": 0.6}) == 0.6
    assert combine({"news": 0.6, "sentiment": None}) == 0.6
    # Все None → 0.
    assert combine({"news": None, "ta": None}) == 0.0


def test_combine_is_weighted_mean_and_clamped():
    # news (0.5) и sentiment (0.25): (0.5*1.0 + 0.25*0.0)/(0.5+0.25) = 0.666…
    assert abs(combine({"news": 1.0, "sentiment": 0.0}) - (0.5 / 0.75)) < 1e-9
    # Значения вне [0,1] клампятся.
    assert combine({"news": 5.0}) == 1.0


def test_ta_strength_rsi_extremes_and_none():
    assert ta_strength({}) is None
    # RSI=50 → нейтрально (0); RSI=80 → |30|/50=0.6.
    assert ta_strength({"rsi14": 50}) == 0.0
    assert abs(ta_strength({"rsi14": 80}) - 0.6) < 1e-9


def test_ta_strength_blends_rsi_and_macd():
    # RSI=50 (0.0) + MACD hist large vs 2% цены (клампится к 1.0) → среднее 0.5.
    s = ta_strength({"rsi14": 50, "macd_hist": 100.0, "last": 100.0})
    assert abs(s - 0.5) < 1e-9


def test_normalize_weight_scales_to_peak_with_floor():
    # Пик → 1.0; половина пика → floor + (1-floor)/2; ноль → floor (узел не схлопывается).
    assert normalize_weight(10.0, 10.0) == 1.0
    assert abs(normalize_weight(5.0, 10.0, floor=0.2) - (0.2 + 0.8 * 0.5)) < 1e-9
    assert normalize_weight(0.0, 10.0, floor=0.25) == 0.25
    # Нулевой пик (нет данных оборота) → floor.
    assert normalize_weight(5.0, 0.0, floor=0.3) == 0.3


def test_aggregate_weight_blends_max_and_mean():
    assert aggregate_weight([]) == 0.0
    # max=0.8, mean=0.4 → 0.6*0.8 + 0.4*0.4 = 0.64.
    assert abs(aggregate_weight([0.8, 0.0]) - 0.64) < 1e-9
    # Единственный потомок → 0.6*x + 0.4*x = x.
    assert abs(aggregate_weight([0.5]) - 0.5) < 1e-9
