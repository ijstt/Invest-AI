# NLP Codebase Refactoring Analysis & Plan

This report presents a read-only investigation of the NLP codebase under `src/geoanalytics/nlp/` and outlines a structured design to eliminate duplication, fix namespace/import issues, and improve unit testing.

---

## 1. Observation

### A. Duplicate Model Loading and Status Logic
Four separate modules load pre-trained or LoRA-adapted sequence classifiers and report their loading status using duplicated or highly similar code:

1. **`classify.py` (lines 63-73, 76-83):**
   ```python
   @lru_cache
   def _get_classifier() -> SeqClsAdapter | None:
       return load_seqcls_adapter(
           get_settings().event_adapter_path,
           log,
           name="event",
           err_level="warning",
           missing_key="event_adapter_missing",
           ready_key="event_classifier_ready",
           failed_key="event_classifier_failed_rules",
       )
   
   def model_status() -> tuple[str, str]:
       configured = bool(get_settings().event_adapter_path)
       if _get_classifier() is not None:
           return "ok", "дообученная модель"
       if configured:
           return "degraded", "адаптер настроен, но не загрузился — активны ПРАВИЛА"
       return "ok", "правила (адаптер не настроен)"
   ```

2. **`significance.py` (lines 149-166, 169-180):**
   ```python
   @lru_cache
   def _get_model():
       return load_seqcls_adapter(
           get_settings().significance_adapter_path,
           log,
           name="significance",
           err_level="error",
           missing_key="significance_adapter_missing_FORMULA_FALLBACK",
           ready_key="significance_model_ready",
           failed_key="significance_model_failed_FORMULA_FALLBACK",
       )
   
   def model_status() -> tuple[str, str]:
       configured = bool(get_settings().significance_adapter_path)
       if _get_model() is not None:
           return "ok", "модель (дискретные бакеты)"
       if configured:
           return "degraded", "адаптер настроен, но не загрузился — активна ФОРМУЛА (Б1)"
       return "ok", "формула (адаптер не настроен)"
   ```

3. **`temporal.py` (lines 114-125, 137-144):**
   ```python
   @lru_cache(maxsize=1)
   def _model():
       return load_seqcls_adapter(
           get_settings().temporal_adapter_path,
           log,
           name="temporal",
           err_level="error",
           missing_key="temporal_adapter_missing_FALLBACK",
           ready_key="temporal_model_ready",
           failed_key="temporal_model_load_failed_FALLBACK",
       )
   
   def model_status() -> tuple[str, str]:
       path = get_settings().temporal_adapter_path
       if not path:
           return "ok", "temporal: не настроен (статус/дата события NULL)"
       if _model() is None:
           return "degraded", "temporal: НЕ ЗАГРУЗИЛСЯ (статус/дата события NULL)"
       return "ok", "temporal: модель"
   ```

4. **`aspect.py` (lines 39-56, 88-104):**
   This module handles two separate adapters (`aspect_sentiment` and `saliency`):
   ```python
   def _load(path: str | None, name: str):
       return load_seqcls_adapter(
           path,
           log,
           name=name,
           err_level="error",
       )
   
   @lru_cache
   def _get_sentiment_model():
       return _load(get_settings().aspect_sentiment_adapter_path, "aspect_sentiment")
   
   @lru_cache
   def _get_saliency_model():
       return _load(get_settings().saliency_adapter_path, "saliency")
   ```
   Its `model_status` function iterates over the two paths and status values to format a combined status string:
   ```python
   def model_status() -> tuple[str, str]:
       s = get_settings()
       parts: list[str] = []
       degraded = False
       for path, model, label in (
           (s.aspect_sentiment_adapter_path, _get_sentiment_model(), "aspect-sentiment"),
           (s.saliency_adapter_path, _get_saliency_model(), "saliency"),
       ):
           if not path:
               parts.append(f"{label}: не настроен")
           elif model is None:
               parts.append(f"{label}: НЕ ЗАГРУЗИЛСЯ (фолбэк на тональность статьи)")
               degraded = True
           else:
               parts.append(f"{label}: модель")
       return ("degraded" if degraded else "ok"), "; ".join(parts)
   ```

