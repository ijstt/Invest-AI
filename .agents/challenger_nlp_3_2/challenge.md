## Challenge Summary

**Overall risk assessment**: MEDIUM

## Challenges

### [High] Challenge 1: Uncaught ValueError in numeric extraction due to Unicode space mismatches

- **Assumption challenged**: The numeric extraction utility `to_float` assumes that thousand separators in matched numbers are exclusively standard spaces (`" "`) or non-breaking spaces (`"\xa0"`).
- **Attack scenario**: Real-world news feeds often format numbers using thin spaces (`\u2009`) or narrow non-breaking spaces (`\u202f`) as thousand separators. The regular expression patterns in `numeric.py` (e.g., `_DIV_RE`, `_RATE_RE`, `_AMOUNT_RE`, `_TARGET_RE`) use the `\s` shorthand, which matches all Unicode whitespace characters by default in Python 3. Consequently, strings like `"1\u2009200,5"` match the patterns, but when passed to `to_float`, the thin space is not stripped. This causes `float()` to throw a `ValueError`, crashing the entire `extract_numbers()` execution.
- **Blast radius**: A single occurrence of a non-standard Unicode space in a matched number will crash the entire `extract_numbers()` function, failing to process the rest of the text and potentially crashing the caller pipeline.
- **Mitigation**: Update `to_float` to replace all whitespace characters using regular expressions, e.g., `re.sub(r"\s+", "", raw)` instead of chained `.replace()` calls, or wrap the conversion in `extract_numbers()` with a try-except block to gracefully skip malformed numbers.

### [Medium] Challenge 2: Stale Cache in SeqClsRegistry on Dynamic Config Reloads

- **Assumption challenged**: The model registry assumes that model adapter paths in configuration settings are static and never change during the runtime lifetime of the application.
- **Attack scenario**: If the configuration path (e.g. `settings.sentiment_adapter_path` or `settings.event_adapter_path`) is updated dynamically during the runtime of the application (e.g. after training a new model, or during a hot-reload of settings), `SeqClsRegistry.get_model` will still return the previously loaded model. This is because the registry caches the loaded models purely by the task name (e.g., `"sentiment"`, `"event"`).
- **Blast radius**: The application will silently continue using the old model or fallback rules, ignoring configuration changes until a hard restart of the process is performed.
- **Mitigation**: Change the cache key in `SeqClsRegistry` to include both the model name and the adapter path, or expose a cache invalidation mechanism (e.g. `registry.clear()`) to be called when configuration reloads.

## Stress Test Results

- **Scenario 1 (Unicode Thin Space)**:
  - Input: `"дивиденды в размере 1\u2009200,5 руб. на акцию"`
  - Expected behavior: Extracts a `dividend` fact with value `1200.5`.
  - Actual behavior: Throws `ValueError: could not convert string to float: '1\u2009200.5'` and crashes.
  - Result: **FAIL**

- **Scenario 2 (Unicode Narrow No-Break Space)**:
  - Input: `"дивиденды в размере 1\u202f200,5 руб. на акцию"`
  - Expected behavior: Extracts a `dividend` fact with value `1200.5`.
  - Actual behavior: Throws `ValueError: could not convert string to float: '1\u202f200.5'` and crashes.
  - Result: **FAIL**

- **Scenario 3 (Standard spaces in key rate)**:
  - Input: `"ЦБ снизил ключевую ставку до 14%"`
  - Expected behavior: Extracts a `key_rate` fact with value `14.0`.
  - Actual behavior: Extracts `14.0` successfully.
  - Result: **PASS**

- **Scenario 4 (Model Reload)**:
  - Input: Change `settings.sentiment_adapter_path` after registry has already loaded a model.
  - Expected behavior: Registry loads the model from the new path.
  - Actual behavior: Registry returns the cached model from the old path.
  - Result: **FAIL**

## Unchallenged Areas

- **Natasha NER integration**: Not fully stress-tested with memory limits because Natasha is a standard lightweight dependency with no torch backend, and its extraction logic is handled correctly.
- **LLM APIs (Ollama/Cloud)**: Expected responses were mocked/tested in `test_nlp_adversarial.py`, and the integration catches any parse/network exceptions gracefully.
