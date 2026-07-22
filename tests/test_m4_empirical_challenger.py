"""Empirical test harness created by Challenger 2 to test Web API boundary conditions,
route interactions, cache invalidations, and edge-case query parameters.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from geoanalytics.analytics.backtest import BacktestResult
from geoanalytics.analytics.portfolio import PortfolioReport, PositionReport
from geoanalytics.api import app as api_app
from geoanalytics.api import web
from geoanalytics.query.asset_report import AssetReport
from geoanalytics.query.news_summary import MarketSnapshot

client = TestClient(api_app.app)


class TestCacheInvalidationAndIsolation:
    """Empirical verification of web._cache TTL, isolation, and invalidation upon mutations."""

    def test_cache_invalidation_on_portfolio_mutation(self, monkeypatch):
        web._cache.clear()
        web._cache["portfolio_report"] = (100.0, "fake_report")
        web._cache["portfolio_stance"] = (100.0, "fake_stance")

        # Mock underlying _add_position to avoid DB side effects in unit test
        monkeypatch.setattr(web, "_add_position", lambda t, q, p: None)
        monkeypatch.setattr(web, "_portfolio_context", lambda: {
            "report": PortfolioReport(error="empty"), "correlations": [], "exposure": []
        })

        # Call POST /ui/portfolio/add
        res = client.post("/ui/portfolio/add", data={"ticker": "SBER", "quantity": "10", "price": "250"})
        assert res.status_code == 200
        assert "portfolio_report" not in web._cache
        assert "portfolio_stance" not in web._cache

    def test_cache_invalidation_on_portfolio_remove(self, monkeypatch):
        web._cache.clear()
        web._cache["portfolio_report"] = (100.0, "fake_report")
        web._cache["portfolio_stance"] = (100.0, "fake_stance")

        monkeypatch.setattr(web, "_remove_position", lambda t: None)
        monkeypatch.setattr(web, "_portfolio_context", lambda: {
            "report": PortfolioReport(error="empty"), "correlations": [], "exposure": []
        })

        res = client.post("/ui/portfolio/remove", data={"ticker": "SBER"})
        assert res.status_code == 200
        assert "portfolio_report" not in web._cache
        assert "portfolio_stance" not in web._cache

    def test_cache_invalidation_on_portfolio_cash(self, monkeypatch):
        web._cache.clear()
        web._cache["portfolio_report"] = (100.0, "fake_report")
        web._cache["portfolio_stance"] = (100.0, "fake_stance")

        class FakeCashRepo:
            def __init__(self, s): pass
            def set_balance(self, c, a): pass

        monkeypatch.setattr("geoanalytics.storage.repositories.CashBalanceRepository", FakeCashRepo)
        monkeypatch.setattr(web, "_portfolio_context", lambda: {
            "report": PortfolioReport(error="empty"), "correlations": [], "exposure": []
        })

        res = client.post("/ui/portfolio/cash", data={"currency": "RUB", "amount": "100000"})
        assert res.status_code == 200
        assert "portfolio_report" not in web._cache
        assert "portfolio_stance" not in web._cache


class TestAssetRouterBoundaries:
    """Boundary conditions for /ui/asset and /ui/partials/asset/* endpoints."""

    def test_chart_partial_boundary_parameters(self, monkeypatch):
        monkeypatch.setattr(web, "_asset_ohlcv", lambda *a, **kw: [])

        # 1. Invalid range string (e.g., '10y', 'bogus')
        r = client.get("/ui/partials/asset/chart?ticker=SBER&range=bogus")
        assert r.status_code == 200

        # 2. Invalid period string (e.g., 'Z', 'YEAR')
        r = client.get("/ui/partials/asset/chart?ticker=SBER&period=Z")
        assert r.status_code == 200

        # 3. Invalid kind string (e.g., 'scatter', 'bar')
        r = client.get("/ui/partials/asset/chart?ticker=SBER&kind=scatter")
        assert r.status_code == 200

        # 4. Zero flags for overlays, volume, oscillator
        r = client.get("/ui/partials/asset/chart?ticker=SBER&ovl=0&vol=0&osc=0")
        assert r.status_code == 200

        # 5. Invalid non-integer flag (FastAPI returning 422 Validation Error)
        r = client.get("/ui/partials/asset/chart?ticker=SBER&ovl=notanint")
        assert r.status_code == 422

    def test_indicators_partial_boundary_parameters(self, monkeypatch):
        monkeypatch.setattr(web, "_indicators_context", lambda t, p="D": {
            "ticker": t.upper(), "indicators": {}, "ind_period": p
        })

        # 1. Invalid period (should default gracefully or pass through)
        r = client.get("/ui/partials/asset/indicators?ticker=SBER&period=INVALID")
        assert r.status_code == 200

        # 2. Whitespace ticker
        r = client.get("/ui/partials/asset/indicators?ticker=%20%20")
        assert r.status_code == 200
        assert "Введите тикер" in r.text

    def test_asset_page_default_ticker(self, monkeypatch):
        monkeypatch.setattr(web, "build_report", lambda t, **kw: AssetReport(ticker="IMOEX", found=False))
        monkeypatch.setattr(web, "list_assets", lambda: [])
        monkeypatch.setattr(web, "_asset_context", lambda t: {"ticker": t, "chart": None, "report": AssetReport(ticker=t, found=False)})

        r = client.get("/ui/asset")
        assert r.status_code == 200
        assert "IMOEX" in r.text


class TestDashboardRouterBoundaries:
    """Boundary test cases for dashboard and news endpoints."""

    def test_dashboard_hours_boundary(self, monkeypatch):
        snap = MarketSnapshot(key_rate=16.0)
        monkeypatch.setattr(web, "build_snapshot", lambda hours=24, **kw: snap)

        # 1. Normal call
        r = client.get("/?hours=48")
        assert r.status_code == 200

        # 2. Invalid non-int hours -> 422
        r = client.get("/?hours=abc")
        assert r.status_code == 422

    def test_news_partial_boundaries(self, monkeypatch):
        monkeypatch.setattr(web, "recent_headlines", lambda **kw: [])

        # 1. Zero hours, zero limit
        r = client.get("/ui/partials/news?hours=0&limit=0")
        assert r.status_code == 200

        # 2. Negative parameters
        r = client.get("/ui/partials/news?hours=-10&limit=-5")
        assert r.status_code == 200

    def test_ask_partial_xss_and_boundary(self, monkeypatch):
        # 1. Script tag input
        r = client.get("/ui/partials/ask?q=<script>alert('xss')</script>")
        assert r.status_code == 200

        # 2. Extremely long string
        r = client.get(f"/ui/partials/ask?q={'what%20is%20' * 100}")
        assert r.status_code == 200


class TestAlertsRouterBoundaries:
    """Boundary conditions for alerts acknowledge, mute, unmute, and feeds."""

    def test_ack_alert_not_found(self, monkeypatch):
        monkeypatch.setattr(web.manage, "acknowledge", lambda alert_id: False)
        monkeypatch.setattr(web, "get_alert", lambda alert_id: None)

        r = client.post("/ui/alerts/999999/ack")
        assert r.status_code == 404

    def test_alert_mute_invalid_scope(self, monkeypatch):
        def _raise_mute(*a, **kw):
            raise ValueError("invalid scope_type")

        monkeypatch.setattr(web.manage, "mute_for_days", _raise_mute)
        monkeypatch.setattr(web.manage, "list_mutes", lambda: [])

        r = client.post("/ui/alerts/mute", data={"scope_type": "invalid", "scope_value": "foo"})
        assert r.status_code == 200
        assert 'id="mutes-panel"' in r.text

    def test_alert_feed_unknown_filters(self, monkeypatch):
        monkeypatch.setattr(web, "recent_alerts", lambda **kw: [])
        monkeypatch.setattr(web.manage, "list_mutes", lambda: [])

        r = client.get("/ui/alerts?severity=unknown_sev&alert_type=unknown_type&ticker=UNKNOWN")
        assert r.status_code == 200


class TestGraphAndFactorsBoundaries:
    """Boundary conditions for graph and factors routes."""

    def test_graph_page_empty_ticker(self, monkeypatch):
        monkeypatch.setattr(web, "_graph_context", lambda t: {"ticker": t, "graph": None, "assets": []})
        r = client.get("/ui/graph?ticker=")
        assert r.status_code == 200

    def test_market_graph_page_render(self, monkeypatch):
        monkeypatch.setattr(web, "_market_graph_context", lambda: {"graph": None, "is_market": True})
        r = client.get("/ui/graph/market")
        assert r.status_code == 200

    def test_market_heatmap_partial_render(self, monkeypatch):
        monkeypatch.setattr(web, "_market_heatmap_context", lambda: {"heatmap": None})
        r = client.get("/ui/partials/graph/heatmap")
        assert r.status_code == 200
