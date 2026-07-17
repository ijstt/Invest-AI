## 2026-07-16T20:14:39Z

You are a Worker tasked with executing the refactoring of `src/geoanalytics/processing/common.py` and `src/geoanalytics/processing/reprocessing.py`.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Objectives:
1. Run the existing tests using the `.venv/bin/pytest` test runner for `tests/test_processing.py`, `tests/test_processing_adversarial.py`, and `tests/test_processing_stress.py`. Verify that all current tests pass.
2. In `src/geoanalytics/processing/common.py`:
   - Introduce `build_article_text` helper function to construct the full text cleanly from either an Article model (or duck-typed stub) or title/text string parameters. Ensure it handles duck-typing (`hasattr(..., "title")`) so it works with test stubs.
   - Introduce `execute_reprocessing` to generically drive batch/item processing with transaction savepoints (using `session.begin_nested()` or `contextlib.nullcontext()`), item-level exception handling, and error logging.
3. In `src/geoanalytics/processing/reprocessing.py`:
   - Refactor the 6 `_existing` reprocessing functions (`rescore_existing`, `reaspect_existing`, `retemporal_existing`, `refactuality_existing`, `renumeric_existing`, `reforecast_existing`) and `relink_existing` to use the new `execute_reprocessing` and `build_article_text` helper functions.
4. Run the tests again and verify 100% pass rate.
5. Ensure no modified or created file exceeds 600 lines of code.
6. Write a detailed handoff report to `.agents/worker_processing_3/handoff.md` with:
   - Observation: status of tests and files.
   - Logic Chain: what changes were made.
   - Caveats.
   - Conclusion.
   - Verification Method: commands to run.
7. Reply with a completion message once done.
