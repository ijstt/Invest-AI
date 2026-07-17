# NLP Module Analysis Report

## Executive Summary
This report analyzes duplicate sequence classification adapter loading logic across multiple NLP submodules, investigates a delegation method in `sentiment.py`, identifies imported helper structures from `numeric.py` inside `fundamentals.py`, and reviews the unit testing architecture designed for untested/uncovered NLP components (`ner.py`, `embeddings.py`, `llm.py`, and `_seqcls.py`).

---

## 1. Duplicate SeqClsAdapter Loading Logic

We identified duplicated logic for setting up model configurations, model getters, and health checks across `classify.py`, `significance.py`, `temporal.py`, and `aspect.py`.

### Code Observations
In each of these files, the following boilerplate is repeated:
1. Import of `ModelConfig` and `registry` from `geoanalytics.nlp._seqcls`.
2. Initialization of a module-level `ModelConfig` instance containing metadata for logs and health checks.
3. A module-level helper function to retrieve the cached adapter from the registry via `registry.get_model()`.
4. A public `model_status()` function that returns `tuple[str, str]` indicating health state (`ok` or `degraded`) via `registry.get_status()`.

#### Comparison of Boilerplate Code:

| File | Config Object | Getter Function | Status Function |
|---|---|---|---|
| **`classify.py`** | `_CFG` | `_get_classifier()` | `model_status()` |
| **`significance.py`**| `_CFG` | `_get_model()` | `model_status()` |
| **`temporal.py`** | `_CFG` | `_model()` | `model_status()` |
| **`aspect.py`** | `_SENT_CFG`, `_SAL_CFG` | `_get_sentiment_model()`, `_get_saliency_model()` | `model_status()` |

#### Specific Code Snippets:

* **`classify.py` (lines 121–128)**:
```python
def _get_classifier() -> SeqClsAdapter | None:
    return registry.get_model(get_settings().event_adapter_path, _CFG, log)

def model_status() -> tuple[str, str]:
    """Статус классификатора событий для health-check (I4): ("ok"|"degraded", деталь)."""
    return registry.get_status(get_settings().event_adapter_path, _CFG, log)
```

* **`significance.py` (lines 157–174)**:
```python
def _get_model():
    return registry.get_model(get_settings().significance_adapter_path, _CFG, log)

def model_status() -> tuple[str, str]:
    """Статус значимости для health-check (I4): ("ok"|"degraded", деталь)."""
    return registry.get_status(get_settings().significance_adapter_path, _CFG, log)
```

* **`temporal.py` (lines 124–140)**:
```python
def _model():
    return registry.get_model(get_settings().temporal_adapter_path, _CFG, log)

def model_status() -> tuple[str, str]:
    """Статус F3 для health-check: degraded, если настроено, но не загрузилось."""
    return registry.get_status(get_settings().temporal_adapter_path, _CFG, log)
```

* **`aspect.py` (lines 53–60, 90–96)**:
```python
def _get_sentiment_model():
    return registry.get_model(get_settings().aspect_sentiment_adapter_path, _SENT_CFG, log)

def _get_saliency_model():
    return registry.get_model(get_settings().saliency_adapter_path, _SAL_CFG, log)

def model_status() -> tuple[str, str]:
    """Статус F1/F2 для health-check: degraded, если настроено, но не загрузилось."""
    s = get_settings()
    stat_sent, desc_sent = registry.get_status(s.aspect_sentiment_adapter_path, _SENT_CFG, log)
    stat_sal, desc_sal = registry.get_status(s.saliency_adapter_path, _SAL_CFG, log)
    degraded = (stat_sent == "degraded" or stat_sal == "degraded")
    return ("degraded" if degraded else "ok"), f"{desc_sent}; {desc_sal}"
```

### Synthesis & Refactoring Recommendation
While the main loading implementation `load_seqcls_adapter` and caching logic `SeqClsRegistry` are unified in `_seqcls.py`, the dynamic boilerplate in each calling module remains highly copy-pasted.

**Proposed Refactoring**:
Introduce a helper function in `_seqcls.py` to create adapter properties dynamically, or modify `SeqClsRegistry` to accept settings attribute names (e.g. string names like `"event_adapter_path"`) and automatically resolve them. For example:
```python
# In geoanalytics/nlp/_seqcls.py
def build_status_checker(setting_attr: str, config: ModelConfig, logger: Any):
    def check_status() -> tuple[str, str]:
        path = getattr(get_settings(), setting_attr)
        return registry.get_status(path, config, logger)
    return check_status
```
This would reduce the boilerplate in individual modules to a single-line assignment.

---

## 2. Inspection of `sentiment.py` for `_is_full_model()`

