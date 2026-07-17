# NLP Refactoring & Unit Test Design Handoff Report

## 1. Observation

Direct observations of the codebase:

### A. Duplicate SeqClsAdapter Loading Logic
1. **`src/geoanalytics/nlp/classify.py`** (lines 64-78):
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
2. **`src/geoanalytics/nlp/significance.py`** (lines 149-172):
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
3. **`src/geoanalytics/nlp/temporal.py`** (lines 114-131):
   ```python
   @lru_cache(maxsize=1)
   def _model():
       """SeqClsAdapter temporal-классификатора; None — фолбэк (статус неизвестен)."""
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
4. **`src/geoanalytics/nlp/aspect.py`** (lines 38-53):
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

### B. Duplicate `_is_full_model` Detection Logic
1. **`src/geoanalytics/nlp/_seqcls.py`** (lines 25-30):
   ```python
       @staticmethod
       def _is_full_model(path: str) -> bool:
           """Каталог — полностью дообученная модель (config.json без adapter_config.json),
           а не PEFT-адаптер (adapter_config.json)."""
           p = Path(path)
           return (p / "config.json").exists() and not (p / "adapter_config.json").exists()
   ```
2. **`src/geoanalytics/nlp/sentiment.py`** (lines 115-120):
   ```python
       @staticmethod
       def _is_full_model(path: str) -> bool:
           """Каталог — полностью дообученная модель (config.json без adapter_config.json),
           а не PEFT-адаптер (adapter_config.json)."""
           p = Path(path)
           return (p / "config.json").exists() and not (p / "adapter_config.json").exists()
   ```

### C. Private Imports from `numeric.py`
1. **`src/geoanalytics/nlp/fundamentals.py`** (line 17):
   ```python
   from geoanalytics.nlp.numeric import _MULT, _to_float
   ```
2. **`src/geoanalytics/nlp/numeric.py`** (lines 30, 96-97):
   ```python
   _MULT = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}
   ...
   def _to_float(raw: str) -> float:
       return float(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))
   ```

### D. File Line Counts (Verification of the < 600-line constraint)
- `_seqcls.py`: 61 lines
- `classify.py`: 102 lines
- `significance.py`: 201 lines
- `temporal.py`: 159 lines
- `aspect.py`: 112 lines
- `sentiment.py`: 199 lines
- `fundamentals.py`: 135 lines
- `numeric.py`: 163 lines
- `ner.py`: 120 lines
- `embeddings.py`: 77 lines
- `llm.py`: 163 lines

All target files are well under 600 lines. Proposing these modifications will not cause any file to exceed 600 lines.

---

## 2. Logic Chain

1. **Adapter Loading Logic Consolidation**:
   - The loading routines in `classify.py`, `significance.py`, `temporal.py`, and `aspect.py` are structurally identical: they read a path, verify its existence, attempt to instantiate `SeqClsAdapter`, log statuses on success/failure, and return the loaded model or `None` on fallback.
   - They differ slightly in logging levels (warning vs. error) and specific log messages.
   - Proposing a unified helper function `load_adapter` in `_seqcls.py` that accepts options for logging levels (`error_on_missing`, `error_on_fail`) and custom messages (`missing_msg`, `ready_msg`, `fail_msg`) allows each client to preserve its exact logging behavior while deleting duplicate code blocks.

2. **Full Model Detection Consolidation**:
   - Both `SeqClsAdapter._is_full_model` and `_RubertSentiment._is_full_model` check if `config.json` exists and `adapter_config.json` does not.
   - Extracting this to a module-level helper function `is_full_model(path: str | Path) -> bool` in `_seqcls.py` and referencing it in both places removes duplication cleanly. Keeping the static methods as aliases or directly calling the shared function protects any internal references.

3. **Exposing `numeric.py` Private Symbols**:
   - `fundamentals.py` and `connectors/smartlab.py` rely on `_MULT` and `_to_float`.
   - Exposing them as public API variables `MULT` and `to_float` in `numeric.py` is the standard Python practice.
   - Defining `_MULT = MULT` and `_to_float = to_float` inside `numeric.py` ensures any third-party or legacy usage (like in `smartlab.py` and other modules) does not break, satisfying the strict backward compatibility constraint.

4. **Testing Strategy**:
   - `ner.py`: Uses `natasha` which runs lightweight CPU-only models. We can write tests utilizing both the actual library (since it runs quickly and is installed) and mock fallbacks by monkeypatching `_get_ner()` or individual method calls.
   - `embeddings.py`: Uses `fastembed`. To satisfy CODE_ONLY mode constraints (no internet download of weights) and keep tests extremely fast, we must patch/mock `fastembed.TextEmbedding` to return dummy vectors of size 1024.
   - `llm.py`: Uses `httpx` targeting Ollama and OpenAI-compatible providers. We can use the project-standard `respx` library to intercept HTTP calls and assert correct API payloads and response parsed outputs.
   - `_seqcls.py`: Loads `transformers` and `peft`. We must mock `transformers.AutoTokenizer`, `transformers.AutoModelForSequenceClassification`, and `peft.PeftModel` so that the model instantiation branch and `predict_label` work instantly without loading real models.

---

## 3. Caveats

- We assume the existing environment settings and library packages (e.g., `natasha`, `fastembed`, `respx`, `peft`) are installed and functional.
- The unit test designs rely heavily on standard pytest monkeypatching. If the structure of the mocked packages (`transformers`, `fastembed`) changes significantly in future releases, the mocks will need to be updated.
- We must make sure that `is_full_model` is imported cleanly without introducing circular dependencies. (Since `sentiment.py` imports `is_full_model` from `_seqcls.py`, and `_seqcls.py` does not import anything from `sentiment.py`, there is no circular dependency).

---

## 4. Conclusion

We propose the following structural refactorings and new unit test files:

### Proposed Refactored Files

#### 1. `src/geoanalytics/nlp/_seqcls.py`
Add the shared loader helper `load_adapter` and the shared model detector helper `is_full_model` to `_seqcls.py`:
```python
"""Общий загрузчик дообученных seq-классификаторов (M6.5)..."""

