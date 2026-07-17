# Original User Request

## Request — 2026-07-17T04:10:58+03:00

You are a Sub-Orchestrator tasked with completing Milestone 3: NLP Refactoring & Tests.
Your working directory is `/home/ijstt/News/.agents/sub_orch_nlp_2/`.
Your parent conversation ID is 21146468-b70a-4f0d-833a-6b21d87e2b4f.

Objectives:
- Read the Scope document `/home/ijstt/News/.agents/sub_orch_nlp_2/SCOPE.md`.
- Read `/home/ijstt/News/.agents/ORIGINAL_REQUEST.md` for context.
- Create a shared model adapter loader in `/home/ijstt/News/src/geoanalytics/nlp/_seqcls.py` to eliminate the copy-pasted `SeqClsAdapter` loading logic in `classify.py`, `significance.py`, `temporal.py`, and `aspect.py`.
- Refactor `sentiment.py` so that its custom `_RubertSentiment` class shares the `_is_full_model()` detection logic with `_seqcls.py`.
- Fix private imports: `nlp/fundamentals.py` imports private symbols `_MULT` and `_to_float` from `nlp/numeric.py`. Expose them properly as public API (e.g. by removing the leading underscore and exporting/importing them as `MULT` and `to_float`) or extract them to a shared helper.
- Add new unit tests for previously uncovered modules: `nlp/ner.py`, `nlp/embeddings.py`, `nlp/llm.py`, and `nlp/_seqcls.py`.
- Ensure all refactored/created files are strictly under 600 lines.
- Preserve strict public APIs.

Output requirements:
- Write `handoff.md` in `/home/ijstt/News/.agents/sub_orch_nlp_2/` summarizing what was done, what was changed, and verification evidence.
- Send a completion message back to the parent once done.

Completion criteria:
- All unit and integration tests pass 100%.
- New unit tests exist and pass for the 4 modules.
- No file modified or created exceeds 600 lines.
- Verified by Reviewers and Forensic Auditor with a CLEAN verdict.
