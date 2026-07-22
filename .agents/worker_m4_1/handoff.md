# Handoff Report — Milestone 4: Web API Modularization

## 1. Observation
- `src/geoanalytics/api/web.py` was previously a monolithic 1,034-line file containing 27 HTMX/Jinja endpoint routes, helper context functions, and cache logic.
- Extracted endpoints and context functions into 8 sub-router files under `src/geoanalytics/api/routers/`:
  - `dashboard.py` (82 lines): `/`, `/ui/partials/status`, `/ui/partials/news`, `/ui/partials/ask`
  - `asset.py` (251 lines): `/ui/asset`, `/ui/partials/asset`, `/ui/partials/asset/chart`, `/ui/partials/asset/indicators`
  - `backtest.py` (42 lines): `/ui/backtest`, `/ui/partials/backtest`
  - `portfolio.py` (135 lines): `/ui/portfolio`, `/ui/portfolio/add`, `/ui/portfolio/remove`, `/ui/portfolio/cash`
  - `graph.py` (259 lines): `/ui/graph`, `/ui/partials/graph`, `/ui/graph/market`, `/ui/partials/graph/market`, `/ui/partials/graph/heatmap`
  - `factors.py` (62 lines): `/ui/factors`
  - `track2.py` (157 lines): `/ui/track2`, `/ui/partials/track2`
  - `alerts.py` (73 lines): `/ui/alerts`, `/ui/partials/alerts`, `/ui/alerts/{alert_id}/ack`, `/ui/alerts/mute`, `/ui/alerts/unmute/{mute_id}`
- Refactored `src/geoanalytics/api/web.py` into a lightweight app assembler (108 lines) that mounts all 8 sub-routers, holds the TTL cache engine and shared UI constants, and re-exports all helper functions and query modules.
- Executed `source .venv/bin/activate && pytest tests/`:
  - Test output: `1228 passed, 2 warnings in 17.42s` (100% pass rate).
- Line counts verified with `wc -l`:
  ```
  108 src/geoanalytics/api/web.py
   73 src/geoanalytics/api/routers/alerts.py
  251 src/geoanalytics/api/routers/asset.py
   42 src/geoanalytics/api/routers/backtest.py
   82 src/geoanalytics/api/routers/dashboard.py
   62 src/geoanalytics/api/routers/factors.py
  259 src/geoanalytics/api/routers/graph.py
    1 src/geoanalytics/api/routers/__init__.py
  135 src/geoanalytics/api/routers/portfolio.py
  157 src/geoanalytics/api/routers/track2.py
  ```
  All files are strictly under the 600-line limit (maximum file is `graph.py` at 259 lines).

## 2. Logic Chain
- `tests/test_web.py` and `tests/test_regime_history.py` monkeypatch attributes directly on `geoanalytics.api.web` (e.g., `web.build_snapshot`, `web._portfolio_context`, `web._asset_ohlcv`, `web._add_position`, `web._regime_strip`).
- By keeping `web.py` as the module that exports and re-exports all context helpers and query functions, and having sub-router handlers invoke functions via dynamic module access (`web.<function>`), monkeypatches applied by test fixtures intercept calls seamlessly without needing any changes to test files.
- Mount point compatibility is preserved because `web.router` includes all sub-routers, maintaining identical path signatures and HTTP methods for `app.py` mounting (`app.include_router(web.router)`).

## 3. Caveats
- No caveats. All 27 endpoints were verified, all existing comments/docstrings preserved, and no test files were modified.

## 4. Conclusion
- Milestone 4: Web API Modularization is complete. `src/geoanalytics/api/web.py` has been refactored from 1,034 lines to 108 lines, with all 27 endpoints clean, modularized, and fully tested across 8 sub-router files under 600 lines each.

## 5. Verification Method
1. Run pytest test suite:
   ```bash
   source .venv/bin/activate && pytest tests/
   ```
   Expected result: 1,228 passed.
2. Verify line counts:
   ```bash
   wc -l src/geoanalytics/api/web.py src/geoanalytics/api/routers/*.py
   ```
   Expected result: All files < 600 lines.
