# Handoff Report: Web Fixes (Milestone 1)

This handoff contains all the observations, logical inferences, caveats, conclusions, and verification methods to fix the 4 failing web tests in Milestone 1.

## 1. Observation

We executed the test suite against `tests/test_web.py` using:
```bash
.venv/bin/pytest tests/test_web.py
```

The command returned exit code `1` with exactly 4 failures:

### Failure A: `test_portfolio_page_with_positions`
* **Log Error**:
  ```
  FAILED tests/test_web.py::test_portfolio_page_with_positions - assert 'Корреляции холдингов' in r.text
  E       assert 'Корреляции холдингов' in '<!doctype html>\n<html lang="ru">...
  ```
* **Context**: `tests/test_web.py:113`

### Failure B: `test_asset_partial_empty_ticker`
* **Log Error**:
  ```
  FAILED tests/test_web.py::test_asset_partial_empty_ticker - assert 'Введите тикер' in r.text
  E       assert 'Введите тикер' in '\n\n<div style="margin-bottom:6px;">\n  <h1 style="margin-bottom:2px;">Индекс МосБиржи <span class="muted">(IMOEX)</span>...
  ```
* **Context**: `tests/test_web.py:271`

### Failure C: `test_track2_page`
* **Log Error**:
  ```
  FAILED tests/test_web.py::test_track2_page - jinja2.exceptions.UndefinedError: 'dict object' has no attribute 'unreal_pct'
  ...
  src/geoanalytics/api/templates/_track2.html:274: in top-level template code
      {% if p.unreal_pct is not none %}{{ "%+.2f"|format(p.unreal_pct) }}%{% else %}—{% endif %}
  ```
* **Context**: `tests/test_web.py:362` and `src/geoanalytics/api/templates/_track2.html:274`

### Failure D: `test_asset_form_has_datalist`
* **Log Error**:
  ```
  FAILED tests/test_web.py::test_asset_form_has_datalist - assert ('<datalist' in ...
  E       assert ('<datalist' in '<!doctype html>\n<html lang="ru">...
  ```
* **Context**: `tests/test_web.py:440` and `src/geoanalytics/api/templates/asset.html`

---

## 2. Logic Chain

1. **Failure A (`test_portfolio_page_with_positions`)**:
   - *Observation*: The test mocks `_portfolio_context` to provide `correlations: [{"pair": "SBER / GAZP", "r": 0.6}]` and asserts `"Корреляции холдингов"` is in the HTML.
   - *Trace*: Searching `src/geoanalytics/api/templates/portfolio.html` shows no references to `correlations` or the Russian string `"Корреляции холдингов"`.
   - *Inference*: The template is missing the holding correlations panel, which must be added to list the items returned under the `correlations` context key.

2. **Failure B (`test_asset_partial_empty_ticker`)**:
   - *Observation*: The test hits `/ui/partials/asset?ticker=` expecting a response containing `"Введите тикер"`.
   - *Trace*: In `src/geoanalytics/api/web.py:921`, the route handler `asset_partial` defaults the ticker parameter to `"IMOEX"` if it is empty:
     ```python
     if not ticker or not ticker.strip():
         ticker = "IMOEX"
     ```
   - *Inference*: Changing this route to return a warning message if `ticker.strip()` is empty (matching `asset_chart_partial` and `asset_indicators_partial`) resolves the failure.

3. **Failure C (`test_track2_page`)**:
   - *Observation*: Standard Jinja context lookup evaluates missing attributes in dicts as `Undefined`. In the test, mock positions do not contain `unreal_pct` or `duration_bars`.
   - *Trace*:
     - In `_track2.html:274`, `p.unreal_pct is not none` is `True` because `Undefined` is not `None`.
     - In `_track2.html:281`, math is executed (`p.duration_bars / 12`).
     - Both trigger `UndefinedError` when the template compiler evaluates them.
   - *Inference*: Adding defensive `is defined` check-guards (e.g. `p.unreal_pct is defined and ...`) guarantees they safely default to `none` without triggering errors.

4. **Failure D (`test_asset_form_has_datalist`)**:
   - *Observation*: The test asserts that `<datalist` is in the returned text of `/ui/asset`.
   - *Trace*: In `src/geoanalytics/api/templates/asset.html:9`, a `<select>` dropdown menu is used. No `<datalist>` or matching text `<input>` exists in the search form.
   - *Inference*: Replacing the dropdown with an input pointing to a `<datalist>` (similar to `backtest.html`) aligns the template with the test expectation.

---

## 3. Caveats

* We assumed the list of assets returned by `list_assets` is sufficient for the `<datalist>` autocomplete, which is true because the dropdown select already iterated over `assets`.
* No other systems were altered; fixes are isolated strictly to the frontend templates and the `asset_partial` route logic.

---

## 4. Conclusion

The 4 failing tests in `tests/test_web.py` can be fully resolved by:
1. Adding the `correlations` rendering loop to `portfolio.html`.
2. Modifying `asset_partial` in `web.py` to check for empty ticker queries and return an error message response.
3. Adding defensive `is defined` guards to `unreal_pct` and `duration_bars` lookups inside `_track2.html`.
4. Replacing the select dropdown in `asset.html` with an input + datalist structure.

A complete diff patch has been generated at:
`/home/ijstt/News/.agents/explorer_web_fixes_3/web_fixes.patch`

---

## 5. Verification Method

To verify the proposed fix, the implementer should:
1. Apply the patch `web_fixes.patch`.
2. Run pytest on the target test suite:
   ```bash
   .venv/bin/pytest tests/test_web.py
   ```
3. The test suite should pass completely (42 passed).
