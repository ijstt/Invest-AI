# Handoff Report — explorer_nlp_2

## 1. Observation

Direct observations made in the codebase:

### A. Duplicate SeqClsAdapter Loading Logic
* **`src/geoanalytics/nlp/classify.py` (lines 64-78)**:
  ```python
  @lru_cache
  def _get_classifier() -> SeqClsAdapter | None:
      path = get_settings().event_adapter_path
      if not path:
          return None
      if not Path(path).exists():
          log.warning("event_adapter_missing", path=path)
          return None
      try:
          clf = SeqClsAdapter(path)
          log.info("event_classifier_ready", path=path)
          return clf
      except Exception as exc:  # noqa: BLE001 — модель опциональна, есть правиловый фолбэк
          log.warning("event_classifier_failed_rules", error=str(exc))
          return None
  ```
* **`src/geoanalytics/nlp/significance.py` (lines 149-171)**:
  ```python
  @lru_cache
  def _get_model():
      path = get_settings().significance_adapter_path
      if not path:
          return None
      if not Path(path).exists():
          log.error("significance_adapter_missing_FORMULA_FALLBACK", path=path)
          return None
      try:
          from geoanalytics.nlp._seqcls import SeqClsAdapter

          model = SeqClsAdapter(path)
          log.info("significance_model_ready", path=path)
          return model
      except Exception as exc:  # noqa: BLE001 — конвейер выживает на формуле, но громко (Б1)
          log.error("significance_model_failed_FORMULA_FALLBACK", error=str(exc))
          return None
  ```
* **`src/geoanalytics/nlp/temporal.py` (lines 114-130)**:
  ```python
  @lru_cache(maxsize=1)
  def _model():
      path = get_settings().temporal_adapter_path
      if not path:
          return None
      if not Path(path).exists():
          log.error("temporal_adapter_missing_FALLBACK", path=path)
          return None
      try:
          from geoanalytics.nlp._seqcls import SeqClsAdapter

          model = SeqClsAdapter(path)
          log.info("temporal_model_ready", path=path)
          return model
      except Exception as exc:  # noqa: BLE001 — деградация громкая, но не фатальная
          log.error("temporal_model_load_failed_FALLBACK", error=str(exc))
          return None
  ```
* **`src/geoanalytics/nlp/aspect.py` (lines 38-53)**:
  ```python
  def _load(path: str | None, name: str):
      """Загрузка SeqClsAdapter с громкой деградацией (как у significance, Б1)."""
      if not path:
          return None
      if not Path(path).exists():
          log.error(f"{name}_adapter_missing_FALLBACK", path=path)
          return None
      try:
          from geoanalytics.nlp._seqcls import SeqClsAdapter

          model = SeqClsAdapter(path)
          log.info(f"{name}_model_ready", path=path)
          return model
      except Exception as exc:  # noqa: BLE001 — конвейер живёт на фолбэке, но громко
          log.error(f"{name}_model_failed_FALLBACK", error=str(exc))
          return None
  ```

### B. Duplicate `_is_full_model()` Logic
* **`src/geoanalytics/nlp/_seqcls.py` (lines 26-30)**:
  ```python
      @staticmethod
      def _is_full_model(path: str) -> bool:
          """Каталог — полностью дообученная модель (config.json без adapter_config.json),
          а не PEFT-адаптер (adapter_config.json)."""
          p = Path(path)
          return (p / "config.json").exists() and not (p / "adapter_config.json").exists()
  ```
* **`src/geoanalytics/nlp/sentiment.py` (lines 115-120)**:
  ```python
      @staticmethod
      def _is_full_model(path: str) -> bool:
          """Каталог — полностью дообученная модель (config.json без adapter_config.json),
          а не PEFT-адаптер (adapter_config.json)."""
          p = Path(path)
          return (p / "config.json").exists() and not (p / "adapter_config.json").exists()
  ```

### C. Private Imports
* **`src/geoanalytics/nlp/fundamentals.py` (line 17)**:
  ```python
  from geoanalytics.nlp.numeric import _MULT, _to_float
  ```
