# Handoff Report

## 1. Observation
The following absolute file paths and lines were directly observed:
- **`src/geoanalytics/nlp/fundamentals.py`**
  - Line 17: `from geoanalytics.nlp.numeric import MULT, to_float`
  - Line 125: `value=to_float(m.group(1))`
  - Line 132: `value = to_float(m.group(1)) * MULT[m.group(2).lower()]`
- **`src/geoanalytics/nlp/numeric.py`**
  - Line 30-31:
    ```python
    MULT = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}
    _MULT = MULT
    ```
  - Line 97-101:
    ```python
    def to_float(raw: str) -> float:
        return float(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))


    _to_float = to_float
    ```
- **Tests execution**:
  - Run command: `.venv/bin/pytest tests/test_nlp.py tests/test_numeric.py tests/test_fundamentals.py tests/test_aspect.py tests/test_temporal.py tests/test_significance.py`
  - Output: `84 passed in 4.86s`
- **Internal API dependencies**:
  - `tests/test_nlp.py` imports `_lexicon_sentiment` (Line 10), `_get_classifier` (Line 71), `_label_to_event` (Line 78).
  - `tests/test_aspect.py` patches `_get_sentiment_model` and `_get_saliency_model`.
  - `tests/test_dataset.py` imports `_BUCKET_VALUE` (Line 145).
  - `scripts/eval_events.py` imports `_get_classifier` and `_classify_by_rules`.
  - `scripts/eval_significance.py` imports `_get_model`.

## 2. Logic Chain
1. We searched for references to `_to_float` and `_MULT` in the codebase.
2. Based on the observation in `src/geoanalytics/nlp/fundamentals.py:17`, `fundamentals.py` imports the public `MULT` and `to_float` rather than the private ones.
3. Therefore, `_MULT` and `_to_float` are local private aliases inside `numeric.py` and are not imported by `fundamentals.py`.
4. We verified that other files in the `geoanalytics` package import `_to_float` from other internal modules (like `geoanalytics.processing.common` and `geoanalytics.analytics.history`), but not from `nlp.numeric`.
5. We compiled the public APIs for `sentiment.py`, `classify.py`, `significance.py`, `temporal.py`, and `aspect.py` by inspecting their file definitions.
6. Since tests and evaluation scripts mock/import several private variables/functions (such as `_get_classifier`, `_lexicon_sentiment`, and `_BUCKET_VALUE`), any signature compatibility map must capture both the public module exports and these test-mocked internal APIs.
7. To enforce the 600-line limit constraint, we evaluated the line lengths of current files (all under 200 lines) and designed a modular split and CI check strategy.

## 3. Caveats
- We assumed that tests and helper scripts under `tests/` and `scripts/` are the only consumers of the internal underscored APIs. Any other external scripts not in the workspace weren't analyzed.
- Standard libraries and packages (`torch`, `peft`, `transformers`) are assumed to be loaded dynamically as per the existing codebase fallback structure.

## 4. Conclusion
- A comprehensive analysis is completed and written to `/home/ijstt/News/.agents/explorer_nlp2_3/analysis.md`.
- `fundamentals.py` does not import `_MULT` or `_to_float`; it imports `MULT` and `to_float`.
- The strict Signature Compatibility Map and a modularization/CI lint strategy to prevent files from exceeding 600 lines are proposed.

## 5. Verification Method
- Execute the NLP tests to ensure correctness of the existing implementation:
  ```bash
  .venv/bin/pytest tests/test_nlp.py tests/test_numeric.py tests/test_fundamentals.py tests/test_aspect.py tests/test_temporal.py tests/test_significance.py
  ```
- Inspect `/home/ijstt/News/.agents/explorer_nlp2_3/analysis.md` for the full detailed report.
