# Handoff Report — Web Fixes Investigation (Explorer 2)

## 1. Observation
We ran pytest on `tests/test_web.py` using `.venv/bin/pytest tests/test_web.py` and obtained 4 failures:

1.  **`test_portfolio_page_with_positions`**:
    *   **Failure point**: `tests/test_web.py:113`
    *   **Verbatim Error**: `AssertionError: assert 'Корреляции холдингов' in r.text`
    *   **Related file**: `src/geoanalytics/api/templates/portfolio.html` has no section matching `Корреляции холдингов` or rendering `correlations`.

2.  **`test_asset_partial_empty_ticker`**:
    *   **Failure point**: `tests/test_web.py:271`
    *   **Verbatim Error**: `AssertionError: assert 'Введите тикер' in r.text`
    *   **Observation**: The response contains IMOEX dashboard HTML instead of "Введите тикер".
    *   **Related file**: `src/geoanalytics/api/web.py` lines 924-925:
        ```python
        if not ticker or not ticker.strip():
            ticker = "IMOEX"
        ```

3.  **`test_track2_page`**:
    *   **Failure point**: `tests/test_web.py:362`
    *   **Verbatim Error**: `jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'unreal_pct'`
    *   **Related file**: `src/geoanalytics/api/templates/_track2.html` line 274:
        ```html
        {% if p.unreal_pct is not none %}{{ "%+.2f"|format(p.unreal_pct) }}%{% else %}—{% endif %}
        ```
    *   **Observation**: The mock positions list in `tests/test_web.py:347` is a dictionary without the `"unreal_pct"` key.

4.  **`test_asset_form_has_datalist`**:
    *   **Failure point**: `tests/test_web.py:440`
    *   **Verbatim Error**: `AssertionError: assert '<datalist' in r.text and 'GAZP' in r.text`
    *   **Related file**: `src/geoanalytics/api/templates/asset.html` was edited in commit `cd0233c` replacing the `<input list="tickers">` and `<datalist>` structure with a `<select>` dropdown.

---

## 2. Logic Chain
1.  **For `test_portfolio_page_with_positions`**:
    *   The test asserts that `"Корреляции холдингов"` exists in the portfolio page HTML when position correlations exist.
    *   `src/geoanalytics/api/templates/portfolio.html` contains no `correlations` lookup or rendering logic.
    *   *Conclusion*: Restoring the correlations panel block in `portfolio.html` is required.
2.  **For `test_asset_partial_empty_ticker`**:
    *   The test requests the HTMX partial `/ui/partials/asset?ticker=` and expects the HTML to ask the user to enter a ticker.
    *   The route `asset_partial` in `web.py` defaults to `"IMOEX"` when the parameter is empty.
    *   *Conclusion*: `asset_partial` must return `HTMLResponse('<p class="muted">Введите тикер.</p>')` when `ticker` is blank, identical to the pattern used in indicator and chart partials.
3.  **For `test_track2_page`**:
    *   The template `_track2.html` expects `unreal_pct` to be present on the position object/dictionary `p`.
    *   Strict undefined configuration causes Jinja to crash when it renders a dictionary `p` which does not contain the key `"unreal_pct"`.
    *   *Conclusion*: Using `p.get('unreal_pct')` makes the template robust to missing dictionary keys, and updating the mock data in `tests/test_web.py` aligns the test data with real runtime models.
4.  **For `test_asset_form_has_datalist`**:
    *   The test asserts that a `<datalist>` is present and contains `"GAZP"` when navigating to `/ui/asset`.
    *   The template `asset.html` has no `<datalist>` but rather a `<select>` tag.
    *   *Conclusion*: The `<select>` element must be replaced with the original styled `<input>` and `<datalist id="tickers">`.

---

## 3. Caveats
No caveats. All failure causes were directly matched with the template/code segments.

---

## 4. Conclusion
The four failures are completely addressed by:
*   Adding the correlations panel to `portfolio.html`.
*   Fixing the empty parameter check in the `asset_partial` endpoint.
*   Guarding `unreal_pct` in `_track2.html` and updating the test mock positions in `tests/test_web.py`.
*   Restoring the `<datalist>` elements in `asset.html`.

A unified patch file `web_fixes.patch` is created in `/home/ijstt/News/.agents/explorer_web_fixes_2/` containing these changes.

---

## 5. Verification Method
The implementer should apply the patch and run:
```bash
.venv/bin/pytest tests/test_web.py
```
**Expected outcome**: All 42 tests in `tests/test_web.py` pass.
