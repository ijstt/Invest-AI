# Handoff Report: Web API Modularization Analysis (Milestone 4)

**Agent**: Explorer 2 (`explorer_m4_2`)  
**Date**: 2026-07-22  
**Working Directory**: `/home/ijstt/News/.agents/explorer_m4_2`  

---

## 1. Observation

1. **Baseline Test Execution**:
   Command: `source .venv/bin/activate && pytest tests/`
   Result output: `1228 passed, 2 warnings in 21.54s` (Task ID: `task-21`). 100% test pass rate confirmed.

2. **Source Code Inspection**:
   - `src/geoanalytics/api/web.py`: 1,034 lines total (`wc -l src/geoanalytics/api/web.py`).
   - `src/geoanalytics/api/routers/`: Contains `__init__.py` (1 line), `asset.py` (211 lines), `dashboard.py` (80 lines).
   - `src/geoanalytics/api/app.py`: 142 lines, contains `from geoanalytics.api import web` and `app.include_router(web.router)`.

3. **Endpoint Inventory**:
   - `src/geoanalytics/api/web.py` defines 27 total `@router.get` and `@router.post` endpoints (lines 615, 754, 760, 766, 772, 778, 795, 828, 837, 843, 860, 869, 880, 886, 892, 902, 911, 921, 929, 942, 951, 961, 985, 994, 1002, 1012, 1026).

4. **Test Dependency Pattern**:
   - `tests/test_web.py` imports `from geoanalytics.api import web` and patches `web.<function>` using `monkeypatch.setattr(web, "build_snapshot", ...)`, `monkeypatch.setattr(web, "_portfolio_context", ...)`, `monkeypatch.setattr(web, "_add_position", ...)`, etc.
   - `tests/test_regime_history.py:7` imports `from geoanalytics.api.web import _regime_strip`.

---

## 2. Logic Chain

1. **Observation 1** confirms that all 1,228 unit tests currently pass. Any refactoring must preserve 100% pass rate.
2. **Observation 2** shows `web.py` is currently 1,034 lines, exceeding the 600-line project size limit requirement.
3. **Observation 2 & 3** show that `routers/asset.py` and `routers/dashboard.py` were partially created but not yet included in `web.py`, and 27 endpoints remain in `web.py`.
4. **Observation 4** demonstrates that unit tests interact with `web.py` by monkeypatching functions directly on `web`.
5. Therefore, `web.py` can be refactored into a lightweight assembler (<200 lines) by extracting remaining endpoints into domain routers (`portfolio.py`, `graph.py`, `factors.py`, `backtest.py`, `track2.py`, `alerts.py`) under `src/geoanalytics/api/routers/`.
6. To maintain test compatibility, sub-router handlers should invoke `web._context_fn(...)` dynamically so `monkeypatch.setattr(web, ...)` in `test_web.py` continues to work without modifying test files.

---

## 3. Caveats

- `src/geoanalytics/api/charts.py` currently stands at 858 lines. While `charts.py` is a standalone internal SVG chart generator and outside the scope of `web.py` router extraction, future refactoring under project file size rules may address it if required.
- M5 (CLI modularization) depends on M4 completion and is currently planned.

---

## 4. Conclusion

- `src/geoanalytics/api/web.py` is ready for extraction into modular routers in `src/geoanalytics/api/routers/`.
- The recommended router decomposition is:
  1. `routers/dashboard.py` (~85 lines)
  2. `routers/asset.py` (~215 lines)
  3. `routers/portfolio.py` (~190 lines)
  4. `routers/graph.py` (~230 lines)
  5. `routers/factors.py` & `routers/backtest.py` (~120 lines combined)
  6. `routers/track2.py` (~150 lines)
  7. `routers/alerts.py` (~110 lines)
- `web.py` will serve as the app router assembler (~150 lines) including all sub-routers and exporting symbols needed for test monkeypatching.
- All target files will be well below 600 lines.

---

## 5. Verification Method

1. Run full test suite:
   ```bash
   source .venv/bin/activate && pytest tests/
   ```
2. Verify line counts:
   ```bash
   wc -l src/geoanalytics/api/web.py src/geoanalytics/api/routers/*.py
   ```
   *Expected Result*: All files < 600 lines.
3. Verify CLI status command:
   ```bash
   ./geo-ctl.sh status
   ```
