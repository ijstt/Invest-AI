# Web API Modularization Analysis (Milestone 4)

## Executive Summary
`src/geoanalytics/api/web.py` is currently a 1,034-line file containing all HTMX/Jinja web UI routes and helper functions. The previous team started extracting endpoints into `src/geoanalytics/api/routers/dashboard.py` and `asset.py`, but `web.py` was left unmodularized and `app.py` directly mounts `web.router`. All 1,228 project tests pass (`pytest tests/`). Refactoring `web.py` into 8 domain-specific sub-routers within `src/geoanalytics/api/routers/` will reduce `web.py` to ~120 lines and keep every router file well below the 600-line limit.

---

## 1. Baseline Verification & Current Architecture

### 1.1 Test Suite Status
- Command executed: `source .venv/bin/activate && pytest tests/`
- Result: **1228 passed, 2 warnings in 24.88s**
- Baseline status: 100% green.

### 1.2 Current File Breakdown
| File Path | Current Line Count | Description |
|-----------|-------------------|-------------|
| `src/geoanalytics/api/web.py` | 1,034 | Monolithic web router and helpers |
| `src/geoanalytics/api/routers/__init__.py` | 2 | Package marker |
| `src/geoanalytics/api/routers/dashboard.py` | 81 | Partial/draft extraction for dashboard routes |
| `src/geoanalytics/api/routers/asset.py` | 212 | Partial/draft extraction for asset routes |
| `src/geoanalytics/api/app.py` | 143 | FastAPI app entry point (`app.include_router(web.router)`) |

---

## 2. Comprehensive Endpoint & Helper Function Inventory

### 2.1 Router Distribution Plan

| Domain / Target File | Endpoint Path | Method | Helper Functions / Variables | Est. Lines |
|----------------------|---------------|--------|------------------------------|------------|
| **`web.py`** (Assembler) | — | — | `templates`, `_cache`, `_CACHE_TTL_SEC`, `_cached`, `_invalidate_cache`, `_CHART_RANGES`, `_STRATEGIES`, `_ALERT_TYPES`, `_SEVERITIES`, `manage`, re-exports (`_regime_strip`, `_attr_rows`, `build_report`, etc.) | ~120 |
| **`routers/dashboard.py`** | `/`<br>`/ui/partials/status`<br>`/ui/partials/news`<br>`/ui/partials/ask` | GET | `_status_context`, `_pulse_context` | ~90 |
| **`routers/asset.py`** | `/ui/asset`<br>`/ui/partials/asset`<br>`/ui/partials/asset/chart`<br>`/ui/partials/asset/indicators` | GET | `_asset_ohlcv`, `_sentiment_cells`, `_price_overlays`, `_chart_event_markers`, `_chart_context`, `_indicators_context`, `_asset_context`, `_factor_trend` | ~210 |
| **`routers/backtest.py`** | `/ui/backtest`<br>`/ui/partials/backtest` | GET | `_backtest_context` | ~50 |
| **`routers/portfolio.py`** | `/ui/portfolio`<br>`/ui/portfolio/add`<br>`/ui/portfolio/remove`<br>`/ui/portfolio/cash` | GET<br>POST<br>POST<br>POST | `_portfolio_context`, `_compute_portfolio_stance`, `_add_position`, `_remove_position`, `_compute_portfolio_report` | ~140 |
| **`routers/graph.py`** | `/ui/graph`<br>`/ui/partials/graph`<br>`/ui/graph/market`<br>`/ui/partials/graph/market`<br>`/ui/partials/graph/heatmap` | GET | `_graph_context`, `_market_graph_context`, `_market_heatmap_context`, `_FACTOR_CSS`, `_FACTOR_RU`, `_EVENT_AGG` | ~200 |
| **`routers/factors.py`** | `/ui/factors` | GET | `_factors_context`, `_regime_strip` | ~60 |
| **`routers/track2.py`** | `/ui/track2`<br>`/ui/partials/track2` | GET | `_track2_context`, `_attr_rows`, `_TRACK2_ACCOUNT` | ~160 |
| **`routers/alerts.py`** | `/ui/alerts`<br>`/ui/partials/alerts`<br>`/ui/alerts/{alert_id}/ack`<br>`/ui/alerts/mute`<br>`/ui/alerts/unmute/{mute_id}` | GET<br>GET<br>POST<br>POST<br>POST | `_alerts_context` | ~80 |

All target files are strictly under the 600 lines limit (maximum target is ~210 lines for `asset.py`).

---

## 3. Test & External Dependency Compatibility

