## Review Summary

**Verdict**: APPROVE

## Findings

### Minor Finding 1: Lack of Explicit Device Handling for Inputs in `SeqClsAdapter`

- **What**: PyTorch input tensors are not explicitly moved to the model's device during inference.
- **Where**: `src/geoanalytics/nlp/_seqcls.py`, lines 64-69
- **Why**: Currently, models load on the CPU by default, so both the model and the inputs reside on the CPU. However, if GPU execution is configured or the model is manually moved to a GPU (e.g., `model.to('cuda')`), calling `self._model(**inputs)` will raise a PyTorch `RuntimeError` due to a device mismatch.
- **Suggestion**: Update `predict_label` to move input tensors to the same device as the model:
  ```python
  inputs = {k: v.to(self._model.device) for k, v in inputs.items()}
  ```

### Minor Finding 2: Lack of Explicit Device Handling for Inputs in `_RubertSentiment`

- **What**: PyTorch input tensors are not explicitly moved to the model's device during inference in the sentiment module.
- **Where**: `src/geoanalytics/nlp/sentiment.py`, lines 130-136
- **Why**: Similar to `SeqClsAdapter`, this will fail with a device mismatch if the model is moved to a GPU.
- **Suggestion**: Update `predict` to move input tensors to the same device as the model:
  ```python
  inputs = {k: v.to(self._model.device) for k, v in inputs.items()}
  ```

## Verified Claims

- **Refactored modules load model/tokenizer lazily** → Verified by inspection of lazy imports in `SeqClsAdapter.__init__`, `_RubertSentiment.__init__` and mock loading tests in `tests/test_nlp_empirical.py` and `tests/test_nlp_uncovered.py` → **PASS**
- **Graceful fallback is active when models fail to load or are unconfigured** → Verified via unit tests (`test_sentiment_load_failure_fallback_to_lexicon`, `test_aspect_unconfigured`, etc.) and code inspection showing `try-except` wrappers around imports/loading returning `None` and falling back to lexicon or rules → **PASS**
- **Status reporting returns "degraded" when configuration exists but load fails** → Verified by inspecting status methods and checking `test_model_status_degraded_when_configured_but_failed` and similar registry tests → **PASS**
- **Temporal anchoring matches status correctly** → Verified by inspecting date extraction matching with FUTURE/PAST statuses and corresponding unit tests in `tests/test_temporal.py` → **PASS**
- **All tests in tests/ suite pass successfully** → Verified by running `pytest tests/` → **PASS**

## Coverage Gaps

- **GPU Inference Paths** — risk level: low — recommendation: accept risk (CPU execution is sufficient for RSS/news stream throughput and keeps resource footprints low).

## Unverified Items

- **Actual production weights performance** — reason not verified: Model weights are not checked in or loaded in the test environment (stubs/rules are used/mocked). Verified the loader interfaces and configuration fallbacks instead.
