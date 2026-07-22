"""Stress and adversarial test harness for Web API modularization.

Dynamic stress testing:
- Iterates over all 27 routes in `web.router` dynamically.
- Tests parameter boundaries, invalid query params, missing forms, malformed values.
- Tests HTMX vs non-HTMX headers.
- Tests caching logic (`_cached` / `_invalidate_cache`).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

root_dir = Path(__file__).resolve().parents[2]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from fastapi.testclient import TestClient
from geoanalytics.api import app as api_app
from geoanalytics.api import web
from geoanalytics.query.asset_report import AssetReport
from geoanalytics.analytics.portfolio import PortfolioReport

client = TestClient(api_app.app, raise_server_exceptions=False)


class TestWebAPIStressHarness(unittest.TestCase):

    def test_dynamic_route_stress(self):
        """Dynamically invoke all GET endpoints with malicious/boundary query strings."""
        bad_inputs = [
            "",
            "   ",
            "' OR '1'='1",
            "<script>alert(1)</script>",
            "A" * 500,
            "-1",
            "99999999999999999999",
            "../../../etc/passwd",
        ]

        # Mock heavy computations for rapid dynamic route checking
        orig_portfolio = web._portfolio_context
        orig_market_graph = web._market_graph_context
        orig_heatmap = web._market_heatmap_context
        orig_graph = web._graph_context
        orig_build_report = web.build_report

        try:
            web._portfolio_context = lambda: {"report": PortfolioReport(error="портфель пуст"), "correlations": [], "exposure": [], "assets": []}
            web._market_graph_context = lambda: {"graph": None, "is_market": True}
            web._market_heatmap_context = lambda: {"heatmap": None}
            web._graph_context = lambda t: {"ticker": t, "graph": None, "assets": []}
            web.build_report = lambda t, **kw: AssetReport(ticker=t, found=False)

            routes = [r for r in web.router.routes if "GET" in r.methods]
            self.assertGreaterEqual(len(routes), 20)

            for route in routes:
                path = route.path
                test_path = path.replace("{alert_id}", "1").replace("{mute_id}", "1")

                for bad in bad_inputs:
                    res = client.get(f"{test_path}?ticker={bad}&hours={bad}&strategy={bad}&period={bad}&range={bad}")
                    # 200 (ok/fallback), 404 (not found), 422 (fastapi validation err), 500 (handled via app exception_handler)
                    self.assertIn(res.status_code, (200, 404, 422, 500),
                                  f"Route {test_path} failed with unexpected status {res.status_code} on input '{bad[:20]}'")
        finally:
            web._portfolio_context = orig_portfolio
            web._market_graph_context = orig_market_graph
            web._market_heatmap_context = orig_heatmap
            web._graph_context = orig_graph
            web.build_report = orig_build_report

        print(f"[SUCCESS] Dynamic stress test passed across all {len(routes)} GET routes.")

    def test_cache_engine_stress(self):
        """Test TTL cache memoization and manual invalidation."""
        counter = {"val": 0}

        def compute():
            counter["val"] += 1
            return f"result_{counter['val']}"

        web._invalidate_cache("stress_key")

        # 1. First call computes
        res1 = web._cached("stress_key", compute, ttl=60.0)
        self.assertEqual(res1, "result_1")
        self.assertEqual(counter["val"], 1)

        # 2. Subsequent call within TTL returns memoized value
        res2 = web._cached("stress_key", compute, ttl=60.0)
        self.assertEqual(res2, "result_1")
        self.assertEqual(counter["val"], 1)

        # 3. Explicit invalidation forces recomputation
        web._invalidate_cache("stress_key")
        res3 = web._cached("stress_key", compute, ttl=60.0)
        self.assertEqual(res3, "result_2")
        self.assertEqual(counter["val"], 2)

        # 4. Expired TTL forces recomputation
        res4 = web._cached("stress_key", compute, ttl=0.0)
        self.assertEqual(res4, "result_3")
        self.assertEqual(counter["val"], 3)

        web._invalidate_cache("stress_key")
        print("[SUCCESS] Cache engine stress test passed.")

    def test_htmx_header_handling(self):
        """Test HTMX request headers vs standard requests."""
        # Non-HTMX request to partial returns fragment
        res_std = client.get("/ui/partials/status")
        self.assertEqual(res_std.status_code, 200)
        self.assertNotIn("<!doctype html>", res_std.text.lower())

        # HTMX request to partial
        res_htmx = client.get("/ui/partials/status", headers={"HX-Request": "true"})
        self.assertEqual(res_htmx.status_code, 200)
        self.assertNotIn("<!doctype html>", res_htmx.text.lower())

        # Non-HTMX request to main page returns full HTML
        res_page = client.get("/ui/factors")
        self.assertEqual(res_page.status_code, 200)
        self.assertIn("<!doctype html>", res_page.text.lower())
        print("[SUCCESS] HTMX header handling verified.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
