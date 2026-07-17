# Handoff Report — Explorer NLP2

## 1. Observation
- Verified codebase in `/home/ijstt/News/src/geoanalytics/nlp/`.
- Duplicate adapter loader cache and `model_status` logic observed across `classify.py` (lines 63-73, 76-83), `significance.py` (lines 149-166, 169-180), `temporal.py` (lines 114-125, 137-144), and `aspect.py` (lines 39-56, 88-104).
- Redundant class-level static wrappers `_is_full_model()` delegating to module-level `is_full_model()` in `_seqcls.py` (lines 33-35) and `sentiment.py` (lines 116-118).
- Redundant private aliases `_MULT` and `_to_float` in `numeric.py` (lines 31, 101) despite `fundamentals.py` (line 17) importing the public versions `MULT` and `to_float`.
- Extant tests in `tests/test_nlp_uncovered.py` failed due to import namespace shadowing (lines 128, 167), missing `torch.__spec__` on mocked module, and lack of `httpx.Request` instance on mock `httpx.Response` objects.

## 2. Logic Chain
- Step 1: Centralizing sequence classifier loading configs into a unified `ModelConfig` schema and `SeqClsRegistry` inside `_seqcls.py` removes cache setup and status rendering duplication.
- Step 2: Removing the redundant static methods `_is_full_model()` and invoking `is_full_model()` directly deletes duplicate helper methods without altering functionality.
- Step 3: Eliminating private aliases `_to_float` and `_MULT` in `numeric.py` clean-ups internal references to match external imports in `fundamentals.py`.
- Step 4: Resolving import shadowing, mock module specifications, and mock HTTP responses fixes the existing unit tests.

## 3. Caveats
- Settings configured via `get_settings()` are assumed to be static.
- Tests rely on mocks to avoid large network weights downloads.

## 4. Conclusion
- A unified caching loader design has been constructed to clear up logic in classify, significance, temporal, and aspect modules.
- Duplicate full-model checks and private import structures are resolved.
- A comprehensive unit test debugging and expansion plan is mapped out in the report.

## 5. Verification Method
- Execute tests:
  `PYTHONPATH=src .venv/bin/pytest tests/test_nlp_uncovered.py`
- Verify static analysis:
  `.venv/bin/ruff check src/geoanalytics/nlp/`
