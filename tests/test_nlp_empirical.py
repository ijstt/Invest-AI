"""Independent empirical verification of refactored NLP modules."""

from __future__ import annotations

import json
import sys
import importlib.machinery
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from geoanalytics.nlp import _seqcls
from geoanalytics.nlp import sentiment
from geoanalytics.nlp import aspect
from geoanalytics.nlp import classify
from geoanalytics.nlp import significance
from geoanalytics.nlp import temporal


# =========================================================================== #
# Helper Mocking Function
# =========================================================================== #
def mock_module(monkeypatch, name):
    mock_mod = MagicMock()
    mock_mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
    monkeypatch.setitem(sys.modules, name, mock_mod)
    return mock_mod


# =========================================================================== #
# 1. is_full_model() Detection Tests
# =========================================================================== #
def test_is_full_model_detection(tmp_path):
    # Case 1: Neither exists
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert _seqcls.is_full_model(empty_dir) is False

    # Case 2: Only config.json exists -> Full Model
    full_dir = tmp_path / "full_model"
    full_dir.mkdir()
    (full_dir / "config.json").write_text("{}", encoding="utf-8")
    assert _seqcls.is_full_model(full_dir) is True

    # Case 3: Only adapter_config.json exists -> LoRA Adapter
    lora_dir = tmp_path / "lora_model"
    lora_dir.mkdir()
    (lora_dir / "adapter_config.json").write_text("{}", encoding="utf-8")
    assert _seqcls.is_full_model(lora_dir) is False

    # Case 4: Both exist -> LoRA Adapter
    both_dir = tmp_path / "both_model"
    both_dir.mkdir()
    (both_dir / "config.json").write_text("{}", encoding="utf-8")
    (both_dir / "adapter_config.json").write_text("{}", encoding="utf-8")
    assert _seqcls.is_full_model(both_dir) is False

    # Case 5: Path does not exist
    non_existent = tmp_path / "non_existent"
    assert _seqcls.is_full_model(non_existent) is False


# =========================================================================== #
# 2. sentiment.py Fallback and Status Tests
# =========================================================================== #
@pytest.fixture(autouse=True)
def clear_caches():
    _seqcls.registry._cache.clear()
    sentiment._get_model.cache_clear()


def test_sentiment_unconfigured(monkeypatch):
    """Test sentiment with empty adapter path."""
    mock_settings = MagicMock()
    mock_settings.sentiment_model = "blanchefort/rubert-base-cased-sentiment"
    mock_settings.sentiment_adapter_path = None
    monkeypatch.setattr(sentiment, "get_settings", lambda: mock_settings)

    mock_torch = mock_module(monkeypatch, "torch")
    mock_trans = mock_module(monkeypatch, "transformers")
    
    mock_tokenizer = MagicMock()
    mock_model = MagicMock()
    mock_trans.AutoTokenizer.from_pretrained.return_value = mock_tokenizer
    mock_trans.AutoModelForSequenceClassification.from_pretrained.return_value = mock_model

    # Get model and check status
    status, desc = sentiment.model_status()
    assert status == "ok"
    assert "rubert" in desc
    assert "(база)" in desc

    # Verify model loading parameters
    mock_trans.AutoTokenizer.from_pretrained.assert_called_with("blanchefort/rubert-base-cased-sentiment")
    mock_trans.AutoModelForSequenceClassification.from_pretrained.assert_called_with("blanchefort/rubert-base-cased-sentiment")


def test_sentiment_configured_and_loaded_lora(tmp_path, monkeypatch):
    """Test sentiment with a valid LoRA adapter path."""
    adapter_dir = tmp_path / "lora_adapter"
    adapter_dir.mkdir()
    labels_meta = {"labels": ["neutral", "positive", "negative"]}
    (adapter_dir / "labels.json").write_text(json.dumps(labels_meta), encoding="utf-8")

    mock_settings = MagicMock()
    mock_settings.sentiment_model = "blanchefort/rubert-base-cased-sentiment"
    mock_settings.sentiment_adapter_path = str(adapter_dir)
    monkeypatch.setattr(sentiment, "get_settings", lambda: mock_settings)

    mock_torch = mock_module(monkeypatch, "torch")
    mock_trans = mock_module(monkeypatch, "transformers")
    mock_peft = mock_module(monkeypatch, "peft")
    
    mock_tokenizer = MagicMock()
    mock_model = MagicMock()
    mock_peft_model = MagicMock()
    
    mock_trans.AutoTokenizer.from_pretrained.return_value = mock_tokenizer
    mock_trans.AutoModelForSequenceClassification.from_pretrained.return_value = mock_model
    mock_peft.PeftModel.from_pretrained.return_value = mock_peft_model

    status, desc = sentiment.model_status()
    assert status == "ok"
    assert "rubert + дообученная модель" in desc

    # Verify calls
    mock_trans.AutoTokenizer.from_pretrained.assert_called_with("blanchefort/rubert-base-cased-sentiment")
    mock_trans.AutoModelForSequenceClassification.from_pretrained.assert_called_with("blanchefort/rubert-base-cased-sentiment")
    mock_peft.PeftModel.from_pretrained.assert_called_with(mock_model, str(adapter_dir))