* **`src/geoanalytics/nlp/numeric.py` (line 30 and lines 96-97)**:
  ```python
  _MULT = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}
  ...
  def _to_float(raw: str) -> float:
      return float(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))
  ```
* **`src/geoanalytics/connectors/smartlab.py` (line 31)** also imports private symbols:
  ```python
  from geoanalytics.nlp.numeric import _MULT, _to_float
  ```

### D. File sizes (line counts) of files to modify
* `src/geoanalytics/nlp/_seqcls.py`: 61 lines
* `src/geoanalytics/nlp/classify.py`: 102 lines
* `src/geoanalytics/nlp/significance.py`: 201 lines
* `src/geoanalytics/nlp/temporal.py`: 159 lines
* `src/geoanalytics/nlp/aspect.py`: 112 lines
* `src/geoanalytics/nlp/sentiment.py`: 199 lines
* `src/geoanalytics/nlp/fundamentals.py`: 135 lines
* `src/geoanalytics/nlp/numeric.py`: 163 lines

All targeted files are significantly under the 600-line limit.

---

## 2. Logic Chain

1. **Deduplicating SeqClsAdapter Loading**:
   * The duplicate logic across `classify.py`, `significance.py`, `temporal.py`, and `aspect.py` performs the exact same steps (checking path, checking existence, loading `SeqClsAdapter`, catching exceptions, logging).
   * They differ only in: (a) target settings path, (b) logger object, (c) severity level (`warning` vs `error`), and (d) logging keys.
   * By designing a generalized function `load_adapter` in `_seqcls.py` that takes parameterizable logger, error logging level, and customized logging keys, we can replace all 4 code blocks with clean, one-liner calls.

2. **Deduplicating `_is_full_model()`**:
   * `_is_full_model()` behaves exactly identical. Moving it to the module-level of `_seqcls.py` as `is_full_model()` allows `_seqcls.py` and `sentiment.py` to reuse it directly.
   * `_seqcls.py` imports only native modules (`json`, `Path`, `annotations`) at module scope; PyTorch and transformers are imported inside `SeqClsAdapter.__init__`. This prevents circular/heavy imports when `sentiment.py` imports `is_full_model`.

3. **Exposing Private Imports (`_MULT`, `_to_float`)**:
   * To expose these symbols publicly as `MULT` and `to_float` without breaking legacy imports in `smartlab.py` or existing tests, we can define public symbols `MULT` and `to_float` in `numeric.py`, and bind `_MULT = MULT` and `_to_float = to_float` as backward-compatibility aliases.

4. **Designing Fast Unit Tests**:
   * The targeted modules (`ner.py`, `embeddings.py`, `llm.py`, `_seqcls.py`) use heavy or network-based dependencies (`natasha`, `fastembed`, `httpx`, `torch`, `transformers`).
   * Running tests with real model weights or active network calls causes slow execution, high memory usage, and runs risk of failure in the CODE_ONLY environment.
   * Therefore, mocking/patching these dependencies ensures tests execute in milliseconds while testing all functional paths and graceful degradation.

---

## 3. Caveats

* **Mocks**: Testing models via mocking is excellent for unit tests but does not verify actual PyTorch/ONNX inference behavior. However, this is standard for unit-test suites in air-gapped CI environments.
* **Backward compatibility**: Assumes that external callers did not import other private symbols. We confirmed that only `_MULT` and `_to_float` are imported across the src package.

---

## 4. Conclusion & Proposed Diff Patches

