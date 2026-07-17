"""Unit tests for uncovered NLP modules: ner, embeddings, llm, and _seqcls."""

from __future__ import annotations

import importlib.machinery
import json
import sys
from unittest.mock import MagicMock

import httpx
import pytest

from geoanalytics.nlp import _seqcls, embeddings, llm, ner


# Helper to mock modules in sys.modules
def mock_module(monkeypatch, name):
    mock_mod = MagicMock()
    # Provide a real ModuleSpec to avoid sys.modules/importlib.util.find_spec errors in Python 3.12+
    mock_mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
    monkeypatch.setitem(sys.modules, name, mock_mod)
    return mock_mod


# =========================================================================== #
# 1. nlp/_seqcls.py Tests
# =========================================================================== #

def test_is_full_model(tmp_path):
    # Case 1: PEFT adapter (adapter_config.json exists)
    peft_dir = tmp_path / "peft"
    peft_dir.mkdir()
    (peft_dir / "adapter_config.json").write_text("{}", encoding="utf-8")
    assert _seqcls.is_full_model(peft_dir) is False

    # Case 2: Full model (config.json exists, no adapter_config.json)
    full_dir = tmp_path / "full"
    full_dir.mkdir()
    (full_dir / "config.json").write_text("{}", encoding="utf-8")
    assert _seqcls.is_full_model(full_dir) is True


def test_delegated_is_full_model(tmp_path):
    from geoanalytics.nlp._seqcls import SeqClsAdapter
    from geoanalytics.nlp.sentiment import _RubertSentiment

    # Case 1: PEFT adapter
    peft_dir = tmp_path / "peft"
    peft_dir.mkdir()
    (peft_dir / "adapter_config.json").write_text("{}", encoding="utf-8")
    assert SeqClsAdapter._is_full_model(str(peft_dir)) is False
    assert _RubertSentiment._is_full_model(str(peft_dir)) is False

    # Case 2: Full model
    full_dir = tmp_path / "full"
    full_dir.mkdir()
    (full_dir / "config.json").write_text("{}", encoding="utf-8")
    assert SeqClsAdapter._is_full_model(str(full_dir)) is True
    assert _RubertSentiment._is_full_model(str(full_dir)) is True


def test_seqcls_adapter_full_model_loading(tmp_path, monkeypatch):
    model_dir = tmp_path / "my_full_model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    labels_meta = {"labels": ["neg", "pos"], "base": "my-base"}
    (model_dir / "labels.json").write_text(json.dumps(labels_meta), encoding="utf-8")

    _mock_torch = mock_module(monkeypatch, "torch")
    mock_trans = mock_module(monkeypatch, "transformers")

    mock_tokenizer = MagicMock()
    mock_model = MagicMock()
    mock_trans.AutoTokenizer.from_pretrained.return_value = mock_tokenizer
    mock_trans.AutoModelForSequenceClassification.from_pretrained.return_value = mock_model

    adapter = _seqcls.SeqClsAdapter(str(model_dir))
    assert adapter.labels == ["neg", "pos"]
    assert adapter._tokenizer == mock_tokenizer
    assert adapter._model == mock_model
    mock_trans.AutoTokenizer.from_pretrained.assert_called_once_with(str(model_dir))
    mock_trans.AutoModelForSequenceClassification.from_pretrained.assert_called_once_with(
        str(model_dir)
    )


def test_seqcls_adapter_peft_loading(tmp_path, monkeypatch):
    adapter_dir = tmp_path / "my_lora_adapter"
    adapter_dir.mkdir()
    labels_meta = {"labels": ["neg", "pos"], "base": "my-base"}
    (adapter_dir / "labels.json").write_text(json.dumps(labels_meta), encoding="utf-8")

    _mock_torch = mock_module(monkeypatch, "torch")
    mock_trans = mock_module(monkeypatch, "transformers")
    mock_peft = mock_module(monkeypatch, "peft")

    mock_tokenizer = MagicMock()
    mock_base_model = MagicMock()
    mock_peft_model = MagicMock()

    mock_trans.AutoTokenizer.from_pretrained.return_value = mock_tokenizer
    mock_trans.AutoModelForSequenceClassification.from_pretrained.return_value = mock_base_model
    mock_peft.PeftModel.from_pretrained.return_value = mock_peft_model

    adapter = _seqcls.SeqClsAdapter(str(adapter_dir))
    assert adapter.labels == ["neg", "pos"]
    assert adapter._tokenizer == mock_tokenizer
    assert adapter._model == mock_peft_model
    mock_trans.AutoTokenizer.from_pretrained.assert_called_once_with(str(adapter_dir))
    mock_trans.AutoModelForSequenceClassification.from_pretrained.assert_called_once_with(
        "my-base", num_labels=2,
        id2label={0: "neg", 1: "pos"}, label2id={"neg": 0, "pos": 1},
        ignore_mismatched_sizes=True
    )
    mock_peft.PeftModel.from_pretrained.assert_called_once_with(mock_base_model, str(adapter_dir))


