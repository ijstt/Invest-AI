# Progress Log

- [x] Refactor `src/geoanalytics/nlp/_seqcls.py` to add `ModelConfig` and `SeqClsRegistry`.
- [x] Refactor `src/geoanalytics/nlp/classify.py`, `src/geoanalytics/nlp/significance.py`, `src/geoanalytics/nlp/temporal.py`, and `src/geoanalytics/nlp/aspect.py` to use `SeqClsRegistry`.
- [x] Remove `_is_full_model` from `SeqClsAdapter` and `_RubertSentiment`, importing and using `is_full_model` directly.
- [x] Clean up `src/geoanalytics/nlp/numeric.py` and remove `_MULT`/`_to_float`. Verify `fundamentals.py` is unaffected.
- [x] Replace `tests/test_nlp_uncovered.py` with proposed tests + fixes.
- [x] Run pytest to verify all tests pass.
- [x] Verify line counts of all modified files.

Last visited: 2026-07-17T04:17:35Z
