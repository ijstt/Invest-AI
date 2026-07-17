"""Adversarial challenger tests for NLP helpers (is_full_model, load_seqcls_adapter, to_float, MULT)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from geoanalytics.nlp import _seqcls
from geoanalytics.nlp.numeric import MULT, to_float


# =========================================================================== #
# 1. is_full_model Challenger Tests
# =========================================================================== #

def test_is_full_model_trailing_slash(tmp_path):
    """Test is_full_model with trailing slashes in path."""
    model_dir = tmp_path / "my_model"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")
    
    # Path as string with trailing slash
    path_str = str(model_dir) + "/"
    assert _seqcls.is_full_model(path_str) is True


def test_is_full_model_null_byte():
    """Test is_full_model with null byte in path string.
    In Python 3.12 on this environment, Path.exists() for a path with a null byte
    simply returns False, so is_full_model returns False."""
    assert _seqcls.is_full_model("invalid\0path") is False


def test_is_full_model_empty_string():
    """Test is_full_model with empty string."""
    # An empty string Path("") evaluates to ".", which doesn't have config.json
    assert _seqcls.is_full_model("") is False


# =========================================================================== #
# 2. load_seqcls_adapter Challenger Tests
# =========================================================================== #

def test_load_seqcls_adapter_labels_is_directory(tmp_path):
    """Test load_seqcls_adapter when labels.json is actually a directory.
    This should raise an OSError (like IsADirectoryError), be caught, and return None."""
    logger = MagicMock()
    model_dir = tmp_path / "model_with_labels_dir"
    model_dir.mkdir()
    
    # Create labels.json as a directory
    (model_dir / "labels.json").mkdir()
    
    assert _seqcls.load_seqcls_adapter(str(model_dir), logger, name="test") is None
    assert logger.error.called


def test_load_seqcls_adapter_null_byte_path():
    """Test load_seqcls_adapter with a path containing a null byte.
    Since Path.exists() returns False, it should log it as missing and return None."""
    logger = MagicMock()
    
    assert _seqcls.load_seqcls_adapter("invalid\0path", logger, name="test") is None
    assert logger.error.called
    args, kwargs = logger.error.call_args
    assert kwargs.get("path") == "invalid\0path"


def test_load_seqcls_adapter_empty_path():
    """Test load_seqcls_adapter with empty string path.
    Since exists() will return True if run in a dir with labels.json or False otherwise,
    it should behave gracefully."""
    logger = MagicMock()
    # If empty path, Path("").exists() is True (current directory).
    # But it won't have labels.json, so it should fail loading and return None.
    assert _seqcls.load_seqcls_adapter("", logger, name="test") is None


# =========================================================================== #
# 3. to_float Challenger Tests
# =========================================================================== #

def test_to_float_ideographic_space():
    """Test that ideographic space (\u3000) is stripped and parsed correctly."""
    assert to_float("1\u3000200,5") == 1200.5


def test_to_float_zero_width_space():
    r"""Test that zero-width space (\u200b) is handled.
    Note: \u200b is NOT matched by standard \s in Python's regex without special flags,
    so we check if it raises ValueError or is handled."""
    # Since \u200b is not matched by \s, it will remain, causing float() to raise ValueError.
    with pytest.raises(ValueError):
        to_float("1\u200b200,5")


def test_to_float_signs_and_exponents():
    """Test signs and exponents parsing."""
    assert to_float("-123,45") == -123.45
    assert to_float("+123,45") == 123.45
    assert to_float("1,5e+3") == 1500.0


def test_to_float_overflow():
    """Test float overflow behavior in Python (returns inf)."""
    assert to_float("1e309") == float("inf")
    assert to_float("-1e309") == float("-inf")


# =========================================================================== #
# 4. MULT Challenger Tests
# =========================================================================== #

def test_mult_whitespace_keys():
    """Verify keys with leading/trailing whitespaces are not in MULT."""
    with pytest.raises(KeyError):
        _ = MULT[" тыс"]
    with pytest.raises(KeyError):
        _ = MULT["тыс "]
    with pytest.raises(KeyError):
        _ = MULT["млн\n"]
