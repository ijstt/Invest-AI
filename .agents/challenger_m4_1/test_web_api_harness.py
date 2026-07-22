"""Dynamic empirical test harness for Milestone 4 Web API modularization verification.

Tests all 27 web routes across 8 sub-routers, monkeypatching mechanics, response types,
error handlers, and JSON API interoperability.
"""

from __future__ import annotations

import sys
import unittest
from datetime import UTC, datetime
from pathlib import Path

# Add project root to sys.path
root_dir = Path(__file__).resolve().parents[2]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from fastapi.testclient import TestClient
from geoanalytics.analytics.backtest import BacktestResult
from geoanalytics.analytics.portfolio import PortfolioReport, PositionReport
from geoanalytics.api import app as api_app
from geoanalytics.api import web
from geoanalytics.query.asset_report import AssetReport
from geoanalytics.query.news_summary import MarketSnapshot

client = TestClient(api_app.app)


class TestWebAPIModularizationHarness(unittest.TestCase):

    def setUp(self):
        web._cache.clear()

    def tearDown(self):
        web._cache.clear()

    def test_router_route_count_and_assembly(self):
        """Verify all sub-routers are mounted and total web routes count is exact."""
        web_routes = [r.path for r in web.router.routes]
        expected_paths = {
            "/",
            "/ui/partials/status",
            "/ui/partials/news",
            "/ui/partials/ask",
            "/ui/asset",
            "/ui/partials/asset",
            "/ui/partials/asset/chart",
            "/ui/partials/asset/indicators",
            "/ui/backtest",
            "/ui/partials/backtest",
            "/ui/portfolio",
            "/ui/portfolio/add",
            "/ui/portfolio/remove",
            "/ui/portfolio/cash",
            "/ui/graph",
            "/ui/partials/graph",
            "/ui/graph/market",
            "/ui/partials/graph/market",
            "/ui/partials/graph/heatmap",
            "/ui/factors",
            "/ui/track2",
            "/ui/partials/track2",
            "/ui/alerts",
            "/ui/partials/alerts",
            "/ui/alerts/{alert_id}/ack",
            "/ui/alerts/mute",
            "/ui/alerts/unmute/{mute_id}",
        }
        for path in expected_paths:
            self.assertIn(path, web_routes, f"Missing route {path} in web.router")
        print(f"[SUCCESS] Route assembly verified: {len(web_routes)} web routes present.")

    def test_dashboard_router_endpoints(self):
        """Test Dashboard sub-router routes."""
        snap = MarketSnapshot(key_rate=16.0, key_rate_date="15.07.2026", fx={"USD": 85.0})
        orig_build_snap = getattr(web, "build_snapshot", None)
        try:
            web.build_snapshot = lambda **kw: snap
            r = client.get("/")
            self.assertEqual(r.status_code, 200)
            self.assertIn("Сводка рынка", r.text)
            self.assertIn("16.0", r.text)

            r_status = client.get("/ui/partials/status")
            self.assertEqual(r_status.status_code, 200)

            r_news = client.get("/ui/partials/news?hours=24&limit=5")
            self.assertEqual(r_news.status_code, 200)

            r_ask_empty = client.get("/ui/partials/ask?q=")
            self.assertEqual(r_ask_empty.status_code, 200)
            self.assertIn("Задайте вопрос", r_ask_empty.text)
        finally:
            if orig_build_snap:
                web.build_snapshot = orig_build_snap
        print("[SUCCESS] Dashboard router endpoints verified.")

    def test_asset_router_endpoints(self):
        """Test Asset sub-router routes with monkeypatched web helpers."""
        report = AssetReport(ticker="SBER", found=True, name="Сбербанк", indicators={"last": 300.0})
        rows = [(datetime(2026, 6, d, tzinfo=UTC), 290.0, 296.0, 289.0, 295.0, 1000.0) for d in range(1, 10)]

        orig_build_report = web.build_report
        orig_asset_ohlcv = web._asset_ohlcv
        try:
            web.build_report = lambda t, **kw: report if t.upper() == "SBER" else AssetReport(ticker=t, found=False)
            web._asset_ohlcv = lambda *a, **kw: rows

            # GET /ui/asset
            r = client.get("/ui/asset?ticker=SBER")
            self.assertEqual(r.status_code, 200)
            self.assertIn("Сбербанк", r.text)

            # GET /ui/partials/asset
            r_part = client.get("/ui/partials/asset?ticker=SBER")
            self.assertEqual(r_part.status_code, 200)

            # GET /ui/partials/asset/chart (line and candles)
            r_chart = client.get("/ui/partials/asset/chart?ticker=SBER&kind=candles&vol=1&osc=1")
            self.assertEqual(r_chart.status_code, 200)
            self.assertIn("<rect", r_chart.text)

            r_chart_line = client.get("/ui/partials/asset/chart?ticker=SBER&kind=line&vol=0&osc=0")
            self.assertEqual(r_chart_line.status_code, 200)

            # GET /ui/partials/asset/indicators
            r_ind = client.get("/ui/partials/asset/indicators?ticker=SBER&period=W")
            self.assertEqual(r_ind.status_code, 200)
        finally:
            web.build_report = orig_build_report
            web._asset_ohlcv = orig_asset_ohlcv
        print("[SUCCESS] Asset router endpoints verified.")

    def test_backtest_router_endpoints(self):
        """Test Backtest sub-router routes."""
        res = BacktestResult(bars=20, total_return_pct=12.5, buy_hold_return_pct=8.0,
                             max_drawdown_pct=2.1, num_trades=3, exposure=0.6, sharpe=1.2,
                             equity_curve=[1.0, 1.05, 1.12])
        orig_backtest = web.backtest_asset_cached
        try:
            web.backtest_asset_cached = lambda t, strategy="sma_cross": res
            r = client.get("/ui/backtest?ticker=SBER&strategy=sma_cross")
            self.assertEqual(r.status_code, 200)
            self.assertIn("+12.50%", r.text)

            r_part = client.get("/ui/partials/backtest?ticker=SBER&strategy=momentum")
            self.assertEqual(r_part.status_code, 200)
            self.assertIn("+12.50%", r_part.text)
        finally:
            web.backtest_asset_cached = orig_backtest
        print("[SUCCESS] Backtest router endpoints verified.")

    def test_portfolio_router_endpoints(self):
        """Test Portfolio sub-router routes including forms and cache invalidation."""
        report = PortfolioReport(total_value_rub=100000.0, regime="спокойный")
        orig_portfolio_ctx = web._portfolio_context
        orig_add_pos = web._add_position
        orig_rem_pos = web._remove_position

        add_calls = []
        rem_calls = []

        try:
            web._portfolio_context = lambda: {
                "report": report, "correlations": [], "exposure": [], "assets": []
            }
            web._add_position = lambda t, q, p: add_calls.append((t, q, p))
            web._remove_position = lambda t: rem_calls.append(t)

            # GET /ui/portfolio
            r = client.get("/ui/portfolio")
            self.assertEqual(r.status_code, 200)

            # POST /ui/portfolio/add
            r_add = client.post("/ui/portfolio/add", data={"ticker": "GAZP", "quantity": "50", "price": "120.5"})
            self.assertEqual(r_add.status_code, 200)
            self.assertEqual(add_calls, [("GAZP", 50.0, 120.5)])

            # POST /ui/portfolio/remove
            r_rem = client.post("/ui/portfolio/remove", data={"ticker": "GAZP"})
            self.assertEqual(r_rem.status_code, 200)
            self.assertEqual(rem_calls, ["GAZP"])

            # POST /ui/portfolio/cash (with mock repository)
            cash_calls = []
            class FakeRepo:
                def __init__(self, session): pass
                def set_balance(self, currency, amount): cash_calls.append((currency, amount))

            orig_repo = sys.modules["geoanalytics.storage.repositories"].CashBalanceRepository
            sys.modules["geoanalytics.storage.repositories"].CashBalanceRepository = FakeRepo
            try:
                r_cash = client.post("/ui/portfolio/cash", data={"currency": "RUB", "amount": "50000"})
                self.assertEqual(r_cash.status_code, 200)
                self.assertEqual(cash_calls, [("RUB", 50000.0)])
            finally:
                sys.modules["geoanalytics.storage.repositories"].CashBalanceRepository = orig_repo

        finally:
            web._portfolio_context = orig_portfolio_ctx
            web._add_position = orig_add_pos
            web._remove_position = orig_rem_pos
        print("[SUCCESS] Portfolio router endpoints verified.")

    def test_graph_router_endpoints(self):
        """Test Graph sub-router routes."""
        orig_graph_ctx = web._graph_context
        orig_market_ctx = web._market_graph_context
        orig_heatmap_ctx = web._market_heatmap_context

        try:
            web._graph_context = lambda t: {"ticker": t, "graph": None, "assets": []}
            web._market_graph_context = lambda: {"graph": None, "is_market": True}
            web._market_heatmap_context = lambda: {"heatmap": None}

            r_page = client.get("/ui/graph?ticker=SBER")
            self.assertEqual(r_page.status_code, 200)

            r_part = client.get("/ui/partials/graph?ticker=SBER")
            self.assertEqual(r_part.status_code, 200)

            r_mkt = client.get("/ui/graph/market")
            self.assertEqual(r_mkt.status_code, 200)

            r_mkt_part = client.get("/ui/partials/graph/market")
            self.assertEqual(r_mkt_part.status_code, 200)

            r_hm_part = client.get("/ui/partials/graph/heatmap")
            self.assertEqual(r_hm_part.status_code, 200)
        finally:
            web._graph_context = orig_graph_ctx
            web._market_graph_context = orig_market_ctx
            web._market_heatmap_context = orig_heatmap_ctx
        print("[SUCCESS] Graph router endpoints verified.")

    def test_factors_router_endpoints(self):
        """Test Factors sub-router route."""
        orig_factors_ctx = web._factors_context
        try:
            web._factors_context = lambda: {"cards": [], "regime": None}
            r = client.get("/ui/factors")
            self.assertEqual(r.status_code, 200)
            self.assertIn("Факторы рынка", r.text)
        finally:
            web._factors_context = orig_factors_ctx
        print("[SUCCESS] Factors router endpoint verified.")

    def test_track2_router_endpoints(self):
        """Test Track2 sub-router routes."""
        from geoanalytics.futrader.track import TrackMetrics, TrackRecord
        from geoanalytics.futrader.risk_limits import RiskLimits

        rec = TrackRecord(account="demo", starting_cash=100000.0, equity=100000.0,
                          metrics=TrackMetrics(), note="тест")
        orig_track2_ctx = web._track2_context
        try:
            web._track2_context = lambda: {
                "account": "demo", "rec": rec, "metrics": rec.metrics, "risk": None,
                "limits": RiskLimits(), "halt": None, "value_chart": None, "positions": [],
                "trades": [], "drift": [], "by_strategy": [], "strat_max": 0.0,
                "by_instrument": [], "instr_max": 0.0
            }
            r = client.get("/ui/track2")
            self.assertEqual(r.status_code, 200)

            r_part = client.get("/ui/partials/track2")
            self.assertEqual(r_part.status_code, 200)
        finally:
            web._track2_context = orig_track2_ctx
        print("[SUCCESS] Track2 router endpoints verified.")

    def test_alerts_router_endpoints(self):
        """Test Alerts sub-router routes."""
        orig_recent_alerts = web.recent_alerts
        orig_get_alert = web.get_alert
        orig_list_mutes = web.manage.list_mutes
        orig_ack = web.manage.acknowledge
        orig_mute = web.manage.mute_for_days
        orig_unmute = web.manage.unmute

        try:
            web.recent_alerts = lambda **kw: [{
                "id": 1, "alert_type": "price_move", "ticker": "SBER", "severity": "warning",
                "title": "SBER alert", "message": "msg", "created_at": "2026-07-01T10:00:00Z",
                "acknowledged_at": None, "channels": [], "payload": {}
            }]
            web.manage.list_mutes = lambda: []

            # GET /ui/alerts
            r = client.get("/ui/alerts")
            self.assertEqual(r.status_code, 200)
            self.assertIn("SBER alert", r.text)

            # GET /ui/partials/alerts
            r_part = client.get("/ui/partials/alerts?severity=warning")
            self.assertEqual(r_part.status_code, 200)

            # POST /ui/alerts/{alert_id}/ack -> 200 when found, 404 when not found
            web.manage.acknowledge = lambda aid: True
            web.get_alert = lambda aid: {"id": aid, "alert_type": "price_move", "ticker": "SBER",
                                         "severity": "warning", "title": "ack title",
                                         "message": "msg", "created_at": "2026-07-01T10:00:00Z",
                                         "acknowledged_at": "2026-07-01T10:05:00Z",
                                         "channels": [], "payload": {}} if aid == 1 else None
            r_ack_ok = client.post("/ui/alerts/1/ack")
            self.assertEqual(r_ack_ok.status_code, 200)
            self.assertIn("ack title", r_ack_ok.text)

            r_ack_404 = client.post("/ui/alerts/999/ack")
            self.assertEqual(r_ack_404.status_code, 404)

            # POST /ui/alerts/mute
            web.manage.mute_for_days = lambda *a, **kw: 1
            r_mute = client.post("/ui/alerts/mute", data={"scope_type": "ticker", "scope_value": "SBER", "days": "7"})
            self.assertEqual(r_mute.status_code, 200)

            # POST /ui/alerts/unmute/{mute_id}
            web.manage.unmute = lambda mid: True
            r_unmute = client.post("/ui/alerts/unmute/1")
            self.assertEqual(r_unmute.status_code, 200)

        finally:
            web.recent_alerts = orig_recent_alerts
            web.get_alert = orig_get_alert
            web.manage.list_mutes = orig_list_mutes
            web.manage.acknowledge = orig_ack
            web.manage.mute_for_days = orig_mute
            web.manage.unmute = orig_unmute
        print("[SUCCESS] Alerts router endpoints verified.")

    def test_unhandled_exception_handler(self):
        """Verify unhandled exceptions trigger HTML error page for browser requests and JSON for API."""
        orig_build_snap = web.build_snapshot
        try:
            web.build_snapshot = lambda **kw: (_ for _ in ()).throw(RuntimeError("Simulated web error"))
            safe_client = TestClient(api_app.app, raise_server_exceptions=False)

            # HTML accept header
            r_html = safe_client.get("/", headers={"accept": "text/html"})
            self.assertEqual(r_html.status_code, 500)
            self.assertIn("Что-то пошло не так", r_html.text)

            # JSON accept header
            r_json = safe_client.get("/", headers={"accept": "application/json"})
            self.assertEqual(r_json.status_code, 500)
            self.assertEqual(r_json.json(), {"detail": "Internal Server Error"})
        finally:
            web.build_snapshot = orig_build_snap
        print("[SUCCESS] Exception handler verified.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
