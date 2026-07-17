## Challenge Summary

**Overall risk assessment**: LOW

The applied web fixes and changes are robust. The core improvements resolved template crashes when fields such as `unreal_pct` or `duration_bars` are missing from positions, and improved user experience by introducing a searchable autocomplete ticker input. All automated tests pass, and adversarial edge-case inputs (empty tickers, non-existent tickers, script injection/XSS attempts, malformed numeric params) did not trigger any 500 Internal Server Errors or Python stack traces.

---

## Challenges

### [Low] Challenge 1: Cross-Site Scripting (XSS) on ticker input
- **Assumption challenged**: The change from a static `<select>` drop-down to a free-text `<input type="text">` allows users to submit arbitrary HTML/JavaScript tags (e.g., `<script>alert(1)</script>`) as the ticker name.
- **Attack scenario**: An attacker crafts a URL `/ui/asset?ticker=%3Cscript%3Ealert(1)%3C/script%3E` or inputs JavaScript in the search bar. If the template renders this without escaping, arbitrary JavaScript could execute in the user's browser.
- **Blast radius**: Low. The application uses Jinja2 with autoescaping enabled by default. Furthermore, the backend upper-cases the input (turning `<script>` to `<SCRIPT>`). In both the full page and the partial, the output is safely rendered as `&lt;SCRIPT&gt;ALERT(1)&lt;/SCRIPT&gt;`, rendering the payload harmless.
- **Mitigation**: Standard Jinja2 autoescaping is sufficient. No action needed.

### [Low] Challenge 2: Missing or invalid tickers
- **Assumption challenged**: The searchable text input allows users to type non-existent tickers.
- **Attack scenario**: A user queries an invalid ticker (e.g., `ticker=INVALID`). If the backend assumes the ticker always exists, it could lead to `NoneType` access or rendering errors.
- **Blast radius**: Low. `_asset_context` handles missing tickers by returning a report with `found=False`. The template `_asset_result.html` handles this condition gracefully by displaying "Актив не найден" (Asset not found) or "Введите тикер" (Enter ticker) without crashing.
- **Mitigation**: Already handled correctly.

---

## Stress Test Results

- **Empty / Space ticker query** → Expected: `HTMLResponse` stating "Введите тикер" (for partial) or defaulting to "IMOEX" (for full page). → Actual: Passed (no 500, correctly returned friendly message). → **PASS**
- **XSS injection (`<script>alert(1)</script>`)** → Expected: HTML-escaped string rendering textually rather than executing. → Actual: Rendered as `&lt;SCRIPT&gt;ALERT(1)&lt;/SCRIPT&gt;`. → **PASS**
- **Invalid strategy or range parameters** → Expected: Graceful fallback or error response. → Actual: Handled without crashing. → **PASS**
- **Malformed portfolio form submissions (non-numeric inputs)** → Expected: HTTP 422 Unprocessable Entity or ValueError catch rather than 500 error. → Actual: Status 422 (for validation errors) and status 200 (for caught ValueErrors). → **PASS**
- **Jinja2 evaluation of `(p.unreal_pct is defined and p.unreal_pct or 0) >= 0`** → Expected: Evaluates to True when `unreal_pct` is positive, missing, or None, and False when negative. → Actual: Confirmed via environment test. → **PASS**

---

## Unchallenged Areas

- **OAuth/Auth headers**: Authentication logic is outside the scope of Milestone 1.
- **Real-time updates**: We did not test real-time WebSocket connection stability since Milestone 1 is focused on baseline HTML/HTMX fixes.
