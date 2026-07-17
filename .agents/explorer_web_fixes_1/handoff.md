# Handoff Report — Web Fixes for Milestone 1

## 1. Observation

We directly executed the test suite command on `tests/test_web.py`:
```bash
.venv/bin/pytest tests/test_web.py
```
This command exited with code `1`, reporting `38 passed, 4 failed`. 

The four verbatim failure logs and relevant file ranges observed:

1. **`test_portfolio_page_with_positions` failure**:
   - Traceback:
     ```
     tests/test_web.py:113: AssertionError: assert "Корреляции холдингов" in r.text
     ```
   - In `src/geoanalytics/api/templates/portfolio.html`, there is no reference to `correlations` or the Russian string `"Корреляции холдингов"`.

2. **`test_asset_partial_empty_ticker` failure**:
   - Traceback:
     ```
     tests/test_web.py:271: AssertionError: assert "Введите тикер" in r.text
     ```
   - In `src/geoanalytics/api/web.py` lines 921-926:
     ```python
     @router.get("/ui/partials/asset", response_class=HTMLResponse)
     def asset_partial(request: Request, ticker: str = ""):
         if not ticker or not ticker.strip():
             ticker = "IMOEX"
         return templates.TemplateResponse(request, "_asset_result.html", _asset_context(ticker))
     ```

3. **`test_track2_page` failure**:
   - Traceback:
     ```
     src/geoanalytics/api/templates/_track2.html:274: in top-level template code
         {% if p.unreal_pct is not none %}{{ "%+.2f"|format(p.unreal_pct) }}%{% else %}—{% endif %}
     E   jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'unreal_pct'
     ```
   - In `tests/test_web.py` line 345-351, the mock context helper `_track2_ctx_populated` defines positions as:
     ```python
     "positions": [{"asset_code": "BR", "interval": "1h", "source": "rsi", "net_qty": 1,
                    "avg_price": 70.5, "last_price": 71.2, "realized_pnl": 0.0}]
     ```

4. **`test_asset_form_has_datalist` failure**:
   - Traceback:
     ```
     tests/test_web.py:440: AssertionError: assert "<datalist" in r.text and "GAZP" in r.text
     ```
   - In `src/geoanalytics/api/templates/asset.html` lines 9-14, the search form uses a `<select>` dropdown instead of an `<input list="tickers">` autocomplete list:
     ```html
     <select name="ticker" onchange="this.form.dispatchEvent(new Event('submit', {cancelable: true}))" ...>
       {% for a in assets or [] %}
       <option value="{{ a.ticker }}" {% if ticker == a.ticker %}selected{% endif %}>{{ a.ticker }} — {{ a.name }}</option>
       {% endfor %}
     </select>
     ```

---

## 2. Logic Chain

- **Observation 1** shows that `test_portfolio_page_with_positions` asserts that `"Корреляции холдингов"` must appear in the rendered portfolio page. Since the template `portfolio.html` lacks any references to `correlations`, the assertion fails. Therefore, adding a dedicated correlations panel containing a loop over the correlations list is necessary.
- **Observation 2** shows that when `ticker` query parameter is empty, `asset_partial` defaults to `"IMOEX"`. The test `test_asset_partial_empty_ticker` asserts that the response contains `"Введите тикер"`. Changing `asset_partial` to return an HTML response containing the warning string when the ticker parameter is empty will resolve this failure.
- **Observation 3** shows that the template `_track2.html` tries to render `p.unreal_pct`. In the test execution, the mock positions dictionaries lack the `"unreal_pct"` key, which triggers a `jinja2.exceptions.UndefinedError` when Jinja attempts to format the `Undefined` value. Adding `"unreal_pct"` (and `"duration_bars"` as a preventative measure) to the test helper `_track2_ctx_populated` and introducing defensive `is defined` statements to the template will resolve this error.
- **Observation 4** shows that `test_asset_form_has_datalist` asserts the existence of `<datalist` and `"GAZP"` in the output of `/ui/asset`. Since the dropdown was refactored into a `<select>` element, the `<datalist>` tag no longer exists, breaking the test. Restoring the text input with `list="tickers"` and `<datalist id="tickers">` will align the code with the test expectations.

---

## 3. Caveats

- We assumed that `tests/test_web.py` reflects the correct design specifications (such as having a datalist for asset tickers instead of a select dropdown, and displaying correlations in the portfolio view). If the dropdown design was a deliberate product change, the tests themselves might need to be rewritten. However, within the scope of Milestone 1, our objective is to make all baseline tests pass.
- We did not run tests on other files (e.g. `test_cli.py`, etc.) since they were not in the scope of this request, but they might be affected if shared logic is changed (though the proposed fixes are restricted to UI templates and test mocks).

---

## 4. Conclusion

The 4 failing tests in `tests/test_web.py` are caused by discrepancies between recent HTML template/route refactoring and the original test expectations.
Applying the diff in `/home/ijstt/News/.agents/explorer_web_fixes_1/proposed_fixes.patch` will resolve all 4 test failures.

---

## 5. Verification Method

To verify the proposed changes:

1. Apply the patch file:
   ```bash
   git apply /home/ijstt/News/.agents/explorer_web_fixes_1/proposed_fixes.patch
   ```
2. Execute the test command:
   ```bash
   .venv/bin/pytest tests/test_web.py
   ```
3. Invalidation condition: If the test run returns a non-zero exit code or any test in `test_web.py` still fails, the fix is invalid.
