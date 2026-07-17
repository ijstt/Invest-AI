# Handoff Report — 2026-07-17T04:19:30+03:00

## 1. Observation
We investigated the refactored NLP codebase in `src/geoanalytics/nlp/` and the new unit tests. In doing so, we verified the functionality and wrote robustness/stress tests under `tests/test_nlp_robustness.py` to target concurrency, corrupted configurations, missing settings, and model loader exceptions.

We observed the following:
* **Sentiment analyze exception propagation (sentiment.py)**:
  * In `src/geoanalytics/nlp/sentiment.py`, the settings loading and path validation occur outside the `try-except` block inside `_get_model()` (lines 140–145):
    ```python
    140:     settings = get_settings()
    141:     model_name = settings.sentiment_model
    142:     adapter = settings.sentiment_adapter_path
    143:     if adapter and not Path(adapter).exists():
    144:         log.warning("sentiment_adapter_missing", path=adapter)
    145:         adapter = None
    ```
  * In `analyze()` (lines 185–187), the `_get_model()` function is called outside the `try-except` block:
    ```python
    185:     model = _get_model()
    186:     if model is None:
    187:         return _lexicon_sentiment(text)
    ```
  * If `get_settings()` raises an exception or the settings are missing the required attributes, the exception propagates unhandled.
* **Registry loader exception propagation (_seqcls.py)**:
  * In `src/geoanalytics/nlp/_seqcls.py`, path checking is performed outside the `try-except` block in `load_seqcls_adapter()` (lines 76–78):
    ```python
    76:     if not path:
    77:         return None
    78:     if not Path(path).exists():
    ```
  * Passing an invalid type or triggering an filesystem `OSError` during path checks propagates the exception unhandled.
* **Concurrency behavior**:
  * Simultaneous requests under multi-threaded environments are thread-safe once the models/adapters are fully loaded. However, the first-time concurrent access has a small window for duplicate model loading because there is no mutex locking inside `SeqClsRegistry.get_model` or `_get_model` before writing to cache.

We ran the existing and new tests using pytest:
* Command: `.venv/bin/pytest tests/test_nlp.py tests/test_nlp_uncovered.py tests/test_nlp_robustness.py`
* Result: `40 passed in 5.30s` (confirming all unit and robustness assertions pass, validating that these exceptions propagate as asserted).

## 2. Logic Chain
1. **Hypothesis**: Corrupted configuration files, missing settings, and invalid paths cause unhandled crashes instead of graceful fallbacks.
2. **Step 1**: If `get_settings()` raises an exception or misses `sentiment_model`, `_get_model()` will raise `RuntimeError` or `AttributeError` on lines 140-141.
3. **Step 2**: Since `analyze()` calls `_get_model()` on line 185 without any try-catch wrapping, these exceptions propagate directly, crashing the sentiment analysis pipeline.
4. **Step 3**: If `load_seqcls_adapter()` is called with an invalid path argument (e.g. `12345`), the conversion/checks on lines 76-78 raise `TypeError` or `OSError`. Because the try-catch block only starts on line 85, this propagates to the registry, crashing the sequence classification pipeline.
5. **Validation**: We wrote `tests/test_nlp_robustness.py` asserting that these errors propagate as described. The pytest execution successfully confirmed the assertions.

## 3. Caveats
* Property-based testing was simulated using a wide variety of edge inputs in the unit test assertions.
* GPU memory constraints and actual PyTorch concurrent memory overhead under thread-based execution (due to CUDA contexts) were not stress tested on physical GPUs, as the execution environment uses CPU-based mocks for torch/transformers.

## 4. Conclusion
The refactored NLP codebase is correct under normal settings, but it has minor vulnerabilities in its fallback mechanism. Specifically:
1. `sentiment.analyze` crashes on corrupted settings/missing attributes instead of falling back to the lexicon.
2. `SeqClsRegistry` (and `load_seqcls_adapter`) crashes on invalid path types or filesystem errors.
3. Under highly concurrent initial requests, the models could be loaded redundantly.
Mitigation: Wrap the settings access in `_get_model` and path checks in `load_seqcls_adapter` inside their respective `try-except` blocks, or wrap the model acquisition inside the main calling routines.

## 5. Verification Method
To independently verify the test suite and confirm correctness, run:
```bash
.venv/bin/pytest tests/test_nlp.py tests/test_nlp_uncovered.py tests/test_nlp_robustness.py
```
Check that all 40 tests pass. Inspect `tests/test_nlp_robustness.py` for details on the assertions verifying the crash-reproduction logic.

---

## 🔒 Adversarial Review / Challenge Report

**Overall risk assessment**: MEDIUM

### Challenges

#### [Medium] Challenge 1: Unhandled settings exception in sentiment pipeline
* **Assumption challenged**: Settings are always valid and have required attributes when sentiment analysis is invoked.
* **Attack scenario**: A corrupted `.env` file causes a Pydantic `ValidationError` in `get_settings()`, or a configuration change drops `sentiment_model` from the settings.
* **Blast radius**: The entire news ingest pipeline or API sentiment endpoint crashes when processing any article, instead of falling back to lexicon.
* **Mitigation**: Move lines 140–145 inside the `try` block in `_get_model()` or catch all exceptions inside `analyze()`'s call to `_get_model()`.

#### [Low] Challenge 2: Unhandled path verification exception in registry loader
* **Assumption challenged**: The registry adapter path is always a valid path string or `None`.
* **Attack scenario**: An automated configuration script writes an invalid type or a network filesystem error raises an `OSError` when checking if the directory exists.
* **Blast radius**: The sequence classification engine crashes.
* **Mitigation**: Move lines 76–78 inside the `try` block in `load_seqcls_adapter()`.
