## 2026-07-16T20:22:05Z

Objective: Investigate NLP codebase refactoring and unit test requirements.
Your working directory is /home/ijstt/News/.agents/explorer_nlp_2/

1. Read the Scope document at /home/ijstt/News/.agents/sub_orch_nlp/SCOPE.md.
2. Read /home/ijstt/News/.agents/ORIGINAL_REQUEST.md for context.
3. Investigate the duplicate SeqClsAdapter loading logic in:
   - src/geoanalytics/nlp/classify.py
   - src/geoanalytics/nlp/significance.py
   - src/geoanalytics/nlp/temporal.py
   - src/geoanalytics/nlp/aspect.py
   Analyze how they currently load SeqClsAdapter and propose a clean shared model adapter loader interface in src/geoanalytics/nlp/_seqcls.py.
4. Investigate src/geoanalytics/nlp/sentiment.py. Locate the custom _RubertSentiment class and analyze how it detects full models. Propose how to extract the _is_full_model() detection logic so it is shared between sentiment.py and _seqcls.py.
5. Investigate src/geoanalytics/nlp/fundamentals.py and src/geoanalytics/nlp/numeric.py. Identify where fundamentals.py imports private symbols _MULT and _to_float from numeric.py. Propose how to expose them as public API (or extract to a shared location) without breaking existing usage.
6. Design unit tests for previously uncovered modules:
   - src/geoanalytics/nlp/ner.py
   - src/geoanalytics/nlp/embeddings.py
   - src/geoanalytics/nlp/llm.py
   - src/geoanalytics/nlp/_seqcls.py
   Ensure you locate mock fixtures or dependencies in existing tests to design self-contained, fast, and robust tests.
7. Verify file sizes: check current line counts of files to modify to ensure none will exceed 600 lines.
8. Write your findings and recommendations to /home/ijstt/News/.agents/explorer_nlp_2/handoff.md.
9. Report back to parent with a short message when complete.
