"""Robustness and stress tests for NLP modules (SeqClsRegistry and _RubertSentiment)."""

from __future__ import annotations

import importlib.machinery
import json
import sys
import threading
from unittest.mock import MagicMock

import pytest

from geoanalytics.core.types import Sentiment
from geoanalytics.nlp import _seqcls, sentiment
from geoanalytics.nlp._seqcls import ModelConfig, SeqClsRegistry
from geoanalytics.nlp.sentiment import analyze


# Helper to mock modules in sys.modules
def mock_module(monkeypatch, name):
    mock_mod = MagicMock()
    mock_mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
    monkeypatch.setitem(sys.modules, name, mock_mod)
    return mock_mod

@pytest.fixture(autouse=True)
def clean_caches():
    sentiment._get_model.cache_clear()
    _seqcls.registry._cache.clear()

def test_concurrency_sentiment(monkeypatch):
    """Test that multiple threads calling sentiment.analyze concurrently
    do not cause race conditions or crashes."""
    mock_torch = mock_module(monkeypatch, "torch")
    mock_trans = mock_module(monkeypatch, "transformers")
    
    mock_tokenizer = MagicMock()
    mock_tokenizer.return_value = {"input_ids": [1]}
    mock_model = MagicMock()
    mock_logits = MagicMock()
    mock_logits.logits = mock_torch.tensor([[0.1, 0.8, 0.1]])
    mock_model.return_value = mock_logits
    
    mock_torch.softmax.return_value = [mock_torch.tensor([0.1, 0.8, 0.1])]
    
    mock_trans.AutoTokenizer.from_pretrained.return_value = mock_tokenizer
    mock_trans.AutoModelForSequenceClassification.from_pretrained.return_value = mock_model
    
    class FakeSettings:
        sentiment_model = "blanchefort/rubert-base-cased-sentiment"
        sentiment_adapter_path = None
        
    monkeypatch.setattr(sentiment, "get_settings", lambda: FakeSettings())
    
    errors = []
    def worker():
        try:
            label, score = analyze("Тестовый текст для проверки тональности")
            assert label == Sentiment.POSITIVE
        except Exception as e:
            errors.append(e)
            
    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    assert len(errors) == 0, f"Errors in concurrent requests: {errors}"

def test_concurrency_registry(monkeypatch, tmp_path):
    """Test that multiple threads accessing SeqClsRegistry concurrently do not crash."""
    _mock_torch = mock_module(monkeypatch, "torch")
    mock_trans = mock_module(monkeypatch, "transformers")
    
    model_dir = tmp_path / "concurrent_model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    labels_meta = {"labels": ["event_a", "event_b"], "base": "my-base"}
    (model_dir / "labels.json").write_text(json.dumps(labels_meta), encoding="utf-8")
    
    mock_tokenizer = MagicMock()
    mock_model = MagicMock()
    mock_trans.AutoTokenizer.from_pretrained.return_value = mock_tokenizer
    mock_trans.AutoModelForSequenceClassification.from_pretrained.return_value = mock_model
    
    cfg = ModelConfig(name="concurrent_test")
    logger = MagicMock()
    
    registry = SeqClsRegistry()
    
    errors = []
    def worker():
        try:
            model = registry.get_model(str(model_dir), cfg, logger)
            assert model is not None
            assert model.labels == ["event_a", "event_b"]
        except Exception as e:
            errors.append(e)
            
    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    assert len(errors) == 0, f"Errors in concurrent registry access: {errors}"

def test_corrupted_configuration_raises_in_analyze(monkeypatch):
    """Verify that a corrupted configuration raising an exception in get_settings
    is caught by analyze() and falls back gracefully to lexicon sentiment."""
    def bad_get_settings():
        raise RuntimeError("Configuration file corrupted or invalid")
        
    monkeypatch.setattr(sentiment, "get_settings", bad_get_settings)
    
    label, score = analyze("Тестовый текст")
    assert label == Sentiment.NEUTRAL
    assert score == 0.0

def test_missing_settings_raises_attribute_error_in_analyze(monkeypatch):
    """Verify that if sentiment_model setting is missing, it falls back gracefully to lexicon."""
    class BadSettings:
        # sentiment_model is missing
        sentiment_adapter_path = None
        
    monkeypatch.setattr(sentiment, "get_settings", lambda: BadSettings())
    
    label, score = analyze("Тестовый текст")
    assert label == Sentiment.NEUTRAL
    assert score == 0.0

def test_invalid_path_type_raises_in_registry(monkeypatch):
    """Verify that passing an invalid path type to load_seqcls_adapter is caught
    and returns None."""
    logger = MagicMock()
    assert _seqcls.load_seqcls_adapter(12345, logger, name="test") is None

def test_path_exists_raising_oserror_propagates(monkeypatch):
    """Verify that if Path.exists() raises an OSError, it is caught by
    load_seqcls_adapter and returns None."""
    logger = MagicMock()
    monkeypatch.setattr(_seqcls.Path, "exists", MagicMock(side_effect=OSError("Fs error")))
    assert _seqcls.load_seqcls_adapter("some/path", logger, name="test") is None
