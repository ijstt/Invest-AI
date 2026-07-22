# Handoff Report — Explorer 3 (Milestone 4: Web API Modularization)

## 1. Observation
- **Baseline Pytest Suite**: `source .venv/bin/activate && pytest tests/` executed cleanly with 1228 passed tests, 0 failures, 2 warnings in 21.18s.
- **REST JSON API Endpoints (`src/geoanalytics/api/app.py`)**: 8 endpoints tested in `tests/test_api.py` (`/health`, `/sources`, `/assets`, `/news`, `/asset/{ticker}`, `/backtest/{ticker}`, `/events`, `/alerts`).
- **HTMX / HTML Dashboard Routes (`src/geoanalytics/api/web.py`)**: 27 routes tested in `tests/test_web.py` and `tests/test_web_adversarial.py` across dashboard, asset, backtest, portfolio, graph, factors, track2, and alerts domains.
- **Test Monkeypatching**: `tests/test_web.py` relies on `monkeypatch.setattr(web, "<func>", mock)` for functions like `build_snapshot`, `build_report`, `_asset_ohlcv`, `_portfolio_context`, `_add_position`, `_remove_position`, `_indicators_context`, `backtest_asset_cached`, `_track2_context`, `recent_headlines`, `recent_alerts`, `get_alert`, `_graph_context`, `_market_graph_context`, `ask_answer`.
- **Direct Function Imports**: `tests/test_regime_history.py:7` directly imports `from geoanalytics.api.web import _regime_strip`. `tests/test_web.py` directly references `web._cache`, `web._cached`, `web._invalidate_cache`, `web._attr_rows`.
- **Raspberry Pi Integration**:
  - `deploy/pi/geo-dashboard.service` line 15 executes `%h/News/.venv/bin/geo serve --host 0.0.0.0 --port 8800`.
  - `geo serve` in `src/geoanalytics/cli.py` line 2686 invokes `uvicorn.run("geoanalytics.api.app:app", host=host, port=port)`.
  - `src/geoanalytics/api/app.py` line 46 includes `web.router` (`app.include_router(web.router)`).
  - Executing `./geo-ctl.sh status` verified live Pi deployment: `geo-dashboard`, `geo-futrader`, `geo-depth` are active, and `http://192.168.0.114:8800/health` returned `{"status":"ok","sources":11}`.

## 2. Logic Chain
1. Baseline test suite execution confirms 100% pass rate (1,228 passed tests) prior to modularization.
2. Tracing test invocations shows that `test_web.py` patches context builders and query wrappers on the `web` module object (`geoanalytics.api.web`).
3. Therefore, for sub-router extraction to preserve test pass rate without breaking monkeypatches:
   - Sub-routers must call shared helpers via `web.<func>(...)`.
   - `web.py` must retain re-exports/aliases for all shared constants, cache methods, and data/context functions.
4. Tracing Raspberry Pi integration scripts shows that `geo serve` launches FastAPI (`app.py`), which includes `web.router`. As long as `web.router` includes all 8 sub-routers (`router.include_router(...)`), all HTTP endpoint contracts remain identical for Pi clients and LAN users.
5. Decomposing `web.py` (1,034 lines) into 8 sub-routers in `src/geoanalytics/api/routers/` (ranging from 50 to 210 lines) and an assembler `web.py` (~120 lines) satisfies the strict requirement that no file exceeds 600 lines.

## 3. Caveats
- No code changes were made to source files during this investigation (read-only investigation per role guidelines).
- Raspberry Pi integration tests rely on local network connectivity; offline or firewalled environments will receive fallback HTML/JSON error responses, which are correctly handled by `_unhandled_exception` in `app.py`.

## 4. Conclusion
Refactoring `src/geoanalytics/api/web.py` into 8 sub-routers in `src/geoanalytics/api/routers/` is safe, fully specified, and ready for implementation by worker agents. Re-exporting helper symbols in `web.py` ensures 100% test compatibility for `test_web.py`, while `web.router` inclusion in `app.py` guarantees seamless Raspberry Pi execution.

## 5. Verification Method
- **Pytest Test Suite**:
  Run `source .venv/bin/activate && pytest tests/` to verify all 1228 tests pass.
- **Line Count Verification**:
  Run `wc -l src/geoanalytics/api/web.py src/geoanalytics/api/routers/*.py` to confirm no file exceeds 600 lines.
- **Raspberry Pi Health Check**:
  Run `./geo-ctl.sh status` to verify `/health` returns `{"status":"ok", ...}` and Pi services remain active.
