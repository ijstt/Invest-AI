# Progress Tracker - NLP Refactoring & Tests

Last visited: 2026-07-17T04:22:15+03:00

- [x] Step 1: Create `load_seqcls_adapter` and `is_full_model` helper functions in `src/geoanalytics/nlp/_seqcls.py`. (Verified already present)
- [x] Step 2: Update custom `_is_full_model` methods in `SeqClsAdapter` and `_RubertSentiment` (in `src/geoanalytics/nlp/sentiment.py`) to delegate to `is_full_model`.
- [x] Step 3: Refactor loaders in `classify.py`, `significance.py`, `temporal.py`, and `aspect.py` to use `load_seqcls_adapter`. (Verified already using `registry` which delegates to the helper)
- [x] Step 4: Clean up private imports/exposures in `numeric.py`, `fundamentals.py`, and `smartlab.py`. (Exposed public names, added compatibility aliases `_MULT` and `_to_float`)
- [x] Step 5: Implement `tests/test_nlp_uncovered.py`. (Completed with delegation tests added)
- [ ] Step 6: Verify file sizes and run tests. (Line counts verified, running all tests now)
- [ ] Step 7: Finalize handoff report.
