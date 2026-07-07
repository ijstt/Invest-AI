"""Трек 2 / Пул 4: тесты весов уникальности выборки (перекрытие меток снижает вес)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from geoanalytics.futrader.weights import uniqueness_weights


def _d(day):
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day)


def test_overlap_lowers_weight():
    # L0 изолирована; L1 и L2 перекрываются → у перекрытых вес меньше (после нормировки к 1).
    spans = [
        ("BR", _d(1), _d(1)),    # изолирована
        ("BR", _d(2), _d(4)),    # перекрывается с L2
        ("BR", _d(3), _d(4)),    # сильнее всех перекрыта
    ]
    w = uniqueness_weights(spans)
    assert w[0] > w[1] > w[2]
    assert abs(sum(w) / len(w) - 1.0) < 1e-9    # нормировка к среднему 1


def test_isolated_labels_weight_one():
    spans = [("BR", _d(1), _d(1)), ("GD", _d(5), _d(5))]   # разные активы, не пересекаются
    w = uniqueness_weights(spans)
    assert w == [1.0, 1.0]


def test_unlabeled_weight_one_and_excluded_from_norm():
    spans = [
        ("BR", _d(1), None),     # неразмеченное → вес 1, в нормировке не участвует
        ("BR", _d(2), _d(4)),
        ("BR", _d(3), _d(4)),
    ]
    w = uniqueness_weights(spans)
    assert w[0] == 1.0
    # среднее по размеченным = 1
    labeled = [w[1], w[2]]
    assert abs(sum(labeled) / 2 - 1.0) < 1e-9


def test_assets_independent():
    # перекрытие внутри BR не влияет на одиночную метку GD.
    spans = [
        ("BR", _d(1), _d(3)),
        ("BR", _d(2), _d(3)),
        ("GD", _d(1), _d(1)),
    ]
    w = uniqueness_weights(spans)
    assert w[1] < w[2]              # перекрытая BR-метка легче одиночной GD
