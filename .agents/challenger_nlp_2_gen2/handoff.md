# Handoff Report - NLP Correctness and Verification

## 1. Observation
- Verified file paths:
  - `src/geoanalytics/nlp/_seqcls.py` (helpers: `is_full_model`, `load_seqcls_adapter`)
  - `src/geoanalytics/nlp/numeric.py` (helpers: `to_float`, `MULT`)
  - `tests/test_nlp_uncovered.py` (original coverage tests)
- Added new test file:
  - `tests/test_nlp_adversarial.py`
- Executed commands:
  - `.venv/bin/pytest tests/test_nlp_uncovered.py` output:
    ```
    tests/test_nlp_uncovered.py ........................                     [100%]
    ============================== 24 passed in 5.71s ==============================
    ```
  - `.venv/bin/pytest tests/test_nlp_adversarial.py` output:
    ```
    tests/test_nlp_adversarial.py ...........                                [100%]
    ============================== 11 passed in 4.55s ==============================
    ```
  - Combined run: `.venv/bin/pytest tests/test_nlp_uncovered.py tests/test_nlp_adversarial.py` output:
    ```
    tests/test_nlp_uncovered.py ........................                     [ 68%]
    tests/test_nlp_adversarial.py ...........                                [100%]
    ============================== 35 passed in 5.13s ==============================
    ```
- Specific behavioral discoveries:
  - In Python 3.12, `Path.exists()` catches `ValueError` (such as from null-byte embedded paths) and returns `False`, whereas `Path.stat()` throws `ValueError: embedded null byte`. Thus, `load_seqcls_adapter` logs `adapter_missing_FALLBACK` instead of raising an exception.
  - Invalid types (e.g. integer path passed to `load_seqcls_adapter`) throw `TypeError` on `Path(path)` which is caught and correctly logs `model_failed_FALLBACK`.

## 2. Logic Chain
- The helper functions must handle edge cases gracefully:
  - `to_float` successfully strips spaces and replaces commas with dots, raising `ValueError` on completely invalid strings and `TypeError` on non-string inputs.
  - `MULT` contains the correct lowercased multiplier keys (`тыс`, `млн`, `млрд`, `трлн`).
  - `is_full_model` checks for existence of `config.json` while ensuring `adapter_config.json` does not exist. It correctly returns `False` when the directory does not exist, if both configs exist, or if the path is a file. It raises `TypeError` if input type is not `str` or `Path`.
  - `load_seqcls_adapter` successfully captures corrupt JSON, missing `labels` key, schema mismatches, type errors, and OS level/permission errors. It logs the respective fallback messages and returns `None` instead of crashing.
- Executing the custom test suite (`test_nlp_adversarial.py`) confirms that all assumptions and edge cases hold, and the application degrades gracefully rather than crashing.

## 3. Caveats
- Transformers loading (both full model and PEFT) is mocked in the unit tests to avoid network calls and CUDA memory allocation in unit testing.
- No direct evaluation of real-world large model inference speed or CPU/GPU out-of-memory errors was executed in this unit test batch.

## 4. Conclusion
- The refactored NLP features are highly robust. Ingestion pipelines and helpers degrade gracefully by catching format errors, missing files, permission blocks, and type issues without unhandled crashes.
- The overall risk assessment is **LOW**.

## 5. Verification Method
- **Test Command**: `.venv/bin/pytest tests/test_nlp_uncovered.py tests/test_nlp_adversarial.py`
- **Files to Inspect**:
  - `tests/test_nlp_adversarial.py` (contains the detailed adversarial scenarios)
  - `src/geoanalytics/nlp/_seqcls.py`
  - `src/geoanalytics/nlp/numeric.py`
- **Invalidation Conditions**: Any failure in the test command indicates a regression in model format checking, numeric parsing, or exception handling.

---

## Challenge Summary

**Overall risk assessment**: LOW

## Challenges

### [Low] Challenge 1: Null Byte path behavior
- **Assumption challenged**: A path with a null byte will raise an exception during `exists()` check, falling into the `model_failed` fallback.
- **Attack scenario**: Passing a path with a null byte to `load_seqcls_adapter`.
- **Blast radius**: The code logs `adapter_missing` instead of `model_failed`. There is no crash, but the categorization of the failure is slightly different (treated as missing rather than a loader exception).
- **Mitigation**: Standardized python behavior on 3.12 is accepted.

### [Low] Challenge 2: Invalid JSON format or missing schema keys in labels.json
- **Assumption challenged**: `labels.json` will contain a valid JSON list of labels under the key `"labels"`.
- **Attack scenario**: Malformed JSON or JSON without `"labels"` key.
- **Blast radius**: `load_seqcls_adapter` successfully wraps the execution and fails gracefully with `model_failed_FALLBACK`, returning `None`.
- **Mitigation**: The code handles this correctly.

## Stress Test Results
- Scientific notation string (`"1.2e3"`) → successfully parsed → `1200.0` → PASS
- Empty string input to `to_float` → throws `ValueError` → PASS
- Path is a file check in `is_full_model` → returns `False` → PASS
- Permission denied on directory read → caught and logged as fallback → PASS
- Integer path to `load_seqcls_adapter` → caught and logged as fallback → PASS
