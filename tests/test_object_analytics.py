"""Тесты кросс-объектной аналитики: агрегат сектора + секция grounding (чистые функции)."""

from __future__ import annotations

from geoanalytics.analytics.indicators import TechnicalIndicators
from geoanalytics.context import grounding as g
from geoanalytics.context.sector_context import aggregate_indicators


def test_aggregate_indicators_means_and_breadth():
    inds = [
        TechnicalIndicators(ret_1m=10.0, rsi14=60, trend="up"),
        TechnicalIndicators(ret_1m=-4.0, rsi14=40, trend="down"),
        TechnicalIndicators(ret_1m=None, rsi14=None, trend="down"),
    ]
    agg = aggregate_indicators(inds)
    assert agg["count"] == 3
    assert agg["breadth_up"] == 1 and agg["breadth_down"] == 2
    assert agg["avg_ret_1m"] == 3.0          # (10 + -4) / 2
    assert agg["avg_rsi14"] == 50.0          # (60 + 40) / 2


def test_aggregate_indicators_empty():
    agg = aggregate_indicators([])
    assert agg["count"] == 0 and "avg_ret_1m" not in agg


def test_grounding_aggregate_section():
    drivers = {"aggregate": {"count": 5, "avg_ret_1m": -7.85, "avg_rsi14": 28.0,
                             "breadth_up": 0, "breadth_down": 5}}
    out = g.render_grounding(drivers, header="ОБЪЕКТ: отрасль «Банки».")
    assert "АГРЕГАТ СЕКТОРА (5 компаний)" in out
    assert "-7.85%" in out
    assert "перепроданность" in out
    assert "растут 0, падают 5" in out