from __future__ import annotations

import json
from pathlib import Path

def is_full_model(path: str | Path) -> bool:
    """Каталог — полностью дообученная модель (config.json без adapter_config.json),
    а не PEFT-адаптер (adapter_config.json)."""
    p = Path(path)
    return (p / "config.json").exists() and not (p / "adapter_config.json").exists()


def load_adapter(
    path: str | None,
    name: str,
    logger,
    *,
    error_on_missing: bool = True,
    error_on_fail: bool = True,
    missing_msg: str | None = None,
    ready_msg: str | None = None,
    fail_msg: str | None = None,
) -> SeqClsAdapter | None:
    """Общий вспомогательный метод для загрузки SeqClsAdapter с логированием."""
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        msg = missing_msg or f"{name}_adapter_missing_FALLBACK"
        if error_on_missing:
            logger.error(msg, path=path)
        else:
            logger.warning(msg, path=path)
        return None
    try:
        clf = SeqClsAdapter(path)
        msg = ready_msg or f"{name}_model_ready"
        logger.info(msg, path=path)
        return clf
    except Exception as exc:
        msg = fail_msg or f"{name}_model_failed_FALLBACK"
        if error_on_fail:
            logger.error(msg, error=str(exc))
        else:
            logger.warning(msg, error=str(exc))
        return None


class SeqClsAdapter:
    """Загруженный seq-классификатор: text → строковая метка (argmax логитов)."""

    @staticmethod
    def _is_full_model(path: str) -> bool:
        return is_full_model(path)

    def __init__(self, adapter_path: str) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        meta = json.loads((Path(adapter_path) / "labels.json").read_text(encoding="utf-8"))
        self.labels: list[str] = meta["labels"]
        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(adapter_path)
        if is_full_model(adapter_path):
            self._model = AutoModelForSequenceClassification.from_pretrained(adapter_path)
        else:
            from peft import PeftModel

            id2label = dict(enumerate(self.labels))
            label2id = {lab: i for i, lab in id2label.items()}
            model = AutoModelForSequenceClassification.from_pretrained(
                meta["base"], num_labels=len(self.labels),
                id2label=id2label, label2id=label2id, ignore_mismatched_sizes=True,
            )
            self._model = PeftModel.from_pretrained(model, adapter_path)
        self._model.eval()

    def predict_label(self, text: str, max_length: int = 256) -> str:
        inputs = self._tokenizer(text, return_tensors="pt", truncation=True,
                                 max_length=max_length)
        with self._torch.no_grad():
            logits = self._model(**inputs).logits
        return self.labels[int(logits[0].argmax())]