def test_sentiment_configured_but_missing_fallback_to_base(monkeypatch):
    """Test sentiment when adapter path is configured but does not exist."""
    mock_settings = MagicMock()
    mock_settings.sentiment_model = "blanchefort/rubert-base-cased-sentiment"
    # Path that does not exist
    mock_settings.sentiment_adapter_path = "/non_existent/adapter"
    monkeypatch.setattr(sentiment, "get_settings", lambda: mock_settings)

    mock_torch = mock_module(monkeypatch, "torch")
    mock_trans = mock_module(monkeypatch, "transformers")
    
    mock_tokenizer = MagicMock()
    mock_model = MagicMock()
    mock_trans.AutoTokenizer.from_pretrained.return_value = mock_tokenizer
    mock_trans.AutoModelForSequenceClassification.from_pretrained.return_value = mock_model

    status, desc = sentiment.model_status()
    # It fails loading the adapter (missing path) -> falls back to base model,
    # but reports degraded because adapter was configured but not loaded.
    assert status == "degraded"
    assert "база без адаптера" in desc

    # Verify base model was loaded
    mock_trans.AutoTokenizer.from_pretrained.assert_called_with("blanchefort/rubert-base-cased-sentiment")
    mock_trans.AutoModelForSequenceClassification.from_pretrained.assert_called_with("blanchefort/rubert-base-cased-sentiment")


def test_sentiment_load_failure_fallback_to_lexicon(monkeypatch):
    """Test sentiment when everything fails to load (both adapter and base model)."""
    mock_settings = MagicMock()
    mock_settings.sentiment_model = "blanchefort/rubert-base-cased-sentiment"
    mock_settings.sentiment_adapter_path = "/non_existent/adapter"
    monkeypatch.setattr(sentiment, "get_settings", lambda: mock_settings)

    mock_trans = mock_module(monkeypatch, "transformers")
    # Force AutoTokenizer to raise an exception
    mock_trans.AutoTokenizer.from_pretrained.side_effect = RuntimeError("Load error")

    status, desc = sentiment.model_status()
    assert status == "degraded"
    assert "лексиконный фолбэк" in desc

    # Verify analyze falls back to lexicon
    # "Прибыль выросла" should be positive via lexicon
    label, score = sentiment.analyze("Прибыль выросла")
    assert label == sentiment.Sentiment.POSITIVE
    assert score > 0.0


def test_sentiment_predict_exception_fallback_to_lexicon(monkeypatch):
    """Test sentiment when prediction raises an exception -> falls back to lexicon."""
    mock_settings = MagicMock()
    mock_settings.sentiment_model = "blanchefort/rubert-base-cased-sentiment"
    mock_settings.sentiment_adapter_path = None
    monkeypatch.setattr(sentiment, "get_settings", lambda: mock_settings)

    mock_torch = mock_module(monkeypatch, "torch")
    mock_trans = mock_module(monkeypatch, "transformers")
    
    mock_tokenizer = MagicMock()
    mock_model = MagicMock()
    # Mocking model forward pass to fail
    mock_model.side_effect = RuntimeError("Prediction fail")
    
    mock_trans.AutoTokenizer.from_pretrained.return_value = mock_tokenizer
    mock_trans.AutoModelForSequenceClassification.from_pretrained.return_value = mock_model

    # Loading succeeds (status is ok)
    status, desc = sentiment.model_status()
    assert status == "ok"

    # Analyze should catch prediction error and use lexicon fallback
    label, score = sentiment.analyze("Обвал рынка и убытки")
    assert label == sentiment.Sentiment.NEGATIVE
    assert score < 0.0