def test_seqcls_adapter_predict(tmp_path, monkeypatch):
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    labels_meta = {"labels": ["neg", "pos"], "base": "my-base"}
    (model_dir / "labels.json").write_text(json.dumps(labels_meta), encoding="utf-8")

    _mock_torch = mock_module(monkeypatch, "torch")
    mock_trans = mock_module(monkeypatch, "transformers")

    mock_tokenizer = MagicMock()
    mock_tokenizer.return_value = {"input_ids": [1, 2]}
    mock_model = MagicMock()
    mock_logits = MagicMock()
    mock_logits.logits = MagicMock()
    mock_logits.logits.__getitem__.return_value.argmax.return_value = 1
    mock_model.return_value = mock_logits

    mock_trans.AutoTokenizer.from_pretrained.return_value = mock_tokenizer
    mock_trans.AutoModelForSequenceClassification.from_pretrained.return_value = mock_model

    adapter = _seqcls.SeqClsAdapter(str(model_dir))
    assert adapter.predict_label("Пример текста") == "pos"
    mock_tokenizer.assert_called_once_with(
        "Пример текста", return_tensors="pt", truncation=True, max_length=256
    )
    mock_model.assert_called_once_with(input_ids=[1, 2])


def test_load_seqcls_adapter_helper(tmp_path, monkeypatch):
    mock_logger = MagicMock()
    
    # Case 1: Path is None -> return None
    assert _seqcls.load_seqcls_adapter(None, mock_logger, name="test") is None
    
    # Case 2: Path does not exist -> warning
    assert _seqcls.load_seqcls_adapter(
        "/non-existent-dir", mock_logger, name="test", err_level="warning"
    ) is None
    mock_logger.warning.assert_called_once_with(
        "test_adapter_missing_FALLBACK", path="/non-existent-dir"
    )

    # Case 3: Path does not exist -> error
    mock_logger.reset_mock()
    assert _seqcls.load_seqcls_adapter(
        "/non-existent-dir", mock_logger, name="test", err_level="error"
    ) is None
    mock_logger.error.assert_called_once_with(
        "test_adapter_missing_FALLBACK", path="/non-existent-dir"
    )

    # Case 4: Exception during load
    labels_file = tmp_path / "labels.json"
    labels_file.write_text("corrupted json", encoding="utf-8")
    mock_logger.reset_mock()
    assert _seqcls.load_seqcls_adapter(
        str(tmp_path), mock_logger, name="test", err_level="error"
    ) is None
    assert mock_logger.error.called


def test_registry_get_model_and_status(tmp_path, monkeypatch):
    # Setup registry test
    cfg = _seqcls.ModelConfig(
        name="test_reg",
        err_level="warning",
        missing_key="missing_key_test",
        ready_key="ready_key_test",
        failed_key="failed_key_test",
        loaded_desc="loaded!",
        fallback_desc="fallback!",
        unconfigured_desc="unconfigured!"
    )
    mock_logger = MagicMock()
    
    # Clean cache
    _seqcls.registry._cache.clear()
    
    # 1. Unconfigured path
    status, desc = _seqcls.registry.get_status(None, cfg, mock_logger)
    assert status == "ok"
    assert desc == "unconfigured!"
    
    # 2. Configured path but does not exist
    _seqcls.registry._cache.clear()
    status, desc = _seqcls.registry.get_status("/non-existent", cfg, mock_logger)
    assert status == "degraded"
    assert desc == "fallback!"
    mock_logger.warning.assert_called_once_with("missing_key_test", path="/non-existent")


# =========================================================================== #
# 2. nlp/ner.py Tests
# =========================================================================== #

def test_ner_fallback_when_natasha_fails(monkeypatch):
    monkeypatch.setattr(ner, "_NatashaNer", None)
    ner._get_ner.cache_clear()

    assert ner.extract_entities("Текст") == []
    assert ner.lemmas("Текст") is None
    
    status, detail = ner.model_status()
    assert status == "degraded"
    assert "Natasha не загрузилась" in detail


def test_ner_success_with_mocked_natasha(monkeypatch):
    ner._get_ner.cache_clear()

    class MockNatashaNer:
        def extract(self, text: str):
            return [ner.Mention(text="Газпром", normal="Газпром", type="ORG")]
        def lemmatize(self, text: str):
            return ["газпром", "отчитаться"]

    monkeypatch.setattr(ner, "_NatashaNer", MockNatashaNer)

    status, detail = ner.model_status()
    assert status == "ok"
    assert "natasha" in detail

    mentions = ner.extract_entities("Газпром")
    assert len(mentions) == 1
    assert mentions[0].text == "Газпром"
    assert mentions[0].type == "ORG"

    lem = ner.lemmas("Газпром отчитался")
    assert lem == ["газпром", "отчитаться"]