### Proposal 1: Refactor `_seqcls.py`
Add `is_full_model` and `load_adapter` at module scope:
```python
# In src/geoanalytics/nlp/_seqcls.py

def is_full_model(path: str | Path) -> bool:
    """Каталог — полностью дообученная модель (config.json без adapter_config.json),
    а не PEFT-адаптер (adapter_config.json)."""
    p = Path(path)
    return (p / "config.json").exists() and not (p / "adapter_config.json").exists()

def load_adapter(
    path: str | None,
    name: str,
    log,
    err_level: str = "error",
    missing_key: str | None = None,
    ready_key: str | None = None,
    failed_key: str | None = None,
) -> SeqClsAdapter | None:
    """Универсальный вспомогательный загрузчик SeqClsAdapter."""
    if not path:
        return None
    if not Path(path).exists():
        getattr(log, err_level)(missing_key or f"{name}_adapter_missing_FALLBACK", path=path)
        return None
    try:
        clf = SeqClsAdapter(path)
        log.info(ready_key or f"{name}_model_ready", path=path)
        return clf
    except Exception as exc:  # noqa: BLE001
        getattr(log, err_level)(failed_key or f"{name}_model_failed_FALLBACK", error=str(exc))
        return None
```
Modify `SeqClsAdapter._is_full_model` to point to module-level `is_full_model`.

### Proposal 2: Update `classify.py`, `significance.py`, `temporal.py`, and `aspect.py`
* **`classify.py`**:
  ```python
  @lru_cache
  def _get_classifier() -> SeqClsAdapter | None:
      from geoanalytics.nlp._seqcls import load_adapter
      return load_adapter(
          path=get_settings().event_adapter_path,
          name="event",
          log=log,
          err_level="warning",
          missing_key="event_adapter_missing",
          ready_key="event_classifier_ready",
          failed_key="event_classifier_failed_rules",
      )
  ```
* **`significance.py`**:
  ```python
  @lru_cache
  def _get_model():
      from geoanalytics.nlp._seqcls import load_adapter
      return load_adapter(
          path=get_settings().significance_adapter_path,
          name="significance",
          log=log,
          err_level="error",
          missing_key="significance_adapter_missing_FORMULA_FALLBACK",
          ready_key="significance_model_ready",
          failed_key="significance_model_failed_FORMULA_FALLBACK",
      )
  ```
* **`temporal.py`**:
  ```python
  @lru_cache(maxsize=1)
  def _model():
      from geoanalytics.nlp._seqcls import load_adapter
      return load_adapter(
          path=get_settings().temporal_adapter_path,
          name="temporal",
          log=log,
          err_level="error",
          missing_key="temporal_adapter_missing_FALLBACK",
          ready_key="temporal_model_ready",
          failed_key="temporal_model_load_failed_FALLBACK",
      )
  ```
* **`aspect.py`**:
  ```python
  def _load(path: str | None, name: str):
      """Загрузка SeqClsAdapter с громкой деградацией (как у significance, Б1)."""
      from geoanalytics.nlp._seqcls import load_adapter
      return load_adapter(
          path=path,
          name=name,
          log=log,
          err_level="error",
      )
  ```

### Proposal 3: Refactor `sentiment.py`
* In `sentiment.py`, replace `_RubertSentiment._is_full_model` static method call:
  ```python
  from geoanalytics.nlp._seqcls import is_full_model
  # ...
  # and in _RubertSentiment.__init__:
  if adapter_path and is_full_model(adapter_path):
  ```

### Proposal 4: Refactor `numeric.py` and `fundamentals.py`
* **`numeric.py`**:
  ```python
  MULT = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}
  _MULT = MULT

  def to_float(raw: str) -> float:
      return float(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))
  _to_float = to_float
  ```
* **`fundamentals.py`**:
  ```python
  from geoanalytics.nlp.numeric import MULT, to_float
  # Replace _MULT and _to_float with MULT and to_float.
  ```

---

## 5. Proposed Unit Tests Design

