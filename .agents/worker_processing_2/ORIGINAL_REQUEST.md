## 2026-07-17T01:20:06Z
You are Worker 2 (gen 3). Your working directory is `/home/ijstt/News/.agents/worker_processing_2/`.
Your task is to refine the generic iterator `paginate_query` in `src/geoanalytics/processing/common.py`.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Context & Issue:
Challenger 2 identified a transaction rollback vulnerability. When calling `paginate_query`, if an exception propagates out of the caller's loop, Python raises `GeneratorExit` inside the generator at the `yield` statement. Because `session_scope()` only catches `Exception`, it bypasses explicit `session.rollback()`.

Objective:
1. Modify `paginate_query` in `src/geoanalytics/processing/common.py`. Wrap the `yield session, batch` statement in a `try...except BaseException:` block:
   ```python
            try:
                yield session, batch
            except BaseException:
                session.rollback()
                raise
   ```
2. Verify your change by running the test suite via pytest (`pytest tests/test_processing.py` and `pytest tests/`). Ensure all tests pass 100%.
3. Write your completion report in `/home/ijstt/News/.agents/worker_processing_2/handoff.md` and send a message when done.
