## Challenge Summary

**Overall risk assessment**: LOW

All web fixes applied for Milestone 1 are functionally complete and robust. They resolve the key template rendering crash (`UndefinedError` for `unreal_pct` / `duration_bars`) and restore missing UI components (portfolio correlations and ticker datalist autocomplete). Our adversarial stress testing showed that the endpoints survive invalid, extreme, and malicious inputs without server crashes (500).

## Challenges

### [Low] Challenge 1: Ticker whitespace handling

- **Assumption challenged**: Ticker search input is always stripped of leading/trailing spaces before database query lookups.
- **Attack scenario**: User enters `" sber "` or pastes it with spaces into the autocomplete search field. The application does not strip the whitespace and queries `Asset` with `" SBER "`. Since the database stores the ticker as `"SBER"`, the query returns `None`, and the user is shown the "Актив не найден" (Asset not found) message instead of the SBER analytics report.
- **Blast radius**: Low. Degraded user experience, but handles the missing asset gracefully without throwing any backend exceptions.
- **Mitigation**: Perform `.strip()` on input ticker strings at the entry point of the API controllers (e.g. `asset_partial` and `asset_page`) or inside `build_report` before looking it up in the database.

### [Low] Challenge 2: Missing fields in open positions

- **Assumption challenged**: The open positions data structure passed to the Jinja template `_track2.html` will always contain all attributes like `unreal_pct` and `duration_bars`.
- **Attack scenario**: If a position lacks tracking details (e.g., cash positions or new trades where the calculation loop has not finished), these fields are omitted from the position dict. In the previous implementation, Jinja would crash with an `UndefinedError` during formatting, crashing the entire `/ui/track2` page.
- **Blast radius**: Medium (page-wide crash).
- **Mitigation**: The implemented fixes use Jinja's `is defined` checks to safely render `—` for missing attributes, which prevents compilation crashes.

### [Low] Challenge 3: Extreme/negative inputs to portfolio additions

- **Assumption challenged**: Users only submit positive quantities when adding positions to the portfolio.
- **Attack scenario**: A user bypasses client-side forms and directly posts a negative quantity (e.g., `quantity=-10`) or zero to `/ui/portfolio/add`.
- **Blast radius**: Low. The database repository raises a `ValueError` which is caught by the web controller, avoiding changes to the database and preventing invalid short positions.
- **Mitigation**: The exception handler is in place and returns the portfolio HTML response with a 200 status code safely.

## Stress Test Results

- **Empty/whitespace ticker** (`/ui/partials/asset?ticker=`) → Returns `<p class="muted">Введите тикер</p>` → **PASS**
- **Non-existent/long/SQL-injection ticker** (`/ui/partials/asset?ticker=INVALID...`) → Returns "Актив не найден" safely without exceptions → **PASS**
- **Negative/zero quantity on portfolio addition** → Catches `ValueError` and renders the portfolio page safely → **PASS**
- **Missing unreal_pct/duration_bars in positions** → Renders `—` safely without Jinja `UndefinedError` → **PASS**

## Unchallenged Areas

- **OAuth/Authentication** — out of scope for Milestone 1.
- **WebSocket/Realtime UI Updates** — out of scope for Milestone 1.
