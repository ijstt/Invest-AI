# Analysis - Web Fixes for Milestone 1

This analysis details the investigation of 4 failing tests in `tests/test_web.py`. We ran the test suite, isolated the failures, analyzed the source templates and endpoints, and proposed a clear fix strategy.

---

## 1. Summary of Failing Tests

The test suite run on `tests/test_web.py` yielded exactly 38 passes and 4 failures. The failures are:

| Test Name | File/Line | Failure Type | Root Cause |
| --- | --- | --- | --- |
| `test_portfolio_page_with_positions` | `tests/test_web.py:113` | `AssertionError` | Missing "Корреляции холдингов" panel in `portfolio.html`. |
| `test_asset_partial_empty_ticker` | `tests/test_web.py:271` | `AssertionError` | Route `/ui/partials/asset` defaults to `"IMOEX"` instead of returning `"Введите тикер"`. |
| `test_track2_page` | `tests/test_web.py:362` | `jinja2.exceptions.UndefinedError` | Mock position dictionaries are missing `"unreal_pct"` and `"duration_bars"` keys, triggering a Jinja render crash in `_track2.html`. |
| `test_asset_form_has_datalist` | `tests/test_web.py:440` | `AssertionError` | `asset.html` renders a `<select>` drop-down instead of a `<datalist>` autocomplete combo-box. |

---

## 2. Deep Dive & Technical Analysis

### Failure 1: `test_portfolio_page_with_positions`
* **Observation**:
  - The assertion `assert "Корреляции холдингов" in r.text` fails at line 113 of `tests/test_web.py`.
  - Looking at `src/geoanalytics/api/templates/portfolio.html`, the word `correlations` or `"Корреляции холдингов"` does not appear anywhere.
* **Explanation**:
  - The backend function `_portfolio_context()` in `src/geoanalytics/api/web.py` correctly calculates and returns `correlations` as part of the context (mapping pairs to their correlation values).
  - However, the UI template `portfolio.html` has no corresponding HTML element to display this information. The test is expecting a correlation panel to be rendered.
* **Fix**:
  - Insert a new card/panel to display correlations if the `correlations` list exists in the template context. The design can be placed in the bottom grid alongside "Риск" and "Факторная экспозиция".

---

### Failure 2: `test_asset_partial_empty_ticker`
* **Observation**:
  - The assertion `assert "Введите тикер" in r.text` fails at line 271 of `tests/test_web.py`.
  - The response returns the IMOEX dashboard HTML fragment instead.
* **Explanation**:
  - The handler `asset_partial` in `src/geoanalytics/api/web.py` is defined as:
    ```python
    @router.get("/ui/partials/asset", response_class=HTMLResponse)
    def asset_partial(request: Request, ticker: str = ""):
        if not ticker or not ticker.strip():
            ticker = "IMOEX"
        return templates.TemplateResponse(request, "_asset_result.html", _asset_context(ticker))
    ```
  - When an empty `ticker` parameter is passed, the route falls back to `"IMOEX"`.
  - In contrast, the other partial handlers in `web.py` (like `asset_chart_partial` and `asset_indicators_partial`) correctly check for an empty ticker and return an HTML snippet instructing the user to enter a ticker:
    ```python
    if not ticker.strip():
        return HTMLResponse('<p class="muted">Введите тикер.</p>')
    ```
* **Fix**:
  - Align `asset_partial` with the other partial endpoints by returning a standard `HTMLResponse` stating `"Введите тикер"` if the query parameter `ticker` is empty or only whitespace.

---

### Failure 3: `test_track2_page`
* **Observation**:
  - A rendering error `jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'unreal_pct'` is raised when rendering `_track2.html` at line 274:
    ```html
    {% if p.unreal_pct is not none %}{{ "%+.2f"|format(p.unreal_pct) }}%{% else %}—{% endif %}
    ```
* **Explanation**:
  - In the test context generator `_track2_ctx_populated()`, the mocked position dictionary list is declared as:
    ```python
    "positions": [{"asset_code": "BR", "interval": "1h", "source": "rsi", "net_qty": 1,
                   "avg_price": 70.5, "last_price": 71.2, "realized_pnl": 0.0}]
    ```
  - This list is missing the keys `"unreal_pct"` and `"duration_bars"` which are populated in real runs via `_track2_context()`.
  - When Jinja2 renders the template, `p.unreal_pct` evaluates to `Undefined`. Since `Undefined` is not `None`, Jinja2 enters the `if` branch and attempts to pass `Undefined` to the `format` filter, which throws an exception.
* **Fix**:
  - **Part A (Test Fix)**: Add `"unreal_pct": 0.99` and `"duration_bars": 2` keys to the mock position dictionary in `_track2_ctx_populated()`.
  - **Part B (Template Fix)**: Make the template rendering more defensive by checking if the variables are defined: `p.unreal_pct is defined` and `p.duration_bars is defined`.

---

### Failure 4: `test_asset_form_has_datalist`
* **Observation**:
  - The assertion `assert "<datalist" in r.text and "GAZP" in r.text` fails at line 440 of `tests/test_web.py`.
* **Explanation**:
  - The test expects a `<datalist>` element for tickers (enabling autocomplete/combo-box selection) to be available in the main asset analytics page `/ui/asset`.
  - Looking at `src/geoanalytics/api/templates/asset.html`, the search form has been changed to use a dropdown list (`<select>` and `<option>`) instead:
    ```html
    <select name="ticker" onchange="this.form.dispatchEvent(new Event('submit', {cancelable: true}))" ...>
      {% for a in assets or [] %}
      <option value="{{ a.ticker }}" {% if ticker == a.ticker %}selected{% endif %}>{{ a.ticker }} — {{ a.name }}</option>
      {% endfor %}
    </select>
    ```
  - While functional, this broke the test assertion checking for datalist presence.
* **Fix**:
  - Revert the dropdown back to an `<input type="text" name="ticker" list="tickers" ...>` matching the datalist structure used in `backtest.html`. Add the `<datalist id="tickers">` element iterating over the assets context.

---

## 3. Fix Strategy & Validation Command

To implement these fixes, the following steps must be taken:
1. Update `src/geoanalytics/api/templates/portfolio.html` to append the correlations panel block.
2. Edit `src/geoanalytics/api/web.py` to fix the empty-ticker validation check inside `asset_partial`.
3. Update `src/geoanalytics/api/templates/_track2.html` to perform safe `is defined` Jinja2 checks on position fields.
4. Modify `tests/test_web.py`'s `_track2_ctx_populated` function to include `"unreal_pct"` and `"duration_bars"` keys in the mock positions list.
5. Restore `<datalist>` auto-completion in `src/geoanalytics/api/templates/asset.html`.

A unified patch file has been prepared at:
`/home/ijstt/News/.agents/explorer_web_fixes_1/proposed_fixes.patch`

To validate that these changes successfully resolve the failures, the following command must run and return exit code `0`:
```bash
.venv/bin/pytest tests/test_web.py
```
