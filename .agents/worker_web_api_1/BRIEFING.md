# BRIEFING — 2026-07-17T09:23:39+03:00

## Mission
Implement the Web API Modularization plan for Milestone 4, ensuring all route handlers and helper functions are correctly structured across 7 sub-routers and re-exported from web.py.

## 🔒 My Identity
- Archetype: implementer
- Roles: implementer, qa, specialist
- Working directory: /home/ijstt/News/.agents/worker_web_api_1/
- Original parent: b718efd8-6df0-40e1-bd22-15372c192d0d
- Milestone: Milestone 4

## 🔒 Key Constraints
- All implementations must be genuine. DO NOT cheat or hardcode test results.
- No file in the project (modified or created) exceeds 600 lines.
- Sub-routers must resolve shared configurations and helper functions at runtime via `web.<name>`.
- Run pytest and ensure all 1216 tests pass 100%.

## Current Parent
- Conversation ID: b718efd8-6df0-40e1-bd22-15372c192d0d
- Updated: not yet

## Task Summary
- **What to build**: Split `src/geoanalytics/api/web.py` into 7 sub-router files (`dashboard.py`, `asset.py`, `backtest.py`, `portfolio.py`, `graph.py`, `alerts.py`, `factors.py`) under `src/geoanalytics/api/routers/`. Refactor `web.py` to centralize imports, include the sub-routers, and re-export helper functions.
- **Success criteria**: 100% test pass on tests/test_web.py and the full suite. All files under 600 lines.
- **Interface contracts**: `src/geoanalytics/api/web.py` and router interface.
- **Code layout**: Sub-routers in `src/geoanalytics/api/routers/`, central router in `src/geoanalytics/api/web.py`.

## Key Decisions Made
- [TBD]

## Artifact Index
- `/home/ijstt/News/.agents/worker_web_api_1/handoff.md` — Final Handoff Report
