# BRIEFING — 2026-07-16T23:14:50+03:00

## Mission
Refactor `src/geoanalytics/processing/common.py` and `src/geoanalytics/processing/reprocessing.py` to extract common article text builder and reprocessing execution logic.

## 🔒 My Identity
- Archetype: Worker
- Roles: implementer, qa, specialist
- Working directory: /home/ijstt/News/.agents/worker_processing_3
- Original parent: 379c472d-00da-41ba-bd97-1a26a539d36d
- Milestone: Processing Refactoring

## 🔒 Key Constraints
- Run existing tests using `.venv/bin/pytest` and verify they pass.
- In `src/geoanalytics/processing/common.py`:
  - Introduce `build_article_text` helper function (supports duck-typing/test stubs, builds text from either Article model or title/text parameters).
  - Introduce `execute_reprocessing` to generically drive batch/item processing with transaction savepoints (using `session.begin_nested()` or `contextlib.nullcontext()`), item-level exception handling, and error logging.
- In `src/geoanalytics/processing/reprocessing.py`:
  - Refactor 6 `_existing` reprocessing functions (`rescore_existing`, `reaspect_existing`, `retemporal_existing`, `refactuality_existing`, `renumeric_existing`, `reforecast_existing`) and `relink_existing` to use the new helpers.
- Run tests again and verify 100% pass rate.
- Ensure no modified or created file exceeds 600 lines of code.
- Write handoff report to `.agents/worker_processing_3/handoff.md`.

## Current Parent
- Conversation ID: 379c472d-00da-41ba-bd97-1a26a539d36d
- Updated: not yet

## Task Summary
- **What to build**: Common reprocessing driver `execute_reprocessing` and text builder `build_article_text` in `common.py`. Refactor the existing reprocessing functions to use them.
- **Success criteria**: 100% test pass rate, cleaner code, no files > 600 lines, detailed handoff.
- **Interface contracts**: [TBD]
- **Code layout**: Source in `src/geoanalytics/processing/`, tests in `tests/`.

## Key Decisions Made
- Extracted generic `execute_reprocessing` and `build_article_text` helper functions to `common.py`.
- Moved the result dataclasses (e.g. `RelinkResult`, `RescoreResult`, etc.) to `common.py` to keep the line count of `reprocessing.py` below 600 lines (554 lines total).

## Change Tracker
- **Files modified**: `src/geoanalytics/processing/common.py`, `src/geoanalytics/processing/reprocessing.py`
- **Build status**: PASS
- **Pending issues**: None

## Quality Status
- **Build/test result**: PASS (49 tests passed)
- **Lint status**: PASS (0 violations)
- **Tests added/modified**: Verified all standard, adversarial, and stress tests

## Loaded Skills
- None

## Artifact Index
- `/home/ijstt/News/.agents/worker_processing_3/ORIGINAL_REQUEST.md` — Original request context.
- `/home/ijstt/News/.agents/worker_processing_3/handoff.md` — Handoff report.