### A. `tests/test_ner.py`
```python
import sys
import pytest
from geoanalytics.nlp import ner

def test_ner_graceful_fallback(monkeypatch):
    monkeypatch.setattr(ner, "_NatashaNer", None)
    ner._get_ner.cache_clear()
    
    assert ner.extract_entities("любой текст") == []
    assert ner.lemmas("любой текст") is None
    status, detail = ner.model_status()
    assert status == "degraded"
    assert "не загрузилась" in detail

def test_ner_success():
    ner._get_ner.cache_clear()
    try:
        import natasha
    except ImportError:
        pytest.skip("Natasha not installed in this environment")

    mentions = ner.extract_entities("Газпром увеличил поставки в Китай через Сибирь.")
    assert isinstance(mentions, list)
    for m in mentions:
        assert m.text
        assert m.normal
        assert m.type in ("ORG", "LOC", "PER")

def test_lemmas():
    ner._get_ner.cache_clear()
    try:
        import natasha
    except ImportError:
        pytest.skip("Natasha not installed in this environment")

    words = ner.lemmas("Кони бегали по полям")
    assert words is not None
    assert "конь" in words
```

### B. `tests/test_embeddings.py`
```python
import pytest
import numpy as np
import sys
from types import ModuleType
from geoanalytics.nlp import embeddings

class MockTextEmbedding:
    def __init__(self, model_name, **kwargs):
        self.model_name = model_name

    def embed(self, texts):
        return [np.zeros(1024) for _ in texts]

def test_embeddings_success(monkeypatch):
    fastembed_mock = ModuleType("fastembed")
    fastembed_mock.TextEmbedding = MockTextEmbedding
    sys.modules["fastembed"] = fastembed_mock
    
    embeddings.get_embedder.cache_clear()
    
    class MockSettings:
        embedding_model = "BAAI/bge-small-en-v1.5"
        embedding_cache_dir = None
        
    monkeypatch.setattr(embeddings, "get_settings", lambda: MockSettings())
    
    embedder = embeddings.get_embedder()
    assert embedder is not None
    assert embedder.dim == 1024
    
    vec = embedder.embed_one("тест")
    assert len(vec) == 1024
    assert vec[0] == 0.0
    
    status, detail = embeddings.model_status()
    assert status == "ok"
    assert "BAAI/bge-small-en-v1.5" in detail

def test_embeddings_graceful_fallback(monkeypatch):
    if "fastembed" in sys.modules:
        monkeypatch.setitem(sys.modules, "fastembed", None)
        
    embeddings.get_embedder.cache_clear()
    
    embedder = embeddings.get_embedder()
    assert embedder is None
    
    status, detail = embeddings.model_status()
    assert status == "degraded"
    assert "эмбеддер не загрузился" in detail

def test_embeddings_dimension_mismatch(monkeypatch):
    class MockTextEmbedding512:
        def __init__(self, model_name, **kwargs):
            pass
        def embed(self, texts):
            return [np.zeros(512) for _ in texts]
            
    fastembed_mock = ModuleType("fastembed")
    fastembed_mock.TextEmbedding = MockTextEmbedding512
    sys.modules["fastembed"] = fastembed_mock
    
    embeddings.get_embedder.cache_clear()
    
    status, detail = embeddings.model_status()
    assert status == "degraded"
    assert "размерность модели 512" in detail
```

### C. `tests/test_llm.py`
```python
import pytest
import respx
import httpx
from geoanalytics.nlp import llm

@respx.mock
def test_llm_generate_ollama(monkeypatch):
    class MockSettings:
        llm_provider = "local"
        ollama_host = "http://localhost:11434"
        llm_model = "qwen2"
        llm_num_ctx = 4096
        llm_num_predict = 512
        llm_temperature = 0.1
        llm_keep_alive = "5m"
        llm_timeout = 30.0

    monkeypatch.setattr(llm, "get_settings", lambda: MockSettings())
    
    route = respx.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(
            200, 
            json={"message": {"content": "Привет, это ответ."}}
        )
    )
    
    res = llm.generate("Тестовый запрос")
    assert res == "Привет, это ответ."
    assert route.called

@respx.mock
def test_llm_generate_cloud(monkeypatch):
    class MockSettings:
        llm_provider = "cloud"
        cloud_base_url = "https://api.deepseek.com"
        cloud_api_key = "test_key"
        llm_model = "deepseek-chat"
        llm_temperature = 0.1
        llm_timeout = 30.0

    monkeypatch.setattr(llm, "get_settings", lambda: MockSettings())
    
    route = respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Облачный ответ."}}]}
        )
    )
    
    res = llm.generate("Тестовый запрос")
    assert res == "Облачный ответ."
    assert route.called

@respx.mock
def test_llm_is_available(monkeypatch):
    class MockSettings:
        llm_provider = "local"
        ollama_host = "http://localhost:11434"
        
    monkeypatch.setattr(llm, "get_settings", lambda: MockSettings())
    
    route_ok = respx.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200)
    )
    assert llm.is_available() is True
    
    route_fail = respx.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(500)
    )
    assert llm.is_available() is False
```