```

#### 2. `src/geoanalytics/nlp/classify.py`
Simplify `_get_classifier` using `load_adapter`:
```python
from geoanalytics.nlp._seqcls import SeqClsAdapter, load_adapter

@lru_cache
def _get_classifier() -> SeqClsAdapter | None:
    return load_adapter(
        get_settings().event_adapter_path,
        "event",
        log,
        error_on_missing=False,
        error_on_fail=False,
        missing_msg="event_adapter_missing",
        ready_msg="event_classifier_ready",
        fail_msg="event_classifier_failed_rules",
    )
```

#### 3. `src/geoanalytics/nlp/significance.py`
Simplify `_get_model` using `load_adapter`:
```python
from geoanalytics.nlp._seqcls import load_adapter

@lru_cache
def _get_model():
    return load_adapter(
        get_settings().significance_adapter_path,
        "significance",
        log,
        missing_msg="significance_adapter_missing_FORMULA_FALLBACK",
        fail_msg="significance_model_failed_FORMULA_FALLBACK",
    )
```

#### 4. `src/geoanalytics/nlp/temporal.py`
Simplify `_model` using `load_adapter`:
```python
from geoanalytics.nlp._seqcls import load_adapter

@lru_cache(maxsize=1)
def _model():
    """SeqClsAdapter temporal-классификатора; None — фолбэк (статус неизвестен)."""
    return load_adapter(
        get_settings().temporal_adapter_path,
        "temporal",
        log,
        fail_msg="temporal_model_load_failed_FALLBACK",
    )
```

#### 5. `src/geoanalytics/nlp/aspect.py`
Simplify `_load` using `load_adapter`:
```python
from geoanalytics.nlp._seqcls import load_adapter

def _load(path: str | None, name: str):
    """Загрузка SeqClsAdapter с громкой деградацией (как у significance, Б1)."""
    return load_adapter(path, name, log)
```

#### 6. `src/geoanalytics/nlp/sentiment.py`
Import `is_full_model` from `_seqcls.py` and replace static method:
```python
from geoanalytics.nlp._seqcls import is_full_model

class _RubertSentiment:
    ...
    @staticmethod
    def _is_full_model(path: str) -> bool:
        return is_full_model(path)
```

#### 7. `src/geoanalytics/nlp/numeric.py`
Rename private symbols to public ones, maintaining backward-compatible aliases:
```python
MULT = {"тыс": 1e3, "млн": 1e6, "млрд": 1e9, "трлн": 1e12}
_MULT = MULT

...

def to_float(raw: str) -> float:
    return float(raw.replace(" ", "").replace("\xa0", "").replace(",", "."))

_to_float = to_float
```

#### 8. `src/geoanalytics/nlp/fundamentals.py`
Import the public symbols from `numeric.py`:
```python
from geoanalytics.nlp.numeric import MULT, to_float
```

---

### Proposed Unit Test Files

#### 1. `tests/test_ner.py`
```python
from __future__ import annotations

import pytest
from geoanalytics.nlp import ner
from geoanalytics.nlp.ner import Mention, _NatashaNer, extract_entities, lemmas, model_status


def test_extract_entities_success():
    # Natasha should extract entities correctly
    results = extract_entities("Сбербанк купил Яндекс в Москве.")
    types = {m.type for m in results}
    normals = {m.normal for m in results}
    assert "ORG" in types
    assert "LOC" in types
    assert "Сбербанк" in normals
    assert "Яндекс" in normals
    assert "Москва" in normals


def test_lemmas_success():
    words = lemmas("Совет директоров рекомендовал дивиденды")
    assert words is not None
    assert "совет" in words
    assert "дивиденд" in words


def test_extract_entities_unavailable(monkeypatch):
    monkeypatch.setattr(ner, "_get_ner", lambda: None)
    assert extract_entities("Сбербанк") == []


def test_lemmas_unavailable(monkeypatch):
    monkeypatch.setattr(ner, "_get_ner", lambda: None)
    assert lemmas("Сбербанк") is None


