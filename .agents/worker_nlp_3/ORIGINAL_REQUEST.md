## 2026-07-17T06:13:35Z
You are a worker subagent. Your working directory is /home/ijstt/News/.agents/worker_nlp_3/.
Objectives:
1. Examine git status and diff if any, to see the current state of files.
2. Implement `ModelLoader` in `src/geoanalytics/nlp/_seqcls.py` and refactor `classify.py`, `significance.py`, `temporal.py`, and `aspect.py` to use it, eliminating the duplicate `registry.get_model` and `registry.get_status` calls.
3. Ensure `sentiment.py` shares the `_is_full_model()` detection logic with `_seqcls.py`.
4. Fix any private import issues between `fundamentals.py` and `numeric.py`. Ensure symbols `MULT` and `to_float` are exposed properly as public API.
5. Run all pytest tests (e.g. `pytest tests/`) and ensure they pass 100%.
6. Verify that no file modified or created exceeds 600 lines.
7. Write your changes and test verification results in `/home/ijstt/News/.agents/worker_nlp_3/handoff.md`.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.
