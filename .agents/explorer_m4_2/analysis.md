# Web API Modularization Analysis (Milestone 4)

**Agent**: Explorer 2 (`explorer_m4_2`)  
**Date**: 2026-07-22  
**Target Subsystem**: `src/geoanalytics/api/web.py` and `src/geoanalytics/api/routers/`  

---

## 1. Baseline Test Verification

Baseline test suite execution was performed via:
```bash
source .venv/bin/activate && pytest tests/
```
**Results**:
- **Passed**: 1,228 tests
- **Failed**: 0
- **Duration**: 21.54 seconds
- **Status**: 100% PASS RATE

---

## 2. Catalog of `src/geoanalytics/api/web.py`

### 2.1 File Size & Summary
- **Location**: `src/geoanalytics/api/web.py`
- **Total Lines**: 1,034 lines
- **Primary Role**: FastAPI web router providing HTMX + Jinja2 server-rendered pages and HTMX partial endpoints for the market dashboard, asset reports, portfolio tracking, impact graphs, paper trading (Track2), factors, backtesting, and alerts.

### 2.2 Global State & Constants
1. `templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))`: Jinja2 template loader.
2. `router = APIRouter()`: Main FastAPI router instance included by `app.py`.
3. `_STRATEGIES = [*PRICE_STRATEGIES, "sentiment"]`: Backtest strategy list.
4. `_ALERT_TYPES`: List of alert types (`price_move`, `neg_spike`, `new_event`, `technical`, `combo`, `calendar`, `portfolio`).
5. `_SEVERITIES`: Severity levels (`info`, `warning`, `critical`).
6. `_CHART_RANGES`: Zoom ranges dictionary `{"1m": 31, "3m": 93, "6m": 186, "1y": 372, "max": None}`.
7. `_CACHE_TTL_SEC = 60.0`: Default cache TTL in seconds.
8. `_cache: dict[str, tuple[float, object]]`: Global in-memory TTL cache dictionary.
9. `_FACTOR_CSS`, `_FACTOR_RU`, `_EVENT_AGG`: Formatting mappings for factors and graph events.
10. `_TRACK2_ACCOUNT = "demo"`: Paper trading account identifier.

### 2.3 Cache & Helper Functions
1. `_cached(key, fn, ttl)`: Monotonic time-based TTL memoization.
2. `_invalidate_cache(key)`: Purges cache key upon mutations (e.g. portfolio updates).
3. `_asset_ohlcv(ticker, days)`: Fetches OHLCV series for asset.
4. `_sentiment_cells(ticker, days)`: Calculates daily average sentiment scores over N days.
5. `_price_overlays(closes)`: Computes SMA20/50/200 and Bollinger Bands overlay datasets.
6. `_chart_event_markers(ticker, bar_dates, limit)`: Maps event impact dates to chart bar indices.
7. `_chart_context(ticker, rng, period, kind, ovl, vol, osc)`: Builds full chart rendering context.
8. `_indicators_context(ticker, period)`: Computes technical indicators for timeframes D/W/M.
9. `_asset_context(ticker)`: Assembles full asset page context.
10. `_factor_trend(ticker)`: Calculates composite factor z-score sparkline.
11. `_backtest_context(ticker, strategy)`: Assembles backtest execution context.
12. `_portfolio_context()`: Builds portfolio report, stance, charts, allocation treemap/pie, and risk data.
13. `_compute_portfolio_stance(report)`: Computes medium-term stance (weekly timeframe).
14. `_add_position(ticker, quantity, price)`: Helper to upsert portfolio position.
15. `_remove_position(ticker)`: Helper to delete portfolio position.
16. `_compute_portfolio_report()`: Calls `live_portfolio_report` to generate live intraday report.
17. `_graph_context(ticker)`: Builds asset impact radial tree context.
18. `_market_graph_context()`: Builds market-wide IMOEX sector tree context.
19. `_market_heatmap_context()`: Builds Finviz-style market heatmap context.
20. `_factors_context()`: Builds commodity/FX factor series context.
21. `_regime_strip(rows, width, height)`: Generates market regime history color strip.
22. `_attr_rows(by)`: Prepares P&L attribution rows.
23. `_track2_context()`: Builds paper trading track-record context.
24. `_status_context()`: Pipeline ingestion freshness context.
25. `_pulse_context()`: Market sentiment pulse line context.
26. `_alerts_context(...)`: Filtered alert feed context.

