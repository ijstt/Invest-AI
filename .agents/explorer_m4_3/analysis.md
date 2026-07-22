# Web API Test Coverage & Raspberry Pi Integration Analysis (Milestone 4)

## Executive Summary
This analysis investigates test coverage and Raspberry Pi integration relative to `src/geoanalytics/api/web.py` and `src/geoanalytics/api/routers/` for Milestone 4 (Web API Modularization). Baseline testing confirms **1228 passed tests** in the test suite (`pytest tests/`). System status check via `./geo-ctl.sh status` confirms active Raspberry Pi services, with the web API responding `{"status":"ok","sources":11}` at `http://192.168.0.114:8800/health`. 

Refactoring `web.py` into 8 modular sub-routers in `src/geoanalytics/api/routers/` is safe and fully achievable provided strict boundary conditions—specifically re-exporting symbols on `geoanalytics.api.web` for `test_web.py` monkeypatch compatibility—are maintained.

---

## 1. Baseline Test Suite Verification
- **Execution Command**: `source .venv/bin/activate && pytest tests/`
- **Result**: `1228 passed, 2 warnings in 21.18s` (Exit Code: 0)
- **Warnings**:
  - `StarletteDeprecationWarning`: `httpx` with `starlette.testclient` (FastAPI test client warning)
  - `UserWarning`: `pkg_resources` deprecation from `pymorphy2`
- **Test Health**: 100% pass rate across 96 test files.

---

## 2. API Endpoint Trace & Test Coverage Matrix

### 2.1 JSON REST API Endpoints (`src/geoanalytics/api/app.py`)
Tested via `tests/test_api.py`:

| Endpoint Path | Method | Query / Path Parameters | Expected Response / Model | Status Codes | Test Function in `tests/test_api.py` |
|---------------|--------|--------------------------|---------------------------|--------------|--------------------------------------|
| `/health` | GET | None | `HealthResponse` (`status="ok"`, `sources>=9`) | 200 OK | `test_health`, `test_no_db_routes_ok` |
| `/sources` | GET | None | `list[SourceInfo]` (includes interfax, rbc, fred, ecb, moex) | 200 OK | `test_sources`, `test_no_db_routes_ok` |
| `/assets` | GET | None | `list[AssetInfo]` | 200 OK | `test_assets_endpoint` (in `test_web.py`) |
| `/news` | GET | `hours: int = 24`, `use_llm: bool = False` | `NewsResponse` (`key_rate`, `fx`, `top_events`, `headlines`) | 200 OK | `test_news` |
| `/asset/{ticker}` | GET | `ticker: str` (path), `rebuild: bool = False`, `use_llm: bool = False` | `AssetResponse` | 200 OK, 404 Not Found | `test_asset_found`, `test_asset_not_found` |
| `/backtest/{ticker}` | GET | `ticker: str` (path), `strategy: str = "sma_cross"` | `BacktestResponse` | 200 OK, 400 Bad Request (invalid strategy), 404 Not Found (asset not found) | `test_backtest_ok`, `test_backtest_unknown_strategy`, `test_backtest_asset_not_found` |
| `/events` | GET | `hours: int = 168`, `limit: int = 20` | `list[EventResponse]` | 200 OK | `test_events` |
| `/alerts` | GET | `hours: int = 168`, `limit: int = 50` | `list[AlertResponse]` | 200 OK | `test_alerts` |

### 2.2 Web HTMX / HTML Dashboard Routes (`src/geoanalytics/api/web.py` -> `routers/`)
Tested via `tests/test_web.py` & `tests/test_web_adversarial.py`:

