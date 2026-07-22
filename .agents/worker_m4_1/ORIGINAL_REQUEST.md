## 2026-07-22T16:02:35Z
You are Worker 1 assigned to implement Milestone 4: Web API Modularization for the Invest-AI project at /home/ijstt/News.
Your working directory is: /home/ijstt/News/.agents/worker_m4_1

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Scope & Guidance:
Read the Explorer analysis report at:
- `/home/ijstt/News/.agents/explorer_m4_1/analysis.md`
- `/home/ijstt/News/.agents/explorer_m4_2/analysis.md`

Tasks:
1. Extract endpoints from `src/geoanalytics/api/web.py` into sub-router files under `src/geoanalytics/api/routers/`:
   - `dashboard.py`
   - `asset.py`
   - `backtest.py`
   - `portfolio.py`
   - `graph.py`
   - `factors.py`
   - `track2.py`
   - `alerts.py`
2. Refactor `src/geoanalytics/api/web.py` so it serves purely as a lightweight app assembler that mounts all sub-routers, retains shared constants/cache engine, and re-exports helper functions and modules so that tests in `tests/test_web.py` (which monkeypatch `web.<helper>`) and other modules continue to pass without modifications.
3. Purely structural refactoring: keep public APIs intact, retain all existing code comments.
4. Ensure no file exceeds 600 lines of code.
5. Run unit tests (`source .venv/bin/activate && pytest tests/`) and verify all tests pass (100% pass rate).
6. Write your handoff report to `/home/ijstt/News/.agents/worker_m4_1/handoff.md` including exact build/test output and line counts.

Send your completion message back to the orchestrator via send_message when done.