### B. Duplicate `_is_full_model()` Logic
The detection of whether a model is loaded directly (full-FT) or via a PEFT adapter uses the helper function `is_full_model()` in `_seqcls.py`. However, both classes wrap this function in identical static methods:
- **`_seqcls.py` (lines 33-35) inside `SeqClsAdapter`:**
  ```python
  @staticmethod
  def _is_full_model(path: str) -> bool:
      return is_full_model(path)
  ```
- **`sentiment.py` (lines 116-118) inside `_RubertSentiment`:**
  ```python
  @staticmethod
  def _is_full_model(path: str) -> bool:
      return is_full_model(path)
  ```
They call `self._is_full_model(path)` internally inside their constructors.

### C. Redundant Private Definitions/Aliases
In `nlp/numeric.py`, we observe the definition of both public and private names that refer to the same variables:
- **`numeric.py` (lines 30-31):**
  ```python
  MULT = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}
  _MULT = MULT
  ```
- **`numeric.py` (lines 97-101):**
  ```python
  def to_float(raw: str) -> float:
      return float(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))
  
  _to_float = to_float
  ```
Internally, `numeric.py` uses `_to_float` and `_MULT`, whereas `nlp/fundamentals.py` (line 17) imports and uses the public equivalents:
```python
from geoanalytics.nlp.numeric import MULT, to_float
```

### D. Current Unit Test Status and Issues in `tests/test_nlp_uncovered.py`
Running `PYTHONPATH=src .venv/bin/pytest tests/test_nlp_uncovered.py` shows that 8 out of 1167 tests fail due to test code bugs and environment quirks:
1. **Import Shadowing:** Both `ner` and `embeddings` tests import `model_status` directly:
   - Line 128: `from geoanalytics.nlp.ner import Mention, extract_entities, lemmas, model_status`
   - Line 167: `from geoanalytics.nlp.embeddings import Embedder, get_embedder, model_status`
   This shadows `ner.model_status`, causing NER tests to call `embeddings.model_status()` and fail assertions (e.g., expecting "natasha" and getting E5).
2. **ValueError in `sys.modules["torch"]` Mocking:**
   ```
   ValueError: torch.__spec__ is not set
   ```
   In Python 3.12, `importlib.util.find_spec("torch")` checks the module `__spec__` if the module exists in `sys.modules`. Since the test uses `monkeypatch.setitem(sys.modules, "torch", mock_torch)` on a plain `MagicMock`, it raises a `ValueError`.
3. **httpx Mock missing request object:**
   ```
   RuntimeError: Cannot call `raise_for_status` as the request instance has not been set on this response.
   ```
   Mocking `httpx.post` and `httpx.get` using `httpx.Response(200, json=...)` without a `request` instance fails when the application calls `.raise_for_status()`.

---

## 2. Logic Chain

1. **Eliminating Duplicate Model Loader and Cache Logic:** Since the four files import `load_seqcls_adapter` and wrap it using `@lru_cache`, we can extract this configuration and caching pattern into a centralized `SeqClsRegistry` class in `_seqcls.py`. This class can store cached instances of `SeqClsAdapter` and dynamically query the settings path and format the status strings based on standard configuration objects (`ModelConfig`).
2. **Removing Redundant `_is_full_model()` Wrappers:** The class-level static methods `_is_full_model` simply call the module-level function `is_full_model(path)`. Therefore, we can delete the static methods and replace internal class references `self._is_full_model(...)` with direct calls to `is_full_model(...)`.
3. **Cleaning Private/Public Duplication:** Since `fundamentals.py` and other modules import `MULT` and `to_float`, the private aliases `_MULT` and `_to_float` in `numeric.py` are redundant. Standardizing on `MULT` and `to_float` within `numeric.py` makes the code cleaner, PEP 8 compliant, and less error-prone.
4. **Fixing the Uncovered Tests:** Fixing import shadowing (by importing modules instead of shadowing function names), attaching a dummy `__spec__` to the mock `torch` object, and specifying a mock `httpx.Request` on `httpx.Response` objects will make all tests pass cleanly.

---

## 3. Caveats

- We assume settings from `get_settings()` are read-only and static for the duration of the execution process. If settings can be modified dynamically at runtime, the registry must allow cache invalidation.
- Unit testing of NLP modules (ruBERT, Natasha, FastEmbed) must rely heavily on mocks in CI/CD pipelines to avoid downloading gigabytes of model weights and exceeding RAM limits.

---

## 4. Conclusion & Proposed Designs

