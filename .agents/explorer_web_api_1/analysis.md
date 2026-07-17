# Detailed Web API Analysis & Modularization Plan

This document details the analysis of the failing tests in `tests/test_web.py` and presents a comprehensive modularization plan to split `src/geoanalytics/api/web.py` into smaller, maintainable router files under `src/geoanalytics/api/routers/`.

---

## 1. Analysis of the 4 Failing Tests

By comparing the current modified state of the workspace (where all 1216 tests pass) with the `HEAD` of the git repository, we identified the 4 tests that fail under the original baseline implementation, along with their root causes:

### Test 1: `test_track2_page`
- **File & Location**: `tests/test_web.py`
- **Root Cause**:
  In `_track2_ctx_populated()`, the test mocks positions as follows:
  ```python
  "positions": [{"asset_code": "BR", "interval": "1h", "source": "rsi", "net_qty": 1,
                 "avg_price": 70.5, "last_price": 71.2, "realized_pnl": 0.0}]
  ```
  This dictionary is missing the `unreal_pct` and `duration_bars` keys. In the original `src/geoanalytics/api/templates/_track2.html`, the template attempted to evaluate these attributes directly:
  ```html
  <td class="num {{ 'up' if (p.unreal_pct or 0) >= 0 else 'down' }}">
    {% if p.unreal_pct is not none %}{{ "%+.2f"|format(p.unreal_pct) }}%{% else %}—{% endif %}
  </td>
  ...
  {% if p.duration_bars is not none %}
  ```
  Since these keys were missing from the dictionary, Jinja2 raised an `UndefinedError` ('dict object' has no attribute 'unreal_pct').
- **Resolution**:
  1. Add safety checks (`is defined`) in the template `_track2.html`:
     ```html
     <td class="num {{ 'up' if (p.unreal_pct is defined and p.unreal_pct or 0) >= 0 else 'down' }}">
       {% if p.unreal_pct is defined and p.unreal_pct is not none %}{{ "%+.2f"|format(p.unreal_pct) }}%{% else %}—{% endif %}
     </td>
     ...
     {% if p.duration_bars is defined and p.duration_bars is not none %}
     ```
  2. Ensure the mock positions in `tests/test_web.py` include default values for these keys (e.g., `"unreal_pct": 0.99, "duration_bars": 2`).

### Test 2: `test_asset_form_has_datalist`
- **File & Location**: `tests/test_web.py`
- **Root Cause**:
  The test asserts:
  ```python
  assert "<datalist" in r.text and "GAZP" in r.text
  ```
  However, the original template `src/geoanalytics/api/templates/asset.html` used a `<select>` element to display assets rather than a combination of `<input>` and `<datalist>`:
  ```html
  <select name="ticker" onchange="this.form.dispatchEvent(new Event('submit', {cancelable: true}))" ...>
    {% for a in assets or [] %}
    <option value="{{ a.ticker }}" {% if ticker == a.ticker %}selected{% endif %}>{{ a.ticker }} — {{ a.name }}</option>
    {% endfor %}
  </select>
  ```
  As a result, `<datalist` was absent from the rendered HTML, causing the test to fail.
- **Resolution**:
  Replace the `<select>` tag in `asset.html` with an `<input>` field and a `<datalist>` element:
  ```html
  <input type="text" name="ticker" placeholder="Тикер, напр. IMOEX" list="tickers" value="{{ ticker or '' }}" autocomplete="off" autofocus ...>
  <datalist id="tickers">
    {% for a in assets or [] %}<option value="{{ a.ticker }}">{{ a.name }}</option>{% endfor %}
  </datalist>
  ```

### Test 3: `test_asset_partial_empty_ticker`
- **File & Location**: `tests/test_web.py`
- **Root Cause**:
  The route `asset_partial` in `src/geoanalytics/api/web.py` originally had a fallback:
  ```python
  def asset_partial(request: Request, ticker: str = ""):
      if not ticker or not ticker.strip():
          ticker = "IMOEX"
      return templates.TemplateResponse(request, "_asset_result.html", _asset_context(ticker))
  ```
  However, the test asserted that an empty ticker query should render `"Введите тикер"`:
  ```python
  def test_asset_partial_empty_ticker():
      r = client.get("/ui/partials/asset?ticker=")
      assert r.status_code == 200
      assert "Введите тикер" in r.text
  ```
  Because the route fell back to `"IMOEX"`, it rendered the report for IMOEX instead of returning the placeholder text, causing the assertion to fail.
- **Resolution**:
  Update `asset_partial` to return an HTML response displaying the validation message:
  ```python
  def asset_partial(request: Request, ticker: str = ""):
      if not ticker or not ticker.strip():
          return HTMLResponse("<p class=\"muted\">Введите тикер</p>")
      return templates.TemplateResponse(request, "_asset_result.html", _asset_context(ticker))
  ```

### Test 4: `test_portfolio_page_with_positions`
- **File & Location**: `tests/test_web.py`
- **Root Cause**:
  The test asserts:
  ```python
  assert "Корреляции холдингов" in r.text
  ```
  However, the original template `src/geoanalytics/api/templates/portfolio.html` completely lacked the "Holding Correlations" ("Корреляции холдингов") section, so the text was missing from the rendered output.
