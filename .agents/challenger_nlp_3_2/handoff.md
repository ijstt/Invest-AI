# Handoff Report

## 1. Observation

- **Project Tests Output**: Running the full test suite via `pytest tests/` ran successfully.
  ```
  1215 passed, 2 warnings in 19.10s
  ```
- **NLP Tests Output**: Specifically running the NLP test suite via `.venv/bin/pytest tests/test_nlp*` passed cleanly:
  ```
  74 passed in 4.71s
  ```
  And other NLP-related tests like `test_entity_linking.py`, `test_forecast*.py`, etc. passed too:
  ```
  110 passed, 1 warning in 5.49s
  ```
- **File Checked**: `src/geoanalytics/nlp/numeric.py` line 110-111 defines:
  ```python
  def to_float(raw: str) -> float:
      return float(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))
  ```
  And `_NUM` regex is defined on line 28:
  ```python
  _NUM = r"(\d{1,3}(?:\s\d{3})*(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
  ```
- **Verification Failure**: Testing thin space parsing in python command:
  ```
  .venv/bin/python -c 'from geoanalytics.nlp.numeric import extract_numbers; print(extract_numbers("дивиденды в размере 1\u2009200,5 руб. на акцию"))'
  ```
  Resulted in the following traceback:
  ```
  Traceback (most recent call last):
    File "<string>", line 1, in <module>
    File "/home/ijstt/News/src/geoanalytics/nlp/numeric.py", line 152, in extract_numbers
      add(NumericFact(DIVIDEND, to_float(m.group(2)), "RUB", _snippet(text, m)))
                                ^^^^^^^^^^^^^^^^^^^^
    File "/home/ijstt/News/src/geoanalytics/nlp/numeric.py", line 111, in to_float
      return float(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  ValueError: could not convert string to float: '1\u2009200.5'
  ```

- **File Checked**: `src/geoanalytics/nlp/_seqcls.py` line 132-145:
  ```python
      def get_model(self, path: str | None, config: ModelConfig, logger: Any) -> SeqClsAdapter | None:
          if config.name not in self._cache:
              with self._lock:
                  if config.name not in self._cache:
                      self._cache[config.name] = load_seqcls_adapter(
                          path,
                          ...
  ```

## 2. Logic Chain

1. In `src/geoanalytics/nlp/numeric.py`, the `_NUM` regular expression matches numbers formatted with a space separator using `\s`.
2. In Python 3, `\s` in a regular expression matches all Unicode space characters, including thin spaces (`\u2009`) and narrow non-breaking spaces (`\u202f`).
3. However, `to_float` only replaces standard spaces (`" "`) and non-breaking spaces (`"\xa0"`).
4. If a thin space or narrow non-breaking space is present in the input number, `to_float` will forward it to `float()`, causing a `ValueError` because those characters are not valid numerical characters in Python's float parser.
5. In `extract_numbers()`, this exception is unhandled and bubbles up, crashing the entire parsing pipeline for that text.
6. In `src/geoanalytics/nlp/_seqcls.py`, the `SeqClsRegistry` cache is keyed only on `config.name` (e.g., `"sentiment"`, `"event"`), not on the combination of model name and adapter path.
7. Consequently, if the path to the model adapter changes dynamically during execution (e.g., after training a new LoRA model), `get_model` will return the stale/previously cached model.

## 3. Caveats

- We did not benchmark the actual inference latency of LoRA models vs fully fine-tuned models under GPU environments since only CPU emulation was available.
- We assumed that settings reload is expected to be immediate; if settings changes are only intended to take effect after app restart, then Challenge 2 is a design limitation rather than a bug.

## 4. Conclusion

- The refactored NLP modules are functionally correct as verified by passing all 1215 tests in the suite.
- However, two robustness and performance bottlenecks were found:
  1. A crash vulnerability in `numeric.py` when processing numeric values formatted with Unicode thin spaces or narrow non-breaking spaces.
  2. A stale cache issue in `_seqcls.py` where updating the adapter path configuration does not reload the model.

## 5. Verification Method

To verify the findings:
1. Run the Python one-liner to confirm the `ValueError` crash:
   ```bash
   .venv/bin/python -c 'from geoanalytics.nlp.numeric import extract_numbers; print(extract_numbers("дивиденды в размере 1\u2009200,5 руб. на акцию"))'
   ```
2. Verify that all test suites pass using:
   ```bash
   .venv/bin/pytest tests/test_nlp*
   ```
