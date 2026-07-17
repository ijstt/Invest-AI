# Analysis of Web Dashboard Test Failures

## Summary
An investigation into the 4 failing tests in `tests/test_web.py` revealed that they are caused by recent UI template upgrades and missing layout blocks/attributes (such as `<datalist>`, `unreal_pct` safety, and `correlations` display). All failures can be cleanly resolved using the proposed patch.

---

## 1. Identified Failures & Root Causes

### Failure 1: `test_portfolio_page_with_positions`
*   **Exact Error**: `AssertionError: assert 'Корреляции холдингов' in r.text`
*   **Root Cause**: A recent template upgrade in `src/geoanalytics/api/templates/portfolio.html` completely removed the `Корреляции холдингов` section, although `web.py` still computes and passes `correlations` in the context.
*   **Solution**: Restore the correlations panel to `portfolio.html` right below the risk/exposure grid.

### Failure 2: `test_asset_partial_empty_ticker`
*   **Exact Error**: `AssertionError: assert 'Введите тикер' in r.text`
*   **Root Cause**: In `src/geoanalytics/api/web.py` under `@router.get("/ui/partials/asset")`, if `ticker` is empty or whitespace, it defaults to `"IMOEX"` instead of returning `"Введите тикер"`.
*   **Solution**: Update `asset_partial` to return an HTML Response containing the message `"Введите тикер."` when `ticker.strip()` is empty (similar to `asset_chart_partial` and `asset_indicators_partial`).

### Failure 3: `test_track2_page`
*   **Exact Error**: `jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'unreal_pct'`
*   **Root Cause**: The template `src/geoanalytics/api/templates/_track2.html` references `p.unreal_pct` strictly. Since the test mock data `_track2_ctx_populated` defines dummy positions as simple dicts without the `"unreal_pct"` key, Jinja raises an `UndefinedError`.
*   **Solution**: 
    1. Update the position mock in `tests/test_web.py` to include `"unreal_pct": 0.99`.
    2. Make the lookup inside `_track2.html` robust using `p.get('unreal_pct')` to handle cases where the attribute might not be supplied.

### Failure 4: `test_asset_form_has_datalist`
*   **Exact Error**: `AssertionError: assert '<datalist' in r.text and 'GAZP' in r.text`
*   **Root Cause**: Commit `cd0233c` replaced the autocomplete `<input>` and `<datalist id="tickers">` form elements in `src/geoanalytics/api/templates/asset.html` with a `<select>` dropdown.
*   **Solution**: Revert the `<select>` in `asset.html` back to a styled `<input type="text" list="tickers" ...>` and `<datalist id="tickers">` matching the expected interface.

---

## 2. Proposed Fix Strategy & Patch Details

We have provided a unified patch (`web_fixes.patch`) containing all changes needed to restore standard behavior and fix these tests.

### Changes Summary
1.  **`src/geoanalytics/api/templates/portfolio.html`**:
    Add the `correlations` display panel:
    ```html
    {% if correlations %}
    <div class="panel span2" style="margin-top:18px;">
      <h2>Корреляции холдингов</h2>
      {% for c in correlations %}
      <div class="metric"><span class="k">{{ c.pair }}</span>
        <span class="v {{ 'up' if c.r >= 0 else 'down' }}">{{ "%+.2f"|format(c.r) }}</span></div>
      {% endfor %}
    </div>
    {% endif %}
    ```
2.  **`src/geoanalytics/api/web.py`**:
    Modify `asset_partial` to return `HTMLResponse('<p class="muted">Введите тикер.</p>')` if ticker input is blank.
3.  **`src/geoanalytics/api/templates/_track2.html`**:
    Change `p.unreal_pct` to `p.get('unreal_pct')` to prevent Jinja `UndefinedError`.
4.  **`tests/test_web.py`**:
    Update the mock position structure in `_track2_ctx_populated()` to include `"unreal_pct": 0.99`.
5.  **`src/geoanalytics/api/templates/asset.html`**:
    Replace the `<select>` element with:
    ```html
    <input type="text" name="ticker" placeholder="Тикер, напр. SBER" list="tickers"
           value="{{ ticker or '' }}" autocomplete="off" autofocus
           style="min-width: 240px; padding: 10px 14px; border-radius: 8px; border: 1px solid var(--line); background: var(--panel2); color: var(--fg); font-family: var(--font-mono); font-size: 13px;">
    <datalist id="tickers">
      {% for a in assets or [] %}<option value="{{ a.ticker }}">{{ a.name }}</option>{% endfor %}
    </datalist>
    <button type="submit">Показать</button>
    ```

Please refer to the `web_fixes.patch` file in this directory for the exact line-by-line diff.
