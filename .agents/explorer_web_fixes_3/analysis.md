# Analysis Report: Web Test Failures (Milestone 1)

This report investigates the 4 failing tests in `tests/test_web.py` due to recent template and context changes. Below we detail the observations, root causes, and a precise fix strategy.

## Summary of Failing Tests

| # | Test Case | File & Line | Error Type | Root Cause |
|---|---|---|---|---|
| 1 | `test_portfolio_page_with_positions` | `tests/test_web.py:113` | `AssertionError` | The "Holding Correlations" ("Корреляции холдингов") panel is not rendered by the `portfolio.html` template. |
| 2 | `test_asset_partial_empty_ticker` | `tests/test_web.py:271` | `AssertionError` | When `ticker` is empty, `asset_partial` defaults to `"IMOEX"` instead of returning a warning message containing `"Введите тикер"`. |
| 3 | `test_track2_page` | `tests/test_web.py:362` | `jinja2.exceptions.UndefinedError` | The mock data for `positions` in the test lacks the keys `unreal_pct` and `duration_bars`, which triggers an `UndefinedError` during Jinja formatting and math operations. |
| 4 | `test_asset_form_has_datalist` | `tests/test_web.py:440` | `AssertionError` | The asset selection form in `asset.html` was refactored to use a `<select>` dropdown instead of an input with `<datalist>`, violating the test requirements. |

---

## Detailed Investigation

### 1. `test_portfolio_page_with_positions`
* **Observation**: The test asserts that `"Корреляции холдингов" in r.text` is present.
* **Root Cause**: `portfolio.html` has no rendering block for `correlations`. Although `_portfolio_context` inside `src/geoanalytics/api/web.py` builds the list of correlations (`correlations` variable), the HTML template fails to draw the panel.
* **Solution**: Add the correlations panel to `src/geoanalytics/api/templates/portfolio.html` inside the bottom grid alongside `exposure` and `risk`.

### 2. `test_asset_partial_empty_ticker`
* **Observation**: The test asserts that a request to `/ui/partials/asset?ticker=` returns `"Введите тикер"`.
* **Root Cause**: The route handler `asset_partial` in `src/geoanalytics/api/web.py` redirects empty tickers to `"IMOEX"`.
* **Solution**: Add a check `if not ticker.strip(): return HTMLResponse('<p class="muted">Введите тикер.</p>')` in `asset_partial`, matching the behavior of `asset_chart_partial` and `asset_indicators_partial`.

### 3. `test_track2_page`
* **Observation**: Jinja template rendering raises `UndefinedError: 'dict object' has no attribute 'unreal_pct'`.
* **Root Cause**: The mock positions list defined in `_track2_ctx_populated` only contains:
  ```python
  "positions": [{"asset_code": "BR", "interval": "1h", "source": "rsi", "net_qty": 1,
                 "avg_price": 70.5, "last_price": 71.2, "realized_pnl": 0.0}]
  ```
  Since `unreal_pct` is missing, accessing `p.unreal_pct` in Jinja produces an `Undefined` object. Unlike `p.unreal_pct or 0` (which safely defaults to `0`), the subsequent filter formatting `{{ "%+.2f"|format(p.unreal_pct) }}` tries to format an `Undefined` value, raising the error. The same applies to `p.duration_bars`.
* **Solution**: In `src/geoanalytics/api/templates/_track2.html`, safely fetch `unreal_pct` and `duration_bars` using the `is defined` check:
  ```html
  {% set p_unreal = p.unreal_pct if p.unreal_pct is defined and p.unreal_pct is not none else none %}
  ...
  {% set p_duration = p.duration_bars if p.duration_bars is defined and p.duration_bars is not none else none %}
  ```
  This guarantees that missing keys default safely to `none` (rendering `—` and bypassing any division operations).

### 4. `test_asset_form_has_datalist`
* **Observation**: The test expects a `<datalist>` tag and the text `"GAZP"` to be in the response of `client.get("/ui/asset")`.
* **Root Cause**: `asset.html` uses a `<select>` dropdown to list assets instead of an `<input list="tickers">` accompanied by a `<datalist id="tickers">`.
* **Solution**: Revert or refactor the search form in `src/geoanalytics/api/templates/asset.html` to use a text input referencing a `<datalist>` populated with the `assets` list (like in `backtest.html`).

---

## Actionable Fix Strategy

We have prepared a machine-applicable patch containing all the necessary fixes:
* **Path**: `/home/ijstt/News/.agents/explorer_web_fixes_3/web_fixes.patch`

This patch will:
1. Render a **Holding Correlations** panel in `portfolio.html`.
2. Return a **"Введите тикер"** message in `/ui/partials/asset` if the ticker query parameter is empty.
3. Make `_track2.html` resilient to undefined `unreal_pct` and `duration_bars` by introducing Jinja defined check-guards.
4. Replace the `<select>` tag in `asset.html` with an `<input>` text box and `<datalist>` auto-completion panel.