### 2.4 Complete Catalog of Endpoints (27 Total)

| # | HTTP Method | Path | Function | Response Type | Category |
|---|-------------|------|----------|---------------|----------|
| 1 | GET | `/` | `dashboard` | `HTMLResponse` | Dashboard |
| 2 | GET | `/ui/partials/status` | `status_partial` | `HTMLResponse` | Dashboard |
| 3 | GET | `/ui/partials/news` | `news_partial` | `HTMLResponse` | Dashboard |
| 4 | GET | `/ui/partials/ask` | `ask_partial` | `HTMLResponse` | Dashboard |
| 5 | GET | `/ui/asset` | `asset_page` | `HTMLResponse` | Asset |
| 6 | GET | `/ui/partials/asset` | `asset_partial` | `HTMLResponse` | Asset |
| 7 | GET | `/ui/partials/asset/chart` | `asset_chart_partial` | `HTMLResponse` | Asset |
| 8 | GET | `/ui/partials/asset/indicators` | `asset_indicators_partial` | `HTMLResponse` | Asset |
| 9 | GET | `/ui/portfolio` | `portfolio_page` | `HTMLResponse` | Portfolio |
| 10 | POST | `/ui/portfolio/add` | `portfolio_add` | `HTMLResponse` | Portfolio |
| 11 | POST | `/ui/portfolio/remove` | `portfolio_remove` | `HTMLResponse` | Portfolio |
| 12 | POST | `/ui/portfolio/cash` | `portfolio_cash` | `HTMLResponse` | Portfolio |
| 13 | GET | `/ui/graph` | `graph_page` | `HTMLResponse` | Graph |
| 14 | GET | `/ui/partials/graph` | `graph_partial` | `HTMLResponse` | Graph |
| 15 | GET | `/ui/graph/market` | `market_graph_page` | `HTMLResponse` | Graph / Market |
| 16 | GET | `/ui/partials/graph/market` | `market_graph_partial` | `HTMLResponse` | Graph / Market |
| 17 | GET | `/ui/partials/graph/heatmap` | `market_heatmap_partial` | `HTMLResponse` | Graph / Market |
| 18 | GET | `/ui/factors` | `factors_page` | `HTMLResponse` | Factors |
| 19 | GET | `/ui/backtest` | `backtest_page` | `HTMLResponse` | Backtest |
| 20 | GET | `/ui/partials/backtest` | `backtest_partial` | `HTMLResponse` | Backtest |
| 21 | GET | `/ui/track2` | `track2_page` | `HTMLResponse` | Track2 |
| 22 | GET | `/ui/partials/track2` | `track2_partial` | `HTMLResponse` | Track2 |
| 23 | GET | `/ui/alerts` | `alerts_page` | `HTMLResponse` | Alerts |
| 24 | GET | `/ui/partials/alerts` | `alerts_partial` | `HTMLResponse` | Alerts |
| 25 | POST | `/ui/alerts/{alert_id}/ack` | `alert_ack` | `HTMLResponse` | Alerts |
| 26 | POST | `/ui/alerts/mute` | `alert_mute` | `HTMLResponse` | Alerts |
| 27 | POST | `/ui/alerts/unmute/{mute_id}` | `alert_unmute` | `HTMLResponse` | Alerts |

---

## 3. Existing Router Organization (`src/geoanalytics/api/routers/`)

Current directory contents:
```
src/geoanalytics/api/routers/
├── __init__.py (22 bytes)
├── asset.py (211 lines)
└── dashboard.py (80 lines)
```

### Analysis of Existing Partial Extraction:
1. `routers/dashboard.py` (80 lines): Contains `dashboard`, `status_partial`, `news_partial`, `ask_partial`. It imports `from geoanalytics.api import web` and delegates context rendering and caching to `web`.
2. `routers/asset.py` (211 lines): Contains `asset_page`, `asset_partial`, `asset_chart_partial`, `asset_indicators_partial`. It also imports `from geoanalytics.api import web` and delegates to `web`.
3. Currently, neither `asset.py` nor `dashboard.py` are attached to `web.py`'s main router, so all 27 endpoints are still registered directly inside `web.py`.

---

## 4. Internal & External Contracts Analysis