| Target Router | Route Path | Method | Parameters | Expected Response / Template | Status Codes | Test Functions |
|---------------|------------|--------|------------|------------------------------|--------------|----------------|
| `dashboard.py` | `/` | GET | `hours: int = 24` | HTML (`dashboard.html`), includes `snap`, `_status_context`, `_pulse_context` | 200 OK (500 HTML on unhandled exc) | `test_dashboard`, `test_unhandled_exception_returns_html_500` |
| `dashboard.py` | `/ui/partials/status` | GET | None | HTML (`_status.html`) | 200 OK | `test_dashboard` |
| `dashboard.py` | `/ui/partials/news` | GET | `hours: int = 24`, `limit: int = 15` | HTML (`_news_feed.html`) | 200 OK | `test_news_partial` |
| `dashboard.py` | `/ui/partials/ask` | GET | `q: str = ""` | HTML (`_ask_result.html` or muted string if empty) | 200 OK | `test_ask_partial_empty`, `test_ask_partial_renders_result` |
| `asset.py` | `/ui/asset` | GET | `ticker: str | None = None` | HTML (`asset.html`), defaults to `IMOEX` if empty, datalist asset list | 200 OK | `test_asset_page`, `test_asset_page_shows_graph_panel`, `test_asset_form_has_datalist` |
| `asset.py` | `/ui/partials/asset` | GET | `ticker: str = ""` | HTML (`_asset_result.html` or empty msg) | 200 OK | `test_asset_partial_empty_ticker`, `test_asset_partial_edge_cases` |
| `asset.py` | `/ui/partials/asset/chart` | GET | `ticker`, `range`, `period`, `kind`, `ovl`, `vol`, `osc` | HTML (`_asset_chart.html`), SVG `<polyline>` or `<rect>` | 200 OK | `test_asset_chart_partial_candles`, `test_chart_indicator_toggles`, `test_asset_chart_partial_edge_cases` |
| `asset.py` | `/ui/partials/asset/indicators` | GET | `ticker: str = ""`, `period: str = "D"` | HTML (`_indicators.html`), RSI(14) table & D/W/M toggle | 200 OK | `test_indicators_partial_period_toggle`, `test_indicators_partial_empty_ticker` |
| `backtest.py` | `/ui/backtest` | GET | `ticker: str = "SBER"`, `strategy: str = "sma_cross"` | HTML (`backtest.html`), equity curve, strategy options | 200 OK | `test_backtest_page`, `test_backtest_form_lists_strategies` |
| `backtest.py` | `/ui/partials/backtest` | GET | `ticker: str = "SBER"`, `strategy: str = "sma_cross"` | HTML (`_backtest_result.html` or error string) | 200 OK | `test_backtest_partial_error` |
| `portfolio.py` | `/ui/portfolio` | GET | None | HTML (`portfolio.html`), positions, risk contrib, correlations | 200 OK | `test_portfolio_page_empty`, `test_portfolio_page_with_positions`, `test_portfolio_page_quality_panels`, `test_portfolio_cash_row_delete_targets_cash_endpoint` |
| `portfolio.py` | `/ui/portfolio/add` | POST | Form: `ticker`, `quantity`, `price` | HTML (`portfolio.html`), swallows bad input / 422 on invalid schema | 200 OK, 422 | `test_portfolio_add_form`, `test_portfolio_add_form_swallows_bad_input`, `test_portfolio_actions_swallow_failures` |
| `portfolio.py` | `/ui/portfolio/remove` | POST | Form: `ticker` | HTML (`portfolio.html`) | 200 OK | `test_portfolio_remove_form` |
| `portfolio.py` | `/ui/portfolio/cash` | POST | Form: `currency`, `amount` | HTML (`portfolio.html`) | 200 OK | `test_portfolio_cash_form_zero_amount_removes_balance` |
| `graph.py` | `/ui/graph` | GET | `ticker: str = "SBER"` | HTML (`graph.html`), radial tree | 200 OK | `test_graph_page_renders_tree` |
| `graph.py` | `/ui/partials/graph` | GET | `ticker: str = "SBER"` | HTML (`_graph.html`), SVG | 200 OK | `test_graph_partial_returns_svg` |
| `graph.py` | `/ui/graph/market` | GET | None | HTML (`graph.html`), market radial layout | 200 OK | `test_market_graph_page` |
| `graph.py` | `/ui/partials/graph/market` | GET | None | HTML (`_graph.html`), SVG | 200 OK | `test_market_graph_partial` |
| `graph.py` | `/ui/partials/graph/heatmap` | GET | None | HTML (`_heatmap.html`) | 200 OK | Tested via `test_web.py` / `test_charts.py` |
| `factors.py` | `/ui/factors` | GET | None | HTML (`factors.html`), cards for Brent, USD/RUB, etc. | 200 OK | `test_factors_page` |
| `track2.py` | `/ui/track2` | GET | `account: str = "demo"` | HTML (`track2.html`), paper account metrics, equity curve | 200 OK | `test_track2_page`, `test_track2_page_empty`, `test_track2_template_missing_fields` |
| `track2.py` | `/ui/partials/track2` | GET | `account: str = "demo"` | HTML (`_track2.html`), kill-switch banner on halt | 200 OK | `test_track2_partial_halted` |
| `alerts.py` | `/ui/alerts` | GET | `severity`, `alert_type`, `only_unacked` | HTML (`alerts.html`), alert feed, mute panel | 200 OK | `test_alerts_page` |
| `alerts.py` | `/ui/partials/alerts` | GET | `severity`, `alert_type`, `only_unacked` | HTML (`_alerts_feed.html`) | 200 OK | `test_alerts_partial_filtered` |
| `alerts.py` | `/ui/alerts/{alert_id}/ack` | POST | Path: `alert_id: int` | HTML (`_alert_row.html`) | 200 OK | `test_alert_ack_swaps_row` |
| `alerts.py` | `/ui/alerts/mute` | POST | Form: `scope_type`, `scope_value`, `days`, `reason` | HTML (`_mutes_panel.html`) | 200 OK | `test_alert_mute_renders_panel` |
| `alerts.py` | `/ui/alerts/unmute/{mute_id}` | POST | Path: `mute_id: int` | HTML (`_mutes_panel.html`) | 200 OK | `test_alert_unmute_renders_panel` |

---

## 3. Monkeypatch & Test Dependency Analysis
`tests/test_web.py` relies heavily on `monkeypatch.setattr(web, "<symbol>", ...)` to mock data functions and context builders without requiring a live database.