# =========================================================================== #
# 3. aspect.py Fallback and Status Tests
# =========================================================================== #
def test_aspect_unconfigured(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.aspect_sentiment_adapter_path = None
    mock_settings.saliency_adapter_path = None
    monkeypatch.setattr(aspect, "get_settings", lambda: mock_settings)

    status, desc = aspect.model_status()
    assert status == "ok"
    assert "aspect-sentiment: не настроен" in desc
    assert "saliency: не настроен" in desc

    # analyze should return (None, None) since models are not configured
    sent, sal = aspect.analyze_pair("Сбербанк", "Прибыль выросла")
    assert sent is None
    assert sal is None


def test_aspect_configured_but_missing(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.aspect_sentiment_adapter_path = "/non_existent/sent"
    mock_settings.saliency_adapter_path = "/non_existent/sal"
    monkeypatch.setattr(aspect, "get_settings", lambda: mock_settings)

    status, desc = aspect.model_status()
    assert status == "degraded"
    assert "aspect-sentiment: НЕ ЗАГРУЗИЛСЯ" in desc
    assert "saliency: НЕ ЗАГРУЗИЛСЯ" in desc


def test_aspect_prediction_failure_fallback(monkeypatch, tmp_path):
    # Create valid paths so they attempt to load
    sent_path = tmp_path / "sent"
    sent_path.mkdir()
    (sent_path / "config.json").write_text("{}", encoding="utf-8")
    (sent_path / "labels.json").write_text(json.dumps({"labels": ["neg", "pos"]}), encoding="utf-8")

    sal_path = tmp_path / "sal"
    sal_path.mkdir()
    (sal_path / "config.json").write_text("{}", encoding="utf-8")
    (sal_path / "labels.json").write_text(json.dumps({"labels": ["background", "salient"]}), encoding="utf-8")

    mock_settings = MagicMock()
    mock_settings.aspect_sentiment_adapter_path = str(sent_path)
    mock_settings.saliency_adapter_path = str(sal_path)
    monkeypatch.setattr(aspect, "get_settings", lambda: mock_settings)

    mock_torch = mock_module(monkeypatch, "torch")
    mock_trans = mock_module(monkeypatch, "transformers")
    
    mock_tokenizer = MagicMock()
    # Mocking tokenizer or model to raise error on prediction
    mock_tokenizer.side_effect = RuntimeError("Predict failed")
    
    mock_trans.AutoTokenizer.from_pretrained.return_value = mock_tokenizer
    mock_trans.AutoModelForSequenceClassification.from_pretrained.return_value = MagicMock()

    # Load should succeed (since load itself doesn't call tokenizer/model forwards, wait:
    # SeqClsAdapter calls tokenizer.from_pretrained, which is fine, but predict calls tokenizer())
    status, desc = aspect.model_status()
    assert status == "ok"

    # Now run prediction which should fail and return (None, None) gracefully
    sent, sal = aspect.analyze_pair("Сбербанк", "Прибыль выросла")
    assert sent is None
    assert sal is None


# =========================================================================== #
# 4. classify.py Fallback and Status Tests
# =========================================================================== #
def test_classify_unconfigured(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.event_adapter_path = None
    monkeypatch.setattr(classify, "get_settings", lambda: mock_settings)

    status, desc = classify.model_status()
    assert status == "ok"
    assert "правила (адаптер не настроен)" in desc

    # Should use rules
    assert classify.classify_event("США ввели новые санкции") == classify.EventType.SANCTIONS


def test_classify_configured_but_missing(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.event_adapter_path = "/non_existent/event"
    monkeypatch.setattr(classify, "get_settings", lambda: mock_settings)

    status, desc = classify.model_status()
    assert status == "degraded"
    assert "адаптер настроен, но не загрузился — активны ПРАВИЛА" in desc

    # Should fall back to rules
    assert classify.classify_event("Совет директоров рекомендовал дивиденды") == classify.EventType.DIVIDENDS


# =========================================================================== #
# 5. significance.py Fallback and Status Tests
# =========================================================================== #
def test_significance_unconfigured(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.significance_adapter_path = None
    monkeypatch.setattr(significance, "get_settings", lambda: mock_settings)

    status, desc = significance.model_status()
    assert status == "ok"
    assert "формула (адаптер не настроен)" in desc

    assert significance.predict_significance("Любой текст") is None


def test_significance_configured_but_missing(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.significance_adapter_path = "/non_existent/sig"
    monkeypatch.setattr(significance, "get_settings", lambda: mock_settings)

    status, desc = significance.model_status()
    assert status == "degraded"
    assert "адаптер настроен, но не загрузился — активна ФОРМУЛА" in desc

    assert significance.predict_significance("Любой текст") is None


# =========================================================================== #
# 6. temporal.py Fallback and Status Tests
# =========================================================================== #
def test_temporal_unconfigured(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.temporal_adapter_path = None
    monkeypatch.setattr(temporal, "get_settings", lambda: mock_settings)

    status, desc = temporal.model_status()
    assert status == "ok"
    assert "temporal: не настроен" in desc

    assert temporal.classify_temporal("Любой текст") is None


def test_temporal_configured_but_missing(monkeypatch):
    mock_settings = MagicMock()
    mock_settings.temporal_adapter_path = "/non_existent/temp"
    monkeypatch.setattr(temporal, "get_settings", lambda: mock_settings)

    status, desc = temporal.model_status()
    assert status == "degraded"
    assert "temporal: НЕ ЗАГРУЗИЛСЯ" in desc

    assert temporal.classify_temporal("Любой текст") is None
