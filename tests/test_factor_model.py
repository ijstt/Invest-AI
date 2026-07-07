"""Тесты L3: кросс-секционная факторная модель (чистое ядро — без БД)."""

from __future__ import annotations

from geoanalytics.analytics.factor_model import (
    _metric_growth,
    _percentiles,
    _zscores,
    cross_sectional_scores,
)


def test_zscores_standardize_and_clip():
    z = _zscores({1: 0.0, 2: 10.0})
    # симметрично вокруг среднего: знаки противоположны, |z| равны
    assert z[1] < 0 < z[2]
    assert abs(z[1]) == abs(z[2])


def test_zscores_degenerate():
    assert _zscores({1: 5.0}) == {1: 0.0}          # <2 точек
    assert _zscores({1: 3.0, 2: 3.0}) == {1: 0.0, 2: 0.0}  # нулевой разброс


def test_zscores_winsorized():
    # один сильный выброс не уносит z за ±3
    vals = {i: 0.0 for i in range(10)}
    vals[99] = 1e6
    z = _zscores(vals)
    assert all(-3.0 <= v <= 3.0 for v in z.values())
    assert z[99] == 3.0


def test_percentiles_rank_ascending():
    pct = _percentiles({1: 10.0, 2: 20.0, 3: 30.0})
    assert pct[1] == 0.0 and pct[3] == 100.0 and pct[2] == 50.0
    assert _percentiles({7: 5.0}) == {7: 50.0}     # один актив → середина


def test_cross_sectional_cheap_quality_ranks_high():
    # актив 1 — дёшево (высокий earnings_yield) и качественно (высокий ROE); актив 2 — наоборот.
    inputs = {
        1: {"earnings_yield": 0.20, "roe": 30.0, "rev_growth": 25.0},
        2: {"earnings_yield": 0.05, "roe": 5.0, "rev_growth": 2.0},
        3: {"earnings_yield": 0.10, "roe": 15.0, "rev_growth": 10.0},
    }
    out = cross_sectional_scores(inputs)
    # актив 1 в топе по всем трём факторам и по композиту
    for f in ("value", "quality", "growth", "composite"):
        assert out[1][f]["zscore"] > out[2][f]["zscore"]
        assert out[1][f]["percentile"] == 100.0
        assert out[2][f]["percentile"] == 0.0


def test_cross_sectional_partial_metrics():
    # актив без growth-метрик не получает growth-фактор, но получает value/composite
    inputs = {
        1: {"earnings_yield": 0.2},
        2: {"earnings_yield": 0.1},
        3: {"earnings_yield": 0.15, "rev_growth": 10.0, "profit_growth": 5.0},
    }
    out = cross_sectional_scores(inputs)
    assert "growth" not in out[1]
    assert "value" in out[1] and "composite" in out[1]
    # у актива 3 growth есть, но он один с growth → z=0 (нельзя стандартизовать по одному)
    assert out[3]["growth"]["zscore"] == 0.0


def test_cross_sectional_empty():
    assert cross_sectional_scores({}) == {}
    # активы без единой суб-метрики выпадают из результата
    assert cross_sectional_scores({1: {}, 2: {}}) == {}


class _FakeRow:
    def __init__(self, period, value):
        self.period = period
        self.value = value


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self, _stmt):
        return _FakeScalars(self._rows)


def test_metric_growth_yoy():
    rows = [_FakeRow("2022", 100.0), _FakeRow("2023", 120.0), _FakeRow("2024", 150.0)]
    # рост между двумя свежайшими: (150-120)/120 = 25%
    assert _metric_growth(_FakeSession(rows), 1, "revenue") == 25.0


def test_metric_growth_guards():
    assert _metric_growth(_FakeSession([_FakeRow("2024", 100.0)]), 1, "revenue") is None
    # база ≤ 0 → None (рост неинтерпретируем)
    rows = [_FakeRow("2023", 0.0), _FakeRow("2024", 50.0)]
    assert _metric_growth(_FakeSession(rows), 1, "revenue") is None