To preserve 100% test compatibility:
1. `geoanalytics.api.web` MUST expose and re-export all shared context builders, query wrappers, data fetchers, and constants.
2. Routers in `src/geoanalytics/api/routers/*.py` must call context functions via `web.<func>(...)` (e.g. `web.build_snapshot(...)`, `web._asset_ohlcv(...)`, `web._portfolio_context()`).
3. External test imports:
   - `tests/test_regime_history.py:7` directly imports `from geoanalytics.api.web import _regime_strip`.
   - `tests/test_web.py` uses `web._cache`, `web._cached`, `web._invalidate_cache`, `web._attr_rows`.

---

## 4. Raspberry Pi Integration & Deployment Verification

### 4.1 Deployment Inspection
- **Service File**: `deploy/pi/geo-dashboard.service`
- **Command Executed**: `%h/News/.venv/bin/geo serve --host 0.0.0.0 --port 8800`
- **EntryPoint Dispatcher**: `geo serve` in `src/geoanalytics/cli.py` invokes `uvicorn.run("geoanalytics.api.app:app", host=host, port=port)`.
- **FastAPI App Assembly**: `src/geoanalytics/api/app.py` includes `web.router` via `app.include_router(web.router)`.
- **System Health Probing**: `./geo-ctl.sh status` executes `curl -s --noproxy "*" --max-time 3 "http://${DB_HOST}:${PORT}/health"`.

### 4.2 Live Verification Output
Running `./geo-ctl.sh status` against the current environment returned:
```
=== Контейнеры ===
NAME         IMAGE                  COMMAND               SERVICE   CREATED       STATUS      PORTS
geo-ollama   ollama/ollama:latest   "/bin/ollama serve"   ollama    4 weeks ago   Up 3 days   0.0.0.0:11434->11434/tcp, [::]:11434->11434/tcp
=== Службы ===
  geo-alerts      inactive
  geo-bot         active
=== Дашборд /health (на Pi) ===
{"status":"ok","sources":11}
=== Pi-службы (futrader/depth/dashboard) ===
  geo-futrader    active
  geo-depth       active
  geo-dashboard   active
```
- Raspberry Pi `geo-dashboard.service` is actively running and returning status 200 OK on `/health`.

---

## 5. Refactoring Boundary Conditions & Router Breakdown Recommendations

### 5.1 Modular Router Breakdown Plan
`src/geoanalytics/api/web.py` (currently 1,034 lines) should be reduced to ~120 lines as an app assembler and re-exporter, delegating endpoint handlers to 8 sub-routers in `src/geoanalytics/api/routers/`:

1. `web.py` (~120 lines): Assembler, Jinja `templates` instance, TTL cache engine (`_cache`, `_cached`, `_invalidate_cache`), shared UI constants (`_STRATEGIES`, `_ALERT_TYPES`, `_SEVERITIES`, `_CHART_RANGES`), router aggregator (`router.include_router(...)`), and re-exports of helper functions.
2. `routers/dashboard.py` (~90 lines): `/`, `/ui/partials/status`, `/ui/partials/news`, `/ui/partials/ask`.
3. `routers/asset.py` (~210 lines): `/ui/asset`, `/ui/partials/asset`, `/ui/partials/asset/chart`, `/ui/partials/asset/indicators`.
4. `routers/backtest.py` (~50 lines): `/ui/backtest`, `/ui/partials/backtest`.
5. `routers/portfolio.py` (~140 lines): `/ui/portfolio`, `/ui/portfolio/add`, `/ui/portfolio/remove`, `/ui/portfolio/cash`.
6. `routers/graph.py` (~200 lines): `/ui/graph`, `/ui/partials/graph`, `/ui/graph/market`, `/ui/partials/graph/market`, `/ui/partials/graph/heatmap`.
7. `routers/factors.py` (~60 lines): `/ui/factors`.
8. `routers/track2.py` (~160 lines): `/ui/track2`, `/ui/partials/track2`.
9. `routers/alerts.py` (~80 lines): `/ui/alerts`, `/ui/partials/alerts`, `/ui/alerts/{alert_id}/ack`, `/ui/alerts/mute`, `/ui/alerts/unmute/{mute_id}`.

### 5.2 Strict Boundary Conditions for Implementation
- **File Length Limit**: Every single file MUST remain under 600 lines (largest target router is ~210 lines).
- **Zero API Invalidation**: Endpoint paths, HTTP methods, status codes, query parameters, form field names, and template names must remain strictly unchanged.
- **Test Monkeypatching Compatibility**: Sub-routers MUST invoke data/context helpers as `web.<func>(...)` so that `monkeypatch.setattr(web, "<func>", mock)` in `test_web.py` works seamlessly.
- **Re-exports**: `web.py` MUST re-export functions like `_regime_strip`, `_attr_rows`, `_asset_ohlcv`, `_sentiment_cells`, `build_snapshot`, `build_report`, `list_assets`, `recent_headlines`, `ask_answer`, `manage`.
- **Raspberry Pi Compatibility**: `app.py` must continue to include `web.router` (which aggregates all sub-routers).
