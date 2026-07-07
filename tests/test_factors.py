"""Тесты унифицированного слоя факторных серий (analytics.factors)."""

from __future__ import annotations

from datetime import date, timedelta

from geoanalytics.analytics import factors
from geoanalytics.analytics.factors import factor_series


def test_factor_series_window_filter_and_change(monkeypatch):
    """Окно lookback отсекает старые точки; last/change_pct считаются по оставшимся."""
    today = date.today()
    series = {
        today - timedelta(days=400): 50.0,   # вне окна 365 дней — отбрасывается
        today - timedelta(days=10): 100.0,
        today - timedelta(days=1): 120.0,
    }
    monkeypatch.setattr(factors, "_macro_levels", lambda s, ind: series)
    monkeypatch.setattr(factors, "_world_metal_levels", lambda s, ind: {})
    monkeypatch.setattr(factors, "_fx_levels", lambda s, c: {})
    monkeypatch.setattr(factors, "_cross_levels", lambda s, a, b: {})

    out = factor_series(None, lookback_days=365)
    brent = next(f for f in out if f.key == "brent")
    assert brent.values == [100.0, 120.0]      # дата -400 отфильтрована окном
    assert brent.last == 120.0
    assert brent.change_pct == 20.0
    assert brent.group == "commodity"


def test_factor_series_empty_is_safe(monkeypatch):
    """Ряд без данных за окно: пустые values, last/change_pct = None (страница покажет прочерк)."""
    monkeypatch.setattr(factors, "_macro_levels", lambda s, ind: {})
    monkeypatch.setattr(factors, "_world_metal_levels", lambda s, ind: {})
    monkeypatch.setattr(factors, "_fx_levels", lambda s, c: {})
    monkeypatch.setattr(factors, "_cross_levels", lambda s, a, b: {})

    out = factor_series(None)
    assert {f.key for f in out} >= {"brent", "gold", "USD", "usd_eur"}
    gold = next(f for f in out if f.key == "gold")
    assert gold.values == [] and gold.last is None and gold.change_pct is None
