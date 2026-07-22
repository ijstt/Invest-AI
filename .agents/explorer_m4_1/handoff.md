# Handoff Report: Web API Modularization (Milestone 4 Plan)

## 1. Observation
- File `src/geoanalytics/api/web.py` currently has **1,034 lines** (exceeding the 600 line per file limit).
- `src/geoanalytics/api/routers/` currently contains 3 files: `__init__.py` (2 lines), `dashboard.py` (81 lines), and `asset.py` (212 lines). However, `web.py` still contains duplicate route handler code and helper functions, and `app.py` line 46 mounts `web.router` directly via `app.include_router(web.router)`.
- Baseline test suite execution command `source .venv/bin/activate && pytest tests/` completed with result: `1228 passed, 2 warnings in 24.88s`.
- Test file `tests/test_regime_history.py` line 7 imports `from geoanalytics.api.web import _regime_strip`.
- Test files `tests/test_web.py` and `tests/test_web_adversarial.py` perform `monkeypatch.setattr(web, "<func>", mock)` on `web.build_report`, `web._asset_ohlcv`, `web._add_position`, `web._portfolio_context`, `web.manage`, etc.
- `deploy/pi/geo-dashboard.service` line 15 runs `geo serve --host 0.0.0.0 --port 8800`, which invokes `uvicorn.run("geoanalytics.api.app:app", ...)`. `src/geoanalytics/api/app.py` line 46 includes `web.router`.

## 2. Logic Chain
1. **Observation**: `src/geoanalytics/api/web.py` is 1,034 lines long, which violates the requirement that no single file in the project exceed 600 lines.
2. **Observation**: The endpoints in `web.py` naturally cluster into 8 domain groups: `dashboard`, `asset`, `backtest`, `portfolio`, `graph`, `factors`, `track2`, and `alerts`.
3. **Reasoning**: Extracting these 8 domain groups into dedicated sub-router files inside `src/geoanalytics/api/routers/` (`dashboard.py`, `asset.py`, `backtest.py`, `portfolio.py`, `graph.py`, `factors.py`, `track2.py`, `alerts.py`) will reduce `web.py` to a lightweight app assembler (~120 lines) and ensure all router files remain under 220 lines.
4. **Observation**: Existing test suites (`test_web.py`, `test_web_adversarial.py`, `test_regime_history.py`) rely on `web.<func>` attributes and monkeypatching `web.<func>`.
5. **Reasoning**: To maintain 100% test compatibility, `web.py` must define the shared templates and TTL cache engine, import all sub-routers, re-export the helper functions and query functions, and include each sub-router on `web.router`. Sub-routers will access helper functions via `web.<func>` so monkeypatching `web.<func>` in test suites propagates instantly into endpoint execution.

## 3. Caveats
- No code implementation was performed as this is a read-only investigation task.
- CLI modularization (Milestone 5) is out of scope for Milestone 4 and will be handled subsequently.

## 4. Conclusion
A complete, concrete refactoring plan for Web API Modularization (Milestone 4) has been produced in `.agents/explorer_m4_1/analysis.md`. The plan splits `web.py` into 8 sub-routers under `src/geoanalytics/api/routers/`, keeps `web.py` at ~120 lines, maintains 100% test compatibility, and ensures all single file line counts remain under 600 lines.

## 5. Verification Method
1. Execute full pytest test suite:
   ```bash
   source .venv/bin/activate && pytest tests/
   ```
   *Expected outcome*: 1228 passed.
2. Verify line count limit on all web API files:
   ```bash
   wc -l src/geoanalytics/api/web.py src/geoanalytics/api/routers/*.py
   ```
   *Expected outcome*: Every file < 600 lines.
3. Verify `geo serve` command startup:
   ```bash
   source .venv/bin/activate && python -c "from geoanalytics.api.app import app; print(app.title)"
   ```
   *Expected outcome*: Output `geoanalytics API`.