def test_model_status_ok():
    status, detail = model_status()
    assert status == "ok"
    assert "natasha" in detail


def test_model_status_degraded(monkeypatch):
    monkeypatch.setattr(ner, "_get_ner", lambda: None)
    status, detail = model_status()
    assert status == "degraded"
    assert "Natasha не загрузилась" in detail


def test_extract_entities_exception(monkeypatch):
    class BadNer:
        def extract(self, text):
            raise ValueError("Extraction error")

    monkeypatch.setattr(ner, "_get_ner", lambda: BadNer())
    assert extract_entities("Сбербанк") == []


def test_lemmas_exception(monkeypatch):
    class BadNer:
        def lemmatize(self, text):
            raise ValueError("Lemmatization error")

    monkeypatch.setattr(ner, "_get_ner", lambda: BadNer())
    assert lemmas("Сбербанк") is None
```

#### 2. `tests/test_embeddings.py`
```python
from __future__ import annotations

import numpy as np
import pytest
from unittest.mock import MagicMock
import fastembed
from geoanalytics.nlp import embeddings
from geoanalytics.nlp.embeddings import get_embedder, model_status


def test_embedder_success(monkeypatch):
    mock_model = MagicMock()
    mock_model.embed.return_value = [np.ones(1024)]
    
    mock_text_embedding = MagicMock(return_value=mock_model)
    monkeypatch.setattr(fastembed, "TextEmbedding", mock_text_embedding)
    
    get_embedder.cache_clear()
    embedder = get_embedder()
    
    assert embedder is not None
    assert embedder.dim == 1024
    
    # Test embed_one
    vec = embedder.embed_one("тест")
    assert len(vec) == 1024
    assert vec == [1.0] * 1024
    
    # Test embed batch
    vecs = embedder.embed(["тест", "проверка"])
    assert len(vecs) == 1
    assert len(vecs[0]) == 1024


def test_embedder_unavailable(monkeypatch):
    def bad_init(*args, **kwargs):
        raise RuntimeError("Model download failed")
        
    monkeypatch.setattr(fastembed, "TextEmbedding", bad_init)
    
    get_embedder.cache_clear()
    assert get_embedder() is None


def test_model_status_ok(monkeypatch):
    class MockEmbedder:
        dim = 1024
        model_name = "multilingual-e5-large"
        
    monkeypatch.setattr(embeddings, "get_embedder", lambda: MockEmbedder())
    status, detail = model_status()
    assert status == "ok"
    assert "multilingual-e5-large" in detail


def test_model_status_degraded_none(monkeypatch):
    monkeypatch.setattr(embeddings, "get_embedder", lambda: None)
    status, detail = model_status()
    assert status == "degraded"
    assert "эмбеддер не загрузился" in detail


def test_model_status_degraded_mismatch(monkeypatch):
    class MockEmbedder:
        dim = 512
        model_name = "small-model"
        
    monkeypatch.setattr(embeddings, "get_embedder", lambda: MockEmbedder())
    status, detail = model_status()
    assert status == "degraded"
    assert "размерность модели 512 ≠ схемы БД" in detail
```

#### 3. `tests/test_llm.py`
```python
from __future__ import annotations

import pytest
import respx
from geoanalytics.nlp import llm
from geoanalytics.nlp.llm import generate, is_available


@respx.mock
def test_generate_ollama_success(monkeypatch):
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
        return_value=respx.MockResponse(200, json={"message": {"content": "Ollama response"}})
    )
    
    res = generate("Prompt text", system="System instructions")
    assert res == "Ollama response"
    assert route.called
    
    payload = route.calls.last.request.content
    import json
    data = json.loads(payload)
    assert data["model"] == "qwen2"
    assert data["messages"] == [
        {"role": "system", "content": "System instructions"},
        {"role": "user", "content": "Prompt text"}
    ]
    assert data["options"]["num_ctx"] == 4096
    assert data["options"]["temperature"] == 0.1


@respx.mock
def test_generate_ollama_failed(monkeypatch):
    class MockSettings:
        llm_provider = "local"
        ollama_host = "http://localhost:11434"
        llm_model = "qwen2"
        llm_num_ctx = 2048
        llm_num_predict = 256
        llm_temperature = 0.5
        llm_keep_alive = "5m"
        llm_timeout = 5.0

    monkeypatch.setattr(llm, "get_settings", lambda: MockSettings())
    
    respx.post("http://localhost:11434/api/chat").mock(
        return_value=respx.MockResponse(500)
    )
    
    assert generate("hello") is None