### A. Shared Model Adapter Loader in `_seqcls.py`
Define a configuration dataclass and registry class inside `_seqcls.py`:

```python
# In src/geoanalytics/nlp/_seqcls.py

from dataclasses import dataclass
from typing import ClassVar

@dataclass(frozen=True)
class ModelConfig:
    name: str
    err_level: str = "error"
    missing_key: str | None = None
    ready_key: str | None = None
    failed_key: str | None = None
    loaded_desc: str = "модель загружена"
    fallback_desc: str = "фолбэк активен"
    unconfigured_desc: str = "не настроен"

class SeqClsRegistry:
    def __init__(self) -> None:
        self._cache: dict[str, SeqClsAdapter | None] = {}

    def get_model(self, path: str | None, config: ModelConfig, logger: Any) -> SeqClsAdapter | None:
        if config.name not in self._cache:
            self._cache[config.name] = load_seqcls_adapter(
                path,
                logger,
                name=config.name,
                err_level=config.err_level,
                missing_key=config.missing_key,
                ready_key=config.ready_key,
                failed_key=config.failed_key
            )
        return self._cache[config.name]

    def get_status(self, path: str | None, config: ModelConfig, logger: Any) -> tuple[str, str]:
        configured = bool(path)
        model = self.get_model(path, config, logger)
        if model is not None:
            return "ok", config.loaded_desc
        if configured:
            return "degraded", config.fallback_desc
        return "ok", config.unconfigured_desc

# Central singleton instance
registry = SeqClsRegistry()
```

#### Application in target files:

1. **`classify.py`**:
   ```python
   from geoanalytics.nlp._seqcls import registry, ModelConfig
   
   _CFG = ModelConfig(
       name="event",
       err_level="warning",
       missing_key="event_adapter_missing",
       ready_key="event_classifier_ready",
       failed_key="event_classifier_failed_rules",
       loaded_desc="дообученная модель",
       fallback_desc="адаптер настроен, но не загрузился — активны ПРАВИЛА",
       unconfigured_desc="правила (адаптер не настроен)"
   )
   
   def _get_classifier() -> SeqClsAdapter | None:
       return registry.get_model(get_settings().event_adapter_path, _CFG, log)
   
   def model_status() -> tuple[str, str]:
       return registry.get_status(get_settings().event_adapter_path, _CFG, log)
   ```

2. **`significance.py`**:
   ```python
   from geoanalytics.nlp._seqcls import registry, ModelConfig
   
   _CFG = ModelConfig(
       name="significance",
       err_level="error",
       missing_key="significance_adapter_missing_FORMULA_FALLBACK",
       ready_key="significance_model_ready",
       failed_key="significance_model_failed_FORMULA_FALLBACK",
       loaded_desc="модель (дискретные бакеты)",
       fallback_desc="адаптер настроен, но не загрузился — активна ФОРМУЛА (Б1)",
       unconfigured_desc="формула (адаптер не настроен)"
   )
   
   def _get_model():
       return registry.get_model(get_settings().significance_adapter_path, _CFG, log)
   
   def model_status() -> tuple[str, str]:
       return registry.get_status(get_settings().significance_adapter_path, _CFG, log)
   ```

3. **`temporal.py`**:
   ```python
   from geoanalytics.nlp._seqcls import registry, ModelConfig
   
   _CFG = ModelConfig(
       name="temporal",
       err_level="error",
       missing_key="temporal_adapter_missing_FALLBACK",
       ready_key="temporal_model_ready",
       failed_key="temporal_model_load_failed_FALLBACK",
       loaded_desc="temporal: модель",
       fallback_desc="temporal: НЕ ЗАГРУЗИЛСЯ (статус/дата события NULL)",
       unconfigured_desc="temporal: не настроен (статус/дата события NULL)"
   )
   
   def _model():
       return registry.get_model(get_settings().temporal_adapter_path, _CFG, log)
   
   def model_status() -> tuple[str, str]:
       return registry.get_status(get_settings().temporal_adapter_path, _CFG, log)
   ```