- **Resolution**:
  Add the correlations rendering panel to `portfolio.html`:
  ```html
  {% if correlations %}
  <div class="panel">
    <h2>Корреляции холдингов</h2>
    {% for c in correlations %}
    <div class="metric"><span class="k">{{ c.pair }}</span><span class="v {{ 'up' if c.r >= 0 else 'down' }}">{{ "%+.2f"|format(c.r) }}</span></div>
    {% endfor %}
  </div>
  {% endif %}
  ```

---

## 2. Modularization Plan for `src/geoanalytics/api/web.py`

### 2.1 Design Objectives
- **Target Size**: No individual file must exceed 600 lines.
- **API Preservation**: Ensure all public APIs (e.g. `web.router`, `web.templates`) remain intact so that `src/geoanalytics/api/app.py` registers the routes successfully without modification.
- **Mock/Test Compatibility**: Ensure `tests/test_web.py` can continue to monkeypatch helper functions (such as `web._asset_context` or `web.build_report`) on the `web` module. This requires route handlers to access these helpers via `web.<function>` at runtime.

### 2.2 Proposed Package Structure

We will create a new directory structure `src/geoanalytics/api/routers/` and move the respective routes and helpers there:

```
src/geoanalytics/api/
├── __init__.py
├── app.py
├── charts.py
├── schemas.py
├── web.py (Facade & Router Aggregator)
├── templates/ (existing HTML templates)
└── routers/
    ├── __init__.py
    ├── dashboard.py (Dashboard + News + Ask + Status)
    ├── asset.py     (Asset Detail + Charts + Indicators)
    ├── backtest.py  (Backtest results)
    ├── portfolio.py (Portfolio CRUD + stats)
    ├── graph.py     (Single Asset & Market Radial Trees)
    ├── alerts.py    (Alert feeds + Ack + Mutes)
    └── factors.py   (Market factors + Track 2 demo account)
```

### 2.3 Sub-router Definitions & Responsibilities

#### 1. `src/geoanalytics/api/routers/dashboard.py` (approx. 90 lines)
- **Routes**: `/`, `/ui/partials/status`, `/ui/partials/news`, `/ui/partials/ask`
- **Helpers**: `_status_context()`, `_pulse_context()`
- **Responsibilities**: Renders the landing dashboard, real-time pipeline status, sentiment pulse, and interactive question-answering.

#### 2. `src/geoanalytics/api/routers/asset.py` (approx. 250 lines)
- **Routes**: `/ui/asset`, `/ui/partials/asset`, `/ui/partials/asset/chart`, `/ui/partials/asset/indicators`
- **Helpers**: `_asset_ohlcv()`, `_sentiment_cells()`, `_price_overlays()`, `_chart_event_markers()`, `_chart_context()`, `_indicators_context()`, `_asset_context()`, `_factor_trend()`
- **Responsibilities**: Handles query-level reporting for individual assets, compiles price histories, overlays SMA/Bollinger bands, and embeds news/indicators.

#### 3. `src/geoanalytics/api/routers/backtest.py` (approx. 50 lines)
- **Routes**: `/ui/backtest`, `/ui/partials/backtest`
- **Helpers**: `_backtest_context()`
- **Responsibilities**: Renders backtesting pages for strategies like sma_cross, momentum, RSI, and news sentiment.

#### 4. `src/geoanalytics/api/routers/portfolio.py` (approx. 150 lines)
- **Routes**: `/ui/portfolio`, `/ui/portfolio/add`, `/ui/portfolio/remove`, `/ui/portfolio/cash`
- **Helpers**: `_portfolio_context()`, `_compute_portfolio_stance()`, `_add_position()`, `_remove_position()`, `_compute_portfolio_report()`
- **Responsibilities**: Manages the user's paper portfolio holdings, calculates sector allocations (donut & treemap charts), exposures, and cash balances.

#### 5. `src/geoanalytics/api/routers/graph.py` (approx. 180 lines)
- **Routes**: `/ui/graph`, `/ui/partials/graph`, `/ui/graph/market`, `/ui/partials/graph/market`, `/ui/partials/graph/heatmap`
- **Helpers**: `_graph_context()`, `_market_graph_context()`, `_market_heatmap_context()`
- **Responsibilities**: Radial trees and market heatmaps demonstrating connection clusters between peers, sectors, and events.

#### 6. `src/geoanalytics/api/routers/alerts.py` (approx. 70 lines)
- **Routes**: `/ui/alerts`, `/ui/partials/alerts`, `/ui/alerts/{alert_id}/ack`, `/ui/alerts/mute`, `/ui/alerts/unmute/{mute_id}`
- **Helpers**: `_alerts_context()`
- **Responsibilities**: Manages triggered alerts (price moves, calendar events) and suppression (muting) rules.