def test_ner_exceptions(monkeypatch):
    ner._get_ner.cache_clear()

    class BadNatasha:
        def extract(self, text):
            raise RuntimeError("Extraction failed")
        def lemmatize(self, text):
            raise RuntimeError("Lemmatization failed")

    monkeypatch.setattr(ner, "_get_ner", lambda: BadNatasha())

    assert ner.extract_entities("Газпром") == []
    assert ner.lemmas("Газпром") is None


# =========================================================================== #
# 3. nlp/embeddings.py Tests
# =========================================================================== #

def test_embeddings_fallback_when_fastembed_fails(monkeypatch):
    def mock_init(self, model_name, cache_dir=None):
        raise ImportError("FastEmbed failure")

    monkeypatch.setattr(embeddings.Embedder, "__init__", mock_init)
    embeddings.get_embedder.cache_clear()

    assert embeddings.get_embedder() is None
    status, detail = embeddings.model_status()
    assert status == "degraded"
    assert "эмбеддер не загрузился" in detail


def test_embeddings_dimension_mismatch(monkeypatch):
    mock_fastembed = mock_module(monkeypatch, "fastembed")
    
    class MockTextEmbedding:
        def __init__(self, model_name, **kwargs):
            self.model_name = model_name
        def embed(self, texts):
            import numpy as np
            return [np.zeros(512) for _ in texts]

    mock_fastembed.TextEmbedding = MockTextEmbedding
    embeddings.get_embedder.cache_clear()

    emb = embeddings.get_embedder()
    assert emb is not None
    assert emb.dim == 512

    status, detail = embeddings.model_status()
    assert status == "degraded"
    assert "размерность модели 512 ≠ схемы БД" in detail


def test_embeddings_success_matching_dim(monkeypatch):
    from geoanalytics.storage.models import EMBEDDING_DIM
    mock_fastembed = mock_module(monkeypatch, "fastembed")

    class MockTextEmbedding:
        def __init__(self, model_name, **kwargs):
            self.model_name = model_name
        def embed(self, texts):
            import numpy as np
            return [np.zeros(EMBEDDING_DIM) for _ in texts]

    mock_fastembed.TextEmbedding = MockTextEmbedding
    embeddings.get_embedder.cache_clear()

    emb = embeddings.get_embedder()
    assert emb is not None
    assert emb.dim == EMBEDDING_DIM

    vec = emb.embed_one("тест")
    assert len(vec) == EMBEDDING_DIM
    assert isinstance(vec, list)

    status, detail = embeddings.model_status()
    assert status == "ok"


def test_embeddings_batch_with_empty_strings(monkeypatch):
    from geoanalytics.storage.models import EMBEDDING_DIM
    mock_fastembed = mock_module(monkeypatch, "fastembed")

    class MockTextEmbedding:
        def __init__(self, model_name, **kwargs):
            self.model_name = model_name
        def embed(self, texts):
            import numpy as np
            return [np.zeros(EMBEDDING_DIM) for _ in texts]

    mock_fastembed.TextEmbedding = MockTextEmbedding
    embeddings.get_embedder.cache_clear()

    emb = embeddings.get_embedder()
    assert emb is not None

    # Test batch embedding
    vectors = emb.embed(["", "тест", ""])
    assert len(vectors) == 3
    assert len(vectors[0]) == EMBEDDING_DIM
    assert len(vectors[1]) == EMBEDDING_DIM


# =========================================================================== #
# 4. nlp/llm.py Tests
# =========================================================================== #

class MockSettings:
    llm_provider = "local"
    ollama_host = "http://localhost:11434"
    llm_model = "qwen:7b"
    llm_num_ctx = 2048
    llm_num_predict = 128
    llm_temperature = 0.1
    llm_keep_alive = "5m"
    llm_timeout = 10.0
    cloud_api_key = None
    cloud_base_url = None


@pytest.fixture
def mock_settings(monkeypatch):
    s = MockSettings()
    monkeypatch.setattr(llm, "get_settings", lambda: s)
    return s


def test_is_available_ollama_success(monkeypatch, mock_settings):
    mock_settings.llm_provider = "local"
    def mock_get(url, timeout=None):
        req = httpx.Request("GET", url)
        return httpx.Response(200, json={"models": []}, request=req)
    monkeypatch.setattr(httpx, "get", mock_get)
    assert llm.is_available() is True


def test_is_available_ollama_failure(monkeypatch, mock_settings):
    mock_settings.llm_provider = "local"
    def mock_get(url, timeout=None):
        raise httpx.RequestError("Connection failed")
    monkeypatch.setattr(httpx, "get", mock_get)
    assert llm.is_available() is False


