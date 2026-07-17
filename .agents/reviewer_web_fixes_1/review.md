# Quality & Adversarial Review Report

## Review Summary

**Verdict**: PASS

All 42 tests in `tests/test_web.py` pass successfully. The implemented fixes are correct, complete, and robust. There are no integrity violations or dummy/facade implementations. A few minor style/formatting issues were found but they do not block core functionality.

---

## Quality Review

### Findings

#### [Minor] Finding 1: Code Formatting Check Failure
- **What**: The formatting check (`ruff format --check`) fails.
- **Where**: `src/geoanalytics/api/web.py` and `tests/test_web.py`.
- **Why**: There are multiple formatting discrepancies (whitespace, line lengths, wrapping) compared to the standard format.
- **Suggestion**: Run `ruff format` on these files to align with the formatting standards. Since Reviewer 1 is restricted to a review-only role, this task is left for the implementer or orchestrator.

#### [Minor] Finding 2: HTML/CSS Styling for Undefined unreal_pct
- **What**: The CSS class for empty/undefined `unreal_pct` evaluates to `up` (green color).
- **Where**: `src/geoanalytics/api/templates/_track2.html`, line 273:
  ```html
  <td class="num {{ 'up' if (p.unreal_pct is defined and p.unreal_pct or 0) >= 0 else 'down' }}">
  ```
- **Why**: When `p.unreal_pct` is `None` or not defined, `(p.unreal_pct is defined and p.unreal_pct or 0)` evaluates to `0`. Since `0 >= 0` is `True`, it assigns the class `up` (green), even though the rendered value is `—` (dash).
- **Suggestion**: Modify the condition to:
  ```html
  <td class="num {% if p.unreal_pct is defined and p.unreal_pct is not none %}{{ 'up' if p.unreal_pct >= 0 else 'down' }}{% endif %}">
  ```

### Verified Claims

- **All 42 web tests pass** → verified via running `.venv/bin/pytest tests/test_web.py` → PASS (all 42 tests passed in 8.95 seconds).
- **Ruff lint checks pass** → verified via running `.venv/bin/ruff check` → PASS (all checks passed successfully).
- **Dynamic datalist option works as progressive enhancement** → verified via inspecting `src/geoanalytics/api/templates/asset.html` and `tests/test_web.py` → PASS (datalist allows typing/selecting assets correctly).

---

## Adversarial Review

**Overall Risk Assessment**: LOW

The web logic behaves correctly under resource constraints, empty inputs, and case-sensitive variants.

### Challenges

#### [Low] Challenge 1: Empty or Whitespace Ticker Parameter
- **Assumption challenged**: User input can be cleared or submitted empty.
- **Attack scenario**: Sending request to partials or graph pages with `ticker=""` or `ticker="   "`.
- **Blast radius**: Low. The server does not crash (500 Error).
- **Mitigation**: Verified that partial routes (`/ui/partials/asset`, `/ui/partials/asset/chart`, `/ui/partials/asset/indicators`, `/ui/partials/backtest`) check `not ticker.strip()` and return `<p class="muted">Введите тикер</p>` or equivalent HTML error message. The full page `/ui/asset` defaults to `"IMOEX"`. The `/ui/graph` page handles empty ticker gracefully and does not throw a database query error.

#### [Low] Challenge 2: Lowercase Ticker Input
- **Assumption challenged**: User inputs tickers in lowercase (e.g., `sber`, `imoex`).
- **Attack scenario**: Adding portfolio asset via `/ui/portfolio/add` or checking asset via `/ui/asset?ticker=sber`.
- **Blast radius**: Low.
- **Mitigation**: Verified that the backend functions (`build_report`, `_add_position`, `_asset_ohlcv`, `_indicators_context`) explicitly convert the ticker parameter to uppercase (`ticker.upper()`) before queries or entity retrieval.

### Stress Test Results

- **Empty Ticker** → Partial endpoints return placeholder error string → Pass.
- **Lowercase Ticker** → Normalized via `.upper()` and correctly fetched → Pass.
- **Divided by Zero on Average Price** → Guarded by `p.avg_price > 0` before calculating `unreal_pct` → Pass.
