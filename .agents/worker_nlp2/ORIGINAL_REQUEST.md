## 2026-07-17T01:14:29Z

You are a Worker agent with TypeName `teamwork_preview_worker`.
Your working directory is `/home/ijstt/News/.agents/worker_nlp2/`.

Objectives:
1. Implement the refactoring in `src/geoanalytics/nlp/_seqcls.py` as analyzed by the Explorers (reference `/home/ijstt/News/.agents/explorer_nlp2_1/analysis.md`). Specifically, create a `ModelConfig` dataclass and a `SeqClsRegistry` registry class to handle loading adapters and returning status, replacing duplicate code in:
   - `src/geoanalytics/nlp/classify.py`
   - `src/geoanalytics/nlp/significance.py`
   - `src/geoanalytics/nlp/temporal.py`
   - `src/geoanalytics/nlp/aspect.py`
2. Remove `@staticmethod _is_full_model` from `SeqClsAdapter` in `_seqcls.py` and from `_RubertSentiment` in `sentiment.py`. Import and use the shared `is_full_model` function in `_seqcls.py` directly.
3. Clean up `src/geoanalytics/nlp/numeric.py` by removing the private aliases `_MULT` and `_to_float` and replacing their usages with `MULT` and `to_float`. Make sure `nlp/fundamentals.py` continues to import and use the public `MULT` and `to_float`.
4. Replace the contents of `tests/test_nlp_uncovered.py` with the proposed unit test suite (reference `/home/ijstt/News/.agents/explorer_nlp2_2/analysis.md`), which includes proper tests and mocks for `nlp/ner.py`, `nlp/embeddings.py`, `nlp/llm.py`, and `nlp/_seqcls.py`. Resolve any namespace shadowing or pytest mock errors (e.g. `torch.__spec__` and `httpx.Response` request mock issues).
5. Ensure all modified or created files are strictly under 600 lines.
6. Verify your implementation by running the test suite using pytest.

MANDATORY INTEGRITY WARNING:
> DO NOT CHEAT. All implementations must be genuine. DO NOT
> hardcode test results, create dummy/facade implementations, or
> circumvent the intended task. A Forensic Auditor will independently
> verify your work. Integrity violations WILL be detected and your
> work WILL be rejected.

Please write a handoff report at `/home/ijstt/News/.agents/worker_nlp2/handoff.md` and send a message back to the parent once completed.