4. **`aspect.py`**:
   ```python
   from geoanalytics.nlp._seqcls import registry, ModelConfig
   
   _SENT_CFG = ModelConfig(
       name="aspect_sentiment",
       err_level="error",
       loaded_desc="aspect-sentiment: модель",
       fallback_desc="aspect-sentiment: НЕ ЗАГРУЗИЛСЯ (фолбэк на тональность статьи)",
       unconfigured_desc="aspect-sentiment: не настроен"
   )
   
   _SAL_CFG = ModelConfig(
       name="saliency",
       err_level="error",
       loaded_desc="saliency: модель",
       fallback_desc="saliency: НЕ ЗАГРУЗИЛСЯ (фолбэк на тональность статьи)",
       unconfigured_desc="saliency: не настроен"
   )
   
   def _get_sentiment_model():
       return registry.get_model(get_settings().aspect_sentiment_adapter_path, _SENT_CFG, log)
   
   def _get_saliency_model():
       return registry.get_model(get_settings().saliency_adapter_path, _SAL_CFG, log)
   
   def model_status() -> tuple[str, str]:
       s = get_settings()
       stat_sent, desc_sent = registry.get_status(s.aspect_sentiment_adapter_path, _SENT_CFG, log)
       stat_sal, desc_sal = registry.get_status(s.saliency_adapter_path, _SAL_CFG, log)
       degraded = (stat_sent == "degraded" or stat_sal == "degraded")
       return ("degraded" if degraded else "ok"), f"{desc_sent}; {desc_sal}"
   ```

### B. Shared `_is_full_model()` Logic
- In `_seqcls.py`, remove the `@staticmethod _is_full_model` from class `SeqClsAdapter`. Replace calls to `self._is_full_model(path)` with `is_full_model(path)` inside the `SeqClsAdapter` class.
- In `sentiment.py`, remove the `@staticmethod _is_full_model` from class `_RubertSentiment`. Replace calls to `self._is_full_model(path)` with the imported `is_full_model(path)` function.

### C. Private Imports and Aliases Fix
- In `numeric.py`, delete the aliases:
  ```python
  _MULT = MULT
  _to_float = to_float
  ```
- Replace all occurrences of `_MULT` and `_to_float` within `numeric.py` with `MULT` and `to_float` respectively.
- Keep the imports in `fundamentals.py` as:
  ```python
  from geoanalytics.nlp.numeric import MULT, to_float
  ```
  This is clean and ensures public-only imports are used.

### D. New Unit Test Plan & Fixing Existing Failures
Modify `tests/test_nlp_uncovered.py` as follows to resolve existing test failures and expand coverage:

1. **Fix `model_status` import shadowing:**
   Change lines 128 and 167 to:
   ```python
   # Instead of: from geoanalytics.nlp.ner import model_status
   from geoanalytics.nlp import ner
   # In test code:
   status, detail = ner.model_status()
   ```
   Do the same for `embeddings` and `llm`.
2. **Fix `torch.__spec__` Mocking:**
   Ensure that when `sys.modules["torch"]` is set to a mock, the mock has `__spec__`:
   ```python
   mock_torch = MagicMock()
   mock_torch.__spec__ = MagicMock()  # Prevents python 3.12 importlib error
   monkeypatch.setitem(sys.modules, "torch", mock_torch)
   ```
3. **Fix `httpx.Response` instantiation in mocks:**
   Add `request=httpx.Request("METHOD", url)` to every mocked response:
   ```python
   httpx.Response(200, json={"models": []}, request=httpx.Request("GET", "http://localhost:11434/api/tags"))
   ```
4. **Plan New Unit Tests:**
   - **`ner.py`**: Add testing of `lemmas()` under error scenarios (mocking Natasha to raise an unexpected runtime error to verify graceful degradation to `None`).
   - **`embeddings.py`**: Add tests for batch embedding inputs containing empty string elements, verifying that lists of empty lists are handled correctly.
   - **`llm.py`**: Add tests verifying option parameter mapping for `temperature` overrides, and verification that Cloud provider errors are correctly logged.
   - **`_seqcls.py`**: Add tests verifying that `load_seqcls_adapter` logs correctly on different `err_level` settings ("error" vs "warning") when the file path doesn't exist.

---

## 5. Verification Method

To verify the proposed designs:

1. Run the test suite:
   ```bash
   PYTHONPATH=src .venv/bin/pytest tests/test_nlp_uncovered.py
   ```
2. Verify ruff compliance:
   ```bash
   .venv/bin/ruff check src/geoanalytics/nlp/
   ```
3. Validate cascade consistency across NLP and health-checks using:
   ```bash
   PYTHONPATH=src .venv/bin/pytest tests/test_fundamentals.py
   ```