@respx.mock
def test_generate_cloud_success(monkeypatch):
    class MockSettings:
        llm_provider = "cloud"
        cloud_base_url = "https://api.deepseek.com"
        cloud_api_key = "sk-deepseek-key"
        llm_model = "deepseek-chat"
        llm_temperature = 0.1
        llm_timeout = 30.0

    monkeypatch.setattr(llm, "get_settings", lambda: MockSettings())
    
    route = respx.post("https://api.deepseek.com/chat/completions").mock(
        return_value=respx.MockResponse(200, json={"choices": [{"message": {"content": "Cloud response"}}]})
    )
    
    res = generate("Prompt text")
    assert res == "Cloud response"
    assert route.called
    assert route.calls.last.request.headers["Authorization"] == "Bearer sk-deepseek-key"


def test_generate_cloud_not_configured(monkeypatch):
    class MockSettings:
        llm_provider = "cloud"
        cloud_base_url = None
        cloud_api_key = None
        llm_timeout = 30.0

    monkeypatch.setattr(llm, "get_settings", lambda: MockSettings())
    assert generate("hello") is None


@respx.mock
def test_is_available_ollama(monkeypatch):
    class MockSettings:
        llm_provider = "local"
        ollama_host = "http://localhost:11434"

    monkeypatch.setattr(llm, "get_settings", lambda: MockSettings())
    
    route = respx.get("http://localhost:11434/api/tags").mock(
        return_value=respx.MockResponse(200)
    )
    assert is_available() is True
    
    route.mock(return_value=respx.MockResponse(500))
    assert is_available() is False


def test_is_available_cloud(monkeypatch):
    class MockSettings:
        llm_provider = "cloud"
        cloud_base_url = "https://api.deepseek.com"
        cloud_api_key = "sk-key"

    monkeypatch.setattr(llm, "get_settings", lambda: MockSettings())
    assert is_available() is True
    
    class MockSettingsUnconfigured:
        llm_provider = "cloud"
        cloud_base_url = "https://api.deepseek.com"
        cloud_api_key = None

    monkeypatch.setattr(llm, "get_settings", lambda: MockSettingsUnconfigured())
    assert is_available() is False
```

#### 4. `tests/test_seqcls.py`
```python
from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest
import torch
import transformers
import peft
from geoanalytics.nlp import _seqcls
from geoanalytics.nlp._seqcls import SeqClsAdapter, is_full_model, load_adapter


def test_is_full_model(tmp_path):
    path = tmp_path
    
    # 1. Neither exists
    assert not is_full_model(path)
    
    # 2. Both config.json and adapter_config.json exist
    (path / "config.json").write_text("{}", encoding="utf-8")
    (path / "adapter_config.json").write_text("{}", encoding="utf-8")
    assert not is_full_model(path)
    
    # 3. Only config.json exists
    (path / "adapter_config.json").unlink()
    assert is_full_model(path)