def test_is_available_cloud(mock_settings):
    mock_settings.llm_provider = "cloud"
    mock_settings.cloud_api_key = None
    assert llm.is_available() is False

    mock_settings.cloud_api_key = "sk-key"
    mock_settings.cloud_base_url = "https://api.openai.com/v1"
    assert llm.is_available() is True


def test_generate_ollama_success(monkeypatch, mock_settings):
    mock_settings.llm_provider = "local"
    def mock_post(url, json=None, timeout=None):
        assert "api/chat" in url
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"message": {"content": "Ответ Ollama"}}, request=req)
    monkeypatch.setattr(httpx, "post", mock_post)
    assert llm.generate("Привет") == "Ответ Ollama"


def test_generate_ollama_failure(monkeypatch, mock_settings):
    mock_settings.llm_provider = "local"
    def mock_post(url, json=None, timeout=None):
        req = httpx.Request("POST", url)
        return httpx.Response(500, request=req)
    monkeypatch.setattr(httpx, "post", mock_post)
    assert llm.generate("Привет") is None


def test_generate_cloud_success(monkeypatch, mock_settings):
    mock_settings.llm_provider = "cloud"
    mock_settings.cloud_api_key = "sk-key"
    mock_settings.cloud_base_url = "https://api.openai.com/v1"
    def mock_post(url, headers=None, json=None, timeout=None):
        assert "completions" in url
        req = httpx.Request("POST", url, headers=headers)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "Ответ Cloud"}}]}, request=req
        )
    monkeypatch.setattr(httpx, "post", mock_post)
    assert llm.generate("Привет") == "Ответ Cloud"


def test_generate_ollama_temperature_override(monkeypatch, mock_settings):
    mock_settings.llm_provider = "local"
    captured_payload = None

    def mock_post(url, **kwargs):
        nonlocal captured_payload
        captured_payload = kwargs.get("json")
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"message": {"content": "Ответ Ollama"}}, request=req)

    monkeypatch.setattr(httpx, "post", mock_post)
    assert llm.generate("Привет", temperature=0.9) == "Ответ Ollama"
    assert captured_payload is not None
    assert captured_payload["options"]["temperature"] == 0.9


def test_generate_cloud_failure_logging(monkeypatch, mock_settings):
    mock_settings.llm_provider = "cloud"
    mock_settings.cloud_api_key = "sk-key"
    mock_settings.cloud_base_url = "https://api.openai.com/v1"

    def mock_post(url, **kwargs):
        req = httpx.Request("POST", url)
        return httpx.Response(500, text="Cloud Internal Error", request=req)

    monkeypatch.setattr(httpx, "post", mock_post)
    
    mock_log = MagicMock()
    monkeypatch.setattr(llm, "log", mock_log)

    assert llm.generate("Привет") is None
    mock_log.warning.assert_called_once()
    assert mock_log.warning.call_args[0][0] == "cloud_llm_failed"


def test_model_loader_flow(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from geoanalytics.nlp._seqcls import ModelConfig, ModelLoader, registry
    
    cfg = ModelConfig(
        name="test_loader",
        err_level="warning",
        missing_key="missing_key_loader",
        ready_key="ready_key_loader",
        failed_key="failed_key_loader",
        loaded_desc="loaded!",
        fallback_desc="fallback!",
        unconfigured_desc="unconfigured!"
    )
    mock_logger = MagicMock()
    path_val = None
    
    # 1. Initialize ModelLoader
    loader = ModelLoader(cfg, lambda: path_val, mock_logger)
    
    # 2. Clean cache
    registry._cache.clear()
    
    # 3. Unconfigured path
    status, desc = loader.get_status()
    assert status == "ok"
    assert desc == "unconfigured!"
    assert loader.get_model() is None
    
    # 4. Configured path but does not exist
    registry._cache.clear()
    path_val = "/non-existent-path"
    status, desc = loader.get_status()
    assert status == "degraded"
    assert desc == "fallback!"
    assert loader.get_model() is None
    mock_logger.warning.assert_called_with("missing_key_loader", path="/non-existent-path")


# =========================================================================== #
# 5. nlp/numeric.py Unicode space Tests
# =========================================================================== #

def test_extract_numbers_unicode_spaces():
    from geoanalytics.nlp.numeric import extract_numbers, DIVIDEND
    
    # \u2009 is Thin Space, \u202f is Narrow No-Break Space
    text = "дивиденды в размере 1\u2009200\u202f500,5 руб. на акцию"
    facts = extract_numbers(text)
    assert len(facts) == 1
    assert facts[0].kind == DIVIDEND
    assert facts[0].value == 1200500.5
    assert facts[0].unit == "RUB"