#### 7. `src/geoanalytics/api/routers/factors.py` (approx. 220 lines)
- **Routes**: `/ui/factors`, `/ui/track2`, `/ui/partials/track2`
- **Helpers**: `_factors_context()`, `_regime_strip()`, `_track2_context()`, `_attr_rows()`
- **Responsibilities**: Lists raw commodity/FX metrics alongside details for the "Track 2" futures demo account.

### 2.4 Exposing Public APIs and Supporting Mocks

To ensure that tests in `test_web.py` can mock internal functions via `monkeypatch.setattr(web, "<function_name>", ...)`, we must resolve helper calls through the `web` namespace at runtime.

#### Step 1: Retain Constants and Cache inside `src/geoanalytics/api/web.py`
To avoid circular import problems during initialization, we define constants, templates, and caching in `web.py` *before* importing routers.

```python
# src/geoanalytics/api/web.py
from __future__ import annotations
import time
from pathlib import Path
from fastapi import APIRouter
from fastapi.templating import Jinja2Templates

# Global shared resources
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
router = APIRouter()

_STRATEGIES = ["sma_cross", "momentum", "rsi", "sentiment"]
_ALERT_TYPES = ["price_move", "neg_spike", "new_event", "technical", "combo", "calendar", "portfolio"]
_SEVERITIES = ["info", "warning", "critical"]
_CHART_RANGES = {"1m": 31, "3m": 93, "6m": 186, "1y": 372, "max": None}

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
```

#### Step 2: Import Router Instances and Include Them
Further down in `web.py`, we import and mount routers on the central `router` object.

```python
# Import routers to register them on the central router
from geoanalytics.api.routers import (
    dashboard,
    asset,
    backtest,
    portfolio,
    graph,
    alerts,
    factors,
)

router.include_router(dashboard.router)
router.include_router(asset.router)
router.include_router(backtest.router)
router.include_router(portfolio.router)
router.include_router(graph.router)
router.include_router(alerts.router)
router.include_router(factors.router)
```

#### Step 3: Re-export Helpers for Test Mocks
Also in `web.py`, re-export the helper functions so they are exposed as attributes of the `web` module:

```python
# Expose helper functions for backward compatibility / tests (monkeypatching)
build_snapshot = dashboard.build_snapshot
recent_headlines = dashboard.recent_headlines
ask_answer = dashboard.ask_answer
_status_context = dashboard._status_context
_pulse_context = dashboard._pulse_context

build_report = asset.build_report
list_assets = asset.list_assets
_asset_ohlcv = asset._asset_ohlcv
_sentiment_cells = asset._sentiment_cells
_price_overlays = asset._price_overlays
_chart_event_markers = asset._chart_event_markers
_chart_context = asset._chart_context
_indicators_context = asset._indicators_context
_asset_context = asset._asset_context
_factor_trend = asset._factor_trend

backtest_asset_cached = backtest.backtest_asset_cached
_backtest_context = backtest._backtest_context

_portfolio_context = portfolio._portfolio_context
_compute_portfolio_stance = portfolio._compute_portfolio_stance
_add_position = portfolio._add_position
_remove_position = portfolio._remove_position
_compute_portfolio_report = portfolio._compute_portfolio_report

_graph_context = graph._graph_context
_market_graph_context = graph._market_graph_context
_market_heatmap_context = graph._market_heatmap_context

recent_alerts = alerts.recent_alerts
get_alert = alerts.get_alert
_alerts_context = alerts._alerts_context
# Exposure of mute sub-namespaces
class manage:
    list_mutes = staticmethod(alerts.list_mutes)
    mute_for_days = staticmethod(alerts.mute_for_days)
    unmute = staticmethod(alerts.unmute)
    acknowledge = staticmethod(alerts.acknowledge)
    SCOPE_TYPES = alerts.SCOPE_TYPES

_factors_context = factors._factors_context
_regime_strip = factors._regime_strip
_track2_context = factors._track2_context
_attr_rows = factors._attr_rows
```

#### Step 4: Write Route Handlers to Resolve Namespaces at Runtime
In the individual router files (e.g. `src/geoanalytics/api/routers/dashboard.py`), route handlers should call functions via `web.something` rather than calling local functions directly, allowing any mocks set by tests on the `web` module to be honored:

```python
# src/geoanalytics/api/routers/dashboard.py
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from geoanalytics.api import web
from geoanalytics.query.news_summary import build_snapshot, recent_headlines
from geoanalytics.query.ask import answer as ask_answer

router = APIRouter()

# Local helpers (which are also re-exported in web.py)
def _status_context():
    # Helper logic...
    pass

def _pulse_context():
    # Helper logic...
    pass

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, hours: int = 24):
    # Calling through 'web' to respect monkeypatching in test client runs!
    snap = web.build_snapshot(hours=hours, use_llm=False)
    return web.templates.TemplateResponse(
        request, "dashboard.html",
        {
            "snap": snap,
            "hours": hours,
            **web._pulse_context(),
            **web._status_context(),
        }
    )
```

This dynamic runtime resolution via the `web` namespace ensures complete test suite compatibility without changing a single line of test code in `test_web.py`.