### 4.1 FastAPI App Integration (`src/geoanalytics/api/app.py`)
- `app.py` imports `from geoanalytics.api import web` and mounts `app.include_router(web.router)`.
- CLI (`geo analytics web` or `geo web`) invokes `uvicorn.run("geoanalytics.api.app:app", ...)`.
- Therefore, `web.py` MUST remain the single entry point router exporter (`web.router`) that incorporates all sub-routers from `src/geoanalytics/api/routers/`.

### 4.2 Unit Test Contracts (`tests/test_web.py`, `tests/test_regime_history.py`)
Unit tests rely heavily on `monkeypatch.setattr(web, "<symbol>", ...)` to mock data providers and handlers. Specifically, tests patch:
- `web.build_snapshot`
- `web.build_report`
- `web._asset_ohlcv`
- `web.list_assets`
- `web._portfolio_context`
- `web._add_position`
- `web._remove_position`
- `web._indicators_context`
- `web.backtest_asset_cached`
- `web._attr_rows`
- `web._track2_context`
- `web.recent_headlines`
- `web.recent_alerts`
- `web.manage.list_mutes`
- `web.manage.acknowledge`
- `web.get_alert`
- `web._regime_strip` (imported directly by `test_regime_history.py`)

**Critical Requirement for Refactoring**:
To ensure zero test regressions without modifying `tests/test_web.py`:
1. Sub-router handlers must call `web._function_name(...)` or `web.dependency_name(...)` at runtime, rather than statically binding local imports.
2. `web.py` must re-export all domain context functions and external query imports so `monkeypatch.setattr(web, ...)` dynamically intercepts all sub-router calls.

---

## 5. File Size Assessment

Current line counts in `src/geoanalytics/api/`:
- `web.py`: **1,034 lines** (EXCEEDS limit of 600 lines)
- `charts.py`: **858 lines** (Note: internal SVG generator; out of M4 web endpoint scope, but noted)
- `app.py`: 142 lines (Compliant)
- `schemas.py`: 116 lines (Compliant)
- `routers/asset.py`: 211 lines (Compliant)
- `routers/dashboard.py`: 80 lines (Compliant)

### Modularization Plan Line Count Estimates:

| Module | Target File | Proposed Content | Est. Lines |
|--------|-------------|------------------|------------|
| App Assembler & Cache | `web.py` | Global `router`, `templates`, `_cache`, `_cached`, re-exports & sub-router inclusions | ~150 lines |
| Dashboard | `routers/dashboard.py` | `dashboard`, `status_partial`, `news_partial`, `ask_partial` | ~85 lines |
| Asset | `routers/asset.py` | `asset_page`, `asset_partial`, `asset_chart_partial`, `asset_indicators_partial` | ~215 lines |
| Portfolio | `routers/portfolio.py` | `portfolio_page`, `portfolio_add`, `portfolio_remove`, `portfolio_cash` | ~190 lines |
| Graph & Market | `routers/graph.py` | `graph_page`, `graph_partial`, `market_graph_page`, `market_graph_partial`, `market_heatmap_partial` | ~230 lines |
| Factors & Backtest | `routers/factors.py` & `routers/backtest.py` | `factors_page`, `backtest_page`, `backtest_partial` | ~120 lines |
| Track2 | `routers/track2.py` | `track2_page`, `track2_partial` | ~150 lines |
| Alerts | `routers/alerts.py` | `alerts_page`, `alerts_partial`, `alert_ack`, `alert_mute`, `alert_unmute` | ~110 lines |

All files after refactoring will be **< 600 lines** (most well under 250 lines).

---

## 6. Implementation Recommendations for Implementer

1. **Sub-Router Inclusion Strategy**:
   In `src/geoanalytics/api/web.py`:
   ```python
   from geoanalytics.api.routers import alerts, asset, backtest, dashboard, factors, graph, portfolio, track2

   router = APIRouter()
   router.include_router(dashboard.router)
   router.include_router(asset.router)
   router.include_router(portfolio.router)
   router.include_router(graph.router)
   router.include_router(factors.router)
   router.include_router(backtest.router)
   router.include_router(track2.router)
   router.include_router(alerts.router)
   ```

2. **Re-export & Dynamic Delegation**:
   Keep helper context builders (`_portfolio_context`, `_track2_context`, `_graph_context`, `_cached`, etc.) in `web.py` or re-exported via `web.py`, and have sub-router handlers call `web._context_function()` so monkeypatching in `test_web.py` continues to work seamlessly.

3. **Verification**:
   Execute `source .venv/bin/activate && pytest tests/` after each router migration step.
