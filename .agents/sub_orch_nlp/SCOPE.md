# Scope: NLP Refactoring & Tests

## Architecture
- `src/geoanalytics/nlp/`: Package containing various NLP modules (`classify.py`, `significance.py`, `temporal.py`, `aspect.py`, `sentiment.py`, `fundamentals.py`, `numeric.py`).

## Objectives
- Create a shared model adapter loader in `nlp/_seqcls.py` to eliminate the copy-pasted `SeqClsAdapter` loading logic in `classify.py`, `significance.py`, `temporal.py`, and `aspect.py`.
- Refactor `sentiment.py` so that its custom `_RubertSentiment` class shares the `_is_full_model()` detection logic with `_seqcls.py`.
- Fix Private Imports: `nlp/fundamentals.py` currently imports private symbols (`_MULT`, `_to_float`) from `nlp/numeric.py`. Expose them properly as public API or extract them to a shared `_utils.py` module (or other public exposure).
- Add new unit tests for previously uncovered modules: `nlp/ner.py`, `nlp/embeddings.py`, `nlp/llm.py`, and `nlp/_seqcls.py`.
- Ensure all refactored/created files are strictly under 600 lines.
- Preserve all public API signatures and functionality.

## Completion Criteria
- Pytest runs and passes 100%, including the newly created unit tests.
- Verified by Reviewer and Forensic Auditor (CLEAN verdict).