### D. `tests/test_seqcls.py`
```python
import json
import pytest
from pathlib import Path
import sys
from types import ModuleType
from geoanalytics.nlp import _seqcls

def test_is_full_model(tmp_path):
    dir_peft = tmp_path / "peft"
    dir_peft.mkdir()
    (dir_peft / "config.json").write_text("{}", encoding="utf-8")
    (dir_peft / "adapter_config.json").write_text("{}", encoding="utf-8")
    assert _seqcls.is_full_model(str(dir_peft)) is False

    dir_full = tmp_path / "full"
    dir_full.mkdir()
    (dir_full / "config.json").write_text("{}", encoding="utf-8")
    assert _seqcls.is_full_model(str(dir_full)) is True

class MockModelOutput:
    def __init__(self, logits):
        self.logits = logits

class MockModel:
    def __init__(self, logits_val):
        self.logits_val = logits_val
    def eval(self):
        pass
    def __call__(self, *args, **kwargs):
        class MockLogits:
            def __getitem__(self, idx):
                class MockArgMax:
                    def argmax(self):
                        return 1
                return MockArgMax()
        return MockModelOutput(MockLogits())

def test_seqcls_adapter_full_model(tmp_path, monkeypatch):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "labels.json").write_text(json.dumps({"labels": ["A", "B", "C"], "base": "base_model"}), encoding="utf-8")
    (model_dir / "config.json").write_text("{}", encoding="utf-8")

    class MockTorch:
        class no_grad:
            def __enter__(self):
                pass
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass

    class MockTokenizer:
        def __call__(self, text, **kwargs):
            return {"input_ids": [1, 2, 3]}

    transformers_mock = ModuleType("transformers")
    transformers_mock.AutoTokenizer = MockTokenizer
    transformers_mock.AutoModelForSequenceClassification = MockModel
    monkeypatch.setitem(sys.modules, "transformers", transformers_mock)
    
    torch_mock = ModuleType("torch")
    torch_mock.no_grad = MockTorch.no_grad
    monkeypatch.setitem(sys.modules, "torch", torch_mock)

    adapter = _seqcls.SeqClsAdapter(str(model_dir))
    assert adapter.labels == ["A", "B", "C"]
    
    res = adapter.predict_label("Тестовый текст")
    assert res == "B"

def test_load_adapter_helper(monkeypatch):
    class MockLog:
        def error(self, key, **kwargs):
            self.error_key = key
            self.error_path = kwargs.get("path")
            
    log = MockLog()
    res = _seqcls.load_adapter(None, "test", log)
    assert res is None

    res_missing = _seqcls.load_adapter("no_such_path", "test", log)
    assert res_missing is None
    assert log.error_key == "test_adapter_missing_FALLBACK"
    assert log.error_path == "no_such_path"
```

---

## 6. Verification Method

To verify these design proposals:
1. Review the proposed diff patches and check if the public APIs remain completely unchanged.
2. Review the unit test files. They can be created under `tests/` and run using:
   `PYTHONPATH=src .venv/bin/pytest tests/test_ner.py tests/test_embeddings.py tests/test_llm.py tests/test_seqcls.py`
3. All tests should pass synchronously in milliseconds with zero HTTP network requests or weight download attempts.