### Code Observations
In `geoanalytics/nlp/sentiment.py` (lines 66–70):
```python
class _RubertSentiment:
    ...
    @staticmethod
    def _is_full_model(path: str) -> bool:
        """Каталог — полностью дообученная модель (config.json без adapter_config.json),
        а не PEFT-адаптер (adapter_config.json)."""
        return is_full_model(path)
```
Where `is_full_model` is imported at line 20:
```python
from geoanalytics.nlp._seqcls import is_full_model
```

### Analysis
* The `_RubertSentiment` class defines `_is_full_model` as a `@staticmethod` helper.
* Rather than implementing its own detection logic, it simply wraps and delegates to the module-level helper `is_full_model` imported from `geoanalytics.nlp._seqcls`.
* This delegation prevents code duplication of the format detection logic. In `_seqcls.py`, the exact same function is also wrapped inside `SeqClsAdapter` as a static method:
```python
class SeqClsAdapter:
    @staticmethod
    def _is_full_model(path: str) -> bool:
        return is_full_model(path)
```

---

## 3. Private Imports from `numeric.py` in `fundamentals.py`

### Code Observations
In `geoanalytics/nlp/fundamentals.py` (line 17):
```python
from geoanalytics.nlp.numeric import MULT, to_float
```

Inside `geoanalytics/nlp/numeric.py` (lines 30–31, 98–101):
```python
MULT = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}
_MULT = MULT  # Alias for backward compatibility
...
def to_float(raw: str) -> float:
    return float(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))

_to_float = to_float  # Alias for backward compatibility
```

### Analysis
* The imports `MULT` (dictionary mapping units to float scale values) and `to_float` (utility function to parse Russian number formats into Python floats) are technically public names (as they do not start with a leading underscore).
* However, they are internal implementation details of the number parser inside `numeric.py` rather than its intended external API (which is `extract_numbers`).
* `numeric.py` defines aliases with leading underscores (`_MULT` and `_to_float`) for backward compatibility, suggesting that the primary names `MULT` and `to_float` are internal details and their consumption in `fundamentals.py` counts as importing private/internal implementation details.

---

## 4. Test Structure under `tests/` for NLP Modules

We inspected the test structure under the `tests/` directory to evaluate how unit tests for `ner.py`, `embeddings.py`, `llm.py`, and `_seqcls.py` are implemented.

### Current Test suite: `tests/test_nlp_uncovered.py`
A comprehensive test file `tests/test_nlp_uncovered.py` already exists, addressing all four of these modules.

#### Key Architectural Approaches:
1. **Mocking External and Heavy Modules**:
   To avoid loading heavy dependencies (such as `torch`, `transformers`, `peft`, `fastembed`) and to prevent external API calls (e.g. to local Ollama endpoints or cloud models), the test suite uses `monkeypatch` to substitute these modules in `sys.modules`.
   * **`mock_module` helper**:
     ```python
     def mock_module(monkeypatch, name):
         mock_mod = MagicMock()
         mock_mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
         monkeypatch.setitem(sys.modules, name, mock_mod)
         return mock_mod
     ```
     This injects a mock module into `sys.modules` at runtime, bypassing imports of heavy C++/binary modules.

2. **Test Setup for `nlp/_seqcls.py`**:
   * Uses `tmp_path` fixture to construct dummy model directories.
   * Creates files like `config.json`, `adapter_config.json`, and `labels.json` to simulate real LLM/adapter directory structures.
   * Asserts whether `is_full_model` detects PEFT/full fine-tuned formats correctly.
   * Verifies that `SeqClsAdapter` instantiates the correct classes (`AutoModelForSequenceClassification` or `PeftModel`).

3. **Test Setup for `nlp/ner.py`**:
   * Tests the fallback scenario when `Natasha` fails to load by mocking `_NatashaNer = None`.
   * Mocks a dummy Natasha tagger returning mock mentions and lemmas to verify standard behavior under success scenarios.

4. **Test Setup for `nlp/embeddings.py`**:
   * Simulates `fastembed` import failures to verify that the system gracefully degrades to `None` and health status turns to `degraded`.
   * Verifies behavior under model dimension mismatches (comparing against `EMBEDDING_DIM` in `geoanalytics.storage.models`).

5. **Test Setup for `nlp/llm.py`**:
   * Mocks `httpx.get` and `httpx.post` calls to Ollama and OpenAI-like Cloud endpoints.
   * Asserts correct query payloads (e.g., custom `temperature`, `system` prompts).

### Verification
We ran the NLP test suites directly within the project's virtual environment:
```bash
/home/ijstt/News/.venv/bin/pytest tests/test_nlp_uncovered.py tests/test_nlp.py
```
**Results**:
All **35 tests passed successfully** in 4.56 seconds:
* `tests/test_nlp_uncovered.py`: 22 passing tests
* `tests/test_nlp.py`: 13 passing tests
