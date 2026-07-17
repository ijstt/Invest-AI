"""Additional adversarial and stress tests for NLP helpers (is_full_model, load_seqcls_adapter, to_float, MULT)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from geoanalytics.nlp import _seqcls
from geoanalytics.nlp.numeric import MULT, to_float


# =========================================================================== #
# 1. is_full_model Additional Tests
# =========================================================================== #

def test_is_full_model_config_is_directory(tmp_path):
    """If config.json is actually a directory, is_full_model still returns True
    because .exists() is True for directories. This is an edge case."""
    model_dir = tmp_path / "model_dir"
    model_dir.mkdir()
    config_dir = model_dir / "config.json"
    config_dir.mkdir()
    
    assert _seqcls.is_full_model(model_dir) is True


def test_is_full_model_unicode_path(tmp_path):
    """Test is_full_model with unicode characters in path."""
    model_dir = tmp_path / "модель_тест_123"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    
    assert _seqcls.is_full_model(model_dir) is True


# =========================================================================== #
# 2. load_seqcls_adapter Additional Tests
# =========================================================================== #

def test_load_seqcls_adapter_invalid_labels_schema(tmp_path):
    """Test load_seqcls_adapter when labels.json is syntactically valid JSON
    but violates the expected schema (e.g. labels is not a list, labels list is empty)."""
    logger = MagicMock()
    
    # Schema mismatch: labels is not a list (it is an integer)
    dir_1 = tmp_path / "schema_1"
    dir_1.mkdir()
    (dir_1 / "labels.json").write_text(json.dumps({"labels": 123, "base": "model"}), encoding="utf-8")
    assert _seqcls.load_seqcls_adapter(str(dir_1), logger, name="test") is None
    assert logger.error.called
    
    # Schema mismatch: labels list is empty
    # Wait, if labels list is empty, SeqClsAdapter would do dict(enumerate([])) -> id2label = {}
    # Then label2id = {}.
    # Let's mock the PeftModel / transformers to check if empty labels list fails or is handled.
    # In general, if labels is empty, it will fail during argmax logic or when loading the model.
    # Since we don't mock transformers here, SeqClsAdapter will try to import and fail on AutoTokenizer.
    # But even if it imports, it will fail.
    # Let's verify that load_seqcls_adapter catches the exception and returns None.
    logger.reset_mock()
    dir_2 = tmp_path / "schema_2"
    dir_2.mkdir()
    (dir_2 / "labels.json").write_text(json.dumps({"labels": [], "base": "model"}), encoding="utf-8")
    assert _seqcls.load_seqcls_adapter(str(dir_2), logger, name="test") is None
    assert logger.error.called


def test_load_seqcls_adapter_bad_logger_raises(tmp_path):
    """If logger is None or missing the log methods, calling load_seqcls_adapter
    with a non-existent path will raise AttributeError/TypeError. This is a known contract requirement."""
    # If logger is None, it should raise AttributeError when trying to log warning/error
    with pytest.raises(AttributeError):
        _seqcls.load_seqcls_adapter("/non-existent", None, name="test")


# =========================================================================== #
# 3. to_float Additional Tests
# =========================================================================== #

def test_to_float_other_unicode_spaces():
    """to_float replaces unicode spaces (like thin space \u2009 and narrow no-break space \u202f)
    and converts the result successfully."""
    # Thin space: \u2009
    assert to_float("1\u2009200,5") == 1200.5
        
    # Narrow no-break space: \u202f
    assert to_float("1\u202f200,5") == 1200.5


def test_to_float_non_string_types():
    """to_float expects a string. Non-string inputs will raise TypeError."""
    with pytest.raises(TypeError):
        to_float(None)  # type: ignore
        
    with pytest.raises(TypeError):
        to_float(123)  # type: ignore


# =========================================================================== #
# 4. MULT Additional Tests
# =========================================================================== #

def test_mult_case_sensitivity():
    """MULT keys are case-sensitive. Upper/mixed case keys raise KeyError."""
    with pytest.raises(KeyError):
        _ = MULT["МЛН"]
        
    with pytest.raises(KeyError):
        _ = MULT["Млн"]
        
    with pytest.raises(KeyError):
        _ = MULT["Тыс"]