def test_seqcls_adapter_full_model(tmp_path, monkeypatch):
    labels_path = tmp_path / "labels.json"
    labels_path.write_text('{"labels": ["negative", "neutral", "positive"], "base": "bert-base"}', encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    
    mock_tokenizer = MagicMock()
    mock_tokenizer.return_value = {"input_ids": torch.tensor([[1, 2, 3]])}
    
    mock_model = MagicMock()
    mock_logits = MagicMock()
    mock_logits.logits = torch.tensor([[1.0, 2.0, 5.0]])  # Argmax = 2 ("positive")
    mock_model.return_value = mock_logits
    
    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", lambda path: mock_tokenizer)
    monkeypatch.setattr(transformers.AutoModelForSequenceClassification, "from_pretrained", lambda path, **kwargs: mock_model)
    
    adapter = SeqClsAdapter(str(tmp_path))
    assert adapter.labels == ["negative", "neutral", "positive"]
    
    res = adapter.predict_label("sample text")
    assert res == "positive"
    mock_tokenizer.assert_called_with("sample text", return_tensors="pt", truncation=True, max_length=256)


def test_seqcls_adapter_lora_model(tmp_path, monkeypatch):
    labels_path = tmp_path / "labels.json"
    labels_path.write_text('{"labels": ["low", "medium", "high"], "base": "bert-base"}', encoding="utf-8")
    adapter_config_path = tmp_path / "adapter_config.json"
    adapter_config_path.write_text("{}", encoding="utf-8")
    
    mock_tokenizer = MagicMock()
    mock_tokenizer.return_value = {"input_ids": torch.tensor([[1, 2, 3]])}
    
    mock_base_model = MagicMock()
    mock_peft_model = MagicMock()
    mock_logits = MagicMock()
    mock_logits.logits = torch.tensor([[5.0, 2.0, 1.0]])  # Argmax = 0 ("low")
    mock_peft_model.return_value = mock_logits
    
    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", lambda path: mock_tokenizer)
    monkeypatch.setattr(transformers.AutoModelForSequenceClassification, "from_pretrained", lambda path, **kwargs: mock_base_model)
    monkeypatch.setattr(peft.PeftModel, "from_pretrained", lambda model, path: mock_peft_model)
    
    adapter = SeqClsAdapter(str(tmp_path))
    assert adapter.labels == ["low", "medium", "high"]
    
    res = adapter.predict_label("sample text")
    assert res == "low"


def test_load_adapter_success(tmp_path, monkeypatch):
    labels_path = tmp_path / "labels.json"
    labels_path.write_text('{"labels": ["ok"], "base": "b"}', encoding="utf-8")
    
    mock_adapter = MagicMock()
    monkeypatch.setattr(_seqcls, "SeqClsAdapter", lambda path: mock_adapter)
    
    mock_logger = MagicMock()
    
    res = load_adapter(str(tmp_path), "test_model", mock_logger)
    assert res == mock_adapter
    mock_logger.info.assert_called_once_with("test_model_model_ready", path=str(tmp_path))


def test_load_adapter_missing(monkeypatch):
    mock_logger = MagicMock()
    
    # 1. Empty path
    assert load_adapter(None, "test_model", mock_logger) is None
    
    # 2. Non-existent path
    res = load_adapter("/nonexistent/path", "test_model", mock_logger, error_on_missing=True)
    assert res is None
    mock_logger.error.assert_called_once_with("test_model_adapter_missing_FALLBACK", path="/nonexistent/path")
    
    # 3. Non-existent path (warning level)
    mock_logger.reset_mock()
    res = load_adapter("/nonexistent/path", "test_model", mock_logger, error_on_missing=False)
    assert res is None
    mock_logger.warning.assert_called_once_with("test_model_adapter_missing_FALLBACK", path="/nonexistent/path")


def test_load_adapter_failed_to_load(tmp_path, monkeypatch):
    labels_path = tmp_path / "labels.json"
    labels_path.write_text('{"labels": ["ok"], "base": "b"}', encoding="utf-8")
    
    def bad_adapter_init(path):
        raise ValueError("Corrupted weight file")
        
    monkeypatch.setattr(_seqcls, "SeqClsAdapter", bad_adapter_init)
    mock_logger = MagicMock()
    
    res = load_adapter(str(tmp_path), "test_model", mock_logger, error_on_fail=True)
    assert res is None
    mock_logger.error.assert_called_once_with("test_model_model_failed_FALLBACK", error="Corrupted weight file")
```

---

## 5. Verification Method

To independently verify the refactoring logic and the correctness of the unit test designs:

1. **Verify Line Counts Constraint**:
   Ensure all target modules remain strictly under 600 lines after incorporating the loader extraction, the model detection helper extraction, and the public numeric helpers. Use:
   ```bash
   wc -l src/geoanalytics/nlp/*.py
   ```
2. **Execute pytest Suite**:
   Once the refactoring edits are implemented by the implementing agent and the new unit tests are placed in `tests/`, run the full pytest suite to verify 100% pass rate:
   ```bash
   source .venv/bin/activate && pytest tests/
   ```
3. **Verify CLI Health status**:
   Ensure that the `model_status` helper of each module returns identical statuses under configured/degraded states. Run:
   ```bash
   ./geo-ctl.sh status
   ```
   or inspect the health check endpoints.
