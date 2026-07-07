"""Тесты B3: вывод прогнозов брокеров (потенциал/сюрприз) и TTL-кэш бэктеста."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from geoanalytics.analytics.forecasts import forecasts_for_asset


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _stmt):
        class _R:
            def __init__(self, rows):
                self._rows = rows

            def all(self):
                return self._rows
        return _R(self._rows)


def test_forecast_implied_potential_for_pending_target():
    future = datetime.now(UTC).date() + timedelta(days=90)
    # (kind, value, unit, target_date, source, url, published_at)
    rows = [("target_price", 360.0, "RUB", future, "Сбер", "u", None)]
    out = forecasts_for_asset(_FakeSession(rows), asset_id=1, last_price=300.0)
    assert out[0]["implied_pct"] == 20.0          # 360/300 − 1 = +20%
    assert out[0]["surprise_pct"] is None         # горизонт не наступил
    assert out[0]["matured"] is False


def test_forecast_surprise_when_matured():
    past = datetime.now(UTC).date() - timedelta(days=5)
    rows = [("target_price", 250.0, "RUB", past, "БКС", "u", None)]
    out = forecasts_for_asset(_FakeSession(rows), asset_id=1, last_price=300.0)
    # Цена 300 против таргета 250 → рынок выше прогноза на +20%.
    assert out[0]["matured"] is True
    assert out[0]["surprise_pct"] == 20.0


def test_forecast_without_price_has_no_pct():
    rows = [("dividend", 25.0, "RUB", None, "Т", "u", None)]
    out = forecasts_for_asset(_FakeSession(rows), asset_id=1, last_price=None)
    assert out[0]["implied_pct"] is None and out[0]["surprise_pct"] is None
    assert out[0]["label"] == "Дивиденд"


def test_backtest_cache_reuses_result(monkeypatch):
    import geoanalytics.analytics.backtest as bt

    calls = {"n": 0}

    def fake_backtest(ticker, strategy="sma_cross", **kw):
        calls["n"] += 1
        return f"result-{ticker}-{strategy}"

    monkeypatch.setattr(bt, "backtest_asset", fake_backtest)
    bt._BT_CACHE.clear()
    a = bt.backtest_asset_cached("SBER", "sma_cross")
    b = bt.backtest_asset_cached("SBER", "sma_cross")
    assert a == b == "result-SBER-sma_cross"
    assert calls["n"] == 1                          # второй вызов — из кэша
    # Истёкший TTL → пересчёт.
    bt.backtest_asset_cached("SBER", "sma_cross", ttl=0)
    assert calls["n"] == 2
