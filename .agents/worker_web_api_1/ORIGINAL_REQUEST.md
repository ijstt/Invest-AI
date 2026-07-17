## 2026-07-17T06:23:39Z
You are tasked with implementing the Web API Modularization plan for Milestone 4.
Your working directory is `/home/ijstt/News/.agents/worker_web_api_1/`.

DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Objectives:
1. Read the modularization plan from `/home/ijstt/News/.agents/explorer_web_api_1/analysis.md` and the original `src/geoanalytics/api/web.py`.
2. Extract all route handlers and helper functions from `src/geoanalytics/api/web.py` and modularize them into the following 7 sub-router files under `src/geoanalytics/api/routers/`:
   - `dashboard.py` (Dashboard + News + Ask + Status)
   - `asset.py` (Asset Detail + Charts + Indicators)
   - `backtest.py` (Backtest results)
   - `portfolio.py` (Portfolio CRUD + stats)
   - `graph.py` (Single Asset & Market Radial Trees)
   - `alerts.py` (Alert feeds + Ack + Mutes)
   - `factors.py` (Market factors + Track 2 demo account)
3. Ensure that each sub-router file:
   - Sets up its own `router = APIRouter()` and registers its routes.
   - Imports `web` (e.g. `from geoanalytics.api import web`).
   - Resolves all shared configurations (like `web.templates`, `web._cached`, `web._invalidate_cache`, constants `_STRATEGIES`, `_ALERT_TYPES`, etc.) and all helper functions at runtime using `web.<name>` (e.g. `web._status_context()`, `web.build_report()`, `web._portfolio_context()`, etc.). This is critical so that monkeypatching inside `tests/test_web.py` (such as replacing `web.build_report` or `web._asset_context` with mocks) functions correctly.
4. Refactor `src/geoanalytics/api/web.py` to:
   - Retain all global constants, template paths (`templates = Jinja2Templates(...)`), routing setup (`router = APIRouter()`), and the caching mechanism.
   - Import all 7 sub-routers and include them in the central `router` (using `router.include_router(dashboard.router)`, etc.).
   - Re-export all internal helper functions and modules so they are directly accessible as attributes on the `web` module (e.g., `_asset_context = asset._asset_context`, `_portfolio_context = portfolio._portfolio_context`, etc., as detailed in the plan).
5. Verify that no file in the project (modified or created) exceeds 600 lines.
6. Verify your implementation by running pytest (e.g., `.venv/bin/pytest tests/test_web.py` and/or the full suite). Ensure all 1216 tests pass 100%.
7. Write your handoff report to `/home/ijstt/News/.agents/worker_web_api_1/handoff.md`. Include the list of files created/modified, the line counts of each, and the verification commands and outputs.