### 3.1 Test Suite Requirements
1. **Monkeypatching on `geoanalytics.api.web`**:
   - `tests/test_web.py` and `tests/test_web_adversarial.py` frequently use `monkeypatch.setattr(web, "<func_or_helper>", mock)`.
   - Sub-routers should access context builders and data functions via `web.<func>` (e.g. `web._asset_ohlcv(...)`, `web.build_report(...)`, `web._portfolio_context()`).
   - `web.py` will re-export or alias all helper functions and query functions (e.g. `_regime_strip`, `_attr_rows`, `build_report`, `build_snapshot`, `list_assets`, `recent_headlines`, `ask_answer`, `manage`).

2. **Direct Function Imports**:
   - `tests/test_regime_history.py` imports `from geoanalytics.api.web import _regime_strip`. `web.py` will alias `_regime_strip = factors._regime_strip` or import `_regime_strip` from `routers.factors`.
   - `tests/test_web.py` calls `web._attr_rows(...)` and uses `web._cache`, `web._cached`, `web._invalidate_cache`. `web.py` will expose these directly.

### 3.2 Raspberry Pi Deployment Requirements
- `deploy/pi/geo-dashboard.service` executes `%h/News/.venv/bin/geo serve --host 0.0.0.0 --port 8800`.
- `geo serve` in `src/geoanalytics/cli.py` invokes `uvicorn.run("geoanalytics.api.app:app", ...)`.
- `app.py` includes `web.router`.
- Because `web.router` will aggregate all sub-routers via `router.include_router(...)`, all endpoint URLs, HTTP methods, and parameter names will remain 100% identical.

---

## 4. Proposed `web.py` Assembly Structure

```python
"""Lightweight FastAPI app assembler for web UI (M4)."""
from __future__ import annotations

import time
from pathlib import Path
from fastapi import APIRouter
from fastapi.templating import Jinja2Templates
from geoanalytics.alerts import manage

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()

# Shared UI constants
_STRATEGIES = ["sma_cross", "momentum", "rsi", "sentiment"]
_ALERT_TYPES = ["price_move", "neg_spike", "new_event", "technical", "combo", "calendar", "portfolio"]
_SEVERITIES = ["info", "warning", "critical"]
_CHART_RANGES = {"1m": 31, "3m": 93, "6m": 186, "1y": 372, "max": None}

# Shared TTL Cache Engine
_CACHE_TTL_SEC = 60.0
_cache: dict[str, tuple[float, object]] = {}

def _cached(key: str, fn, ttl: float = _CACHE_TTL_SEC):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (now, value)
    return value

def _invalidate_cache(key: str) -> None:
    _cache.pop(key, None)

# Import sub-routers
from geoanalytics.api.routers import (
    alerts,
    asset,
    backtest,
    dashboard,
    factors,
    graph,
    portfolio,
    track2,
)

# Re-exports for test compatibility and monkeypatching
_regime_strip = factors._regime_strip
_attr_rows = track2._attr_rows
_asset_ohlcv = asset._asset_ohlcv
_sentiment_cells = asset._sentiment_cells
_price_overlays = asset._price_overlays
_chart_event_markers = asset._chart_event_markers
_chart_context = asset._chart_context
_indicators_context = asset._indicators_context
_asset_context = asset._asset_context
_factor_trend = asset._factor_trend
_backtest_context = backtest._backtest_context
_portfolio_context = portfolio._portfolio_context
_compute_portfolio_stance = portfolio._compute_portfolio_stance
_add_position = portfolio._add_position
_remove_position = portfolio._remove_position
_compute_portfolio_report = portfolio._compute_portfolio_report
_graph_context = graph._graph_context
_market_graph_context = graph._market_graph_context
_market_heatmap_context = graph._market_heatmap_context
_factors_context = factors._factors_context
_track2_context = track2._track2_context
_alerts_context = alerts._alerts_context

# Re-export queries commonly mocked in test_web
from geoanalytics.query.news_summary import build_snapshot, recent_headlines
from geoanalytics.query.asset_report import build_report
from geoanalytics.query.assets_feed import list_assets
from geoanalytics.query.ask import answer as ask_answer

# Register sub-routers
router.include_router(dashboard.router)
router.include_router(asset.router)
router.include_router(backtest.router)
router.include_router(portfolio.router)
router.include_router(graph.router)
router.include_router(factors.router)
router.include_router(track2.router)
router.include_router(alerts.router)
```

---

## 5. Verification Plan for Implementation
1. **Pytest Verification**:
   Execute `source .venv/bin/activate && pytest tests/` to confirm 1228 tests pass.
2. **Line Count Verification**:
   Execute `wc -l src/geoanalytics/api/web.py src/geoanalytics/api/routers/*.py` to confirm no file exceeds 600 lines.
3. **Deployment Verification**:
   Verify `geo serve` / `app.py` loads `web.router` without errors.
