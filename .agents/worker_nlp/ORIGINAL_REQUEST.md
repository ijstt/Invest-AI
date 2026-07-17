## 2026-07-16T20:24:03Z
Objective: Implement the NLP refactoring and add unit tests.
Your working directory is /home/ijstt/News/.agents/worker_nlp/

Detailed design specifications can be found in the Explorer handoff report:
/home/ijstt/News/.agents/explorer_nlp_3/handoff.md

Instructions:
1. Create `load_seqcls_adapter` and `is_full_model` helper functions in `src/geoanalytics/nlp/_seqcls.py`.
2. Update the custom `_is_full_model` methods in `SeqClsAdapter` and `_RubertSentiment` (in `src/geoanalytics/nlp/sentiment.py`) to delegate to `is_full_model`.
3. Eliminate duplicate loader logic in `src/geoanalytics/nlp/classify.py`, `src/geoanalytics/nlp/significance.py`, `src/geoanalytics/nlp/temporal.py`, and `src/geoanalytics/nlp/aspect.py` by using the new `load_seqcls_adapter` helper.
4. Clean up private imports:
   - Expose `_MULT` and `_to_float` in `src/geoanalytics/nlp/numeric.py` as public `MULT` and `to_float`. Keep `_MULT` and `_to_float` as backward-compatibility aliases in `numeric.py`.
   - Update `src/geoanalytics/nlp/fundamentals.py` and `src/geoanalytics/connectors/smartlab.py` to import and use the new public API symbols.
5. Create a new test file `tests/test_nlp_uncovered.py` and implement comprehensive unit tests for `ner.py`, `embeddings.py`, `llm.py`, and `_seqcls.py` as designed in the handoff.
6. Verify file sizes: confirm that no modified or created file exceeds 600 lines.
7. Run all pytest unit and integration tests (including existing ones and the new `test_nlp_uncovered.py`) to ensure 100% pass rate.
8. Document all completed changes, line counts of modified/created files, test commands, and execution outputs in /home/ijstt/News/.agents/worker_nlp/handoff.md.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.
