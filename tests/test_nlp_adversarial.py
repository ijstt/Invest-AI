"""Adversarial and edge case tests for NLP helpers: is_full_model, load_seqcls_adapter, to_float, MULT."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from geoanalytics.nlp import MULT, to_float
from geoanalytics.nlp._seqcls import is_full_model, load_seqcls_adapter, SeqClsAdapter


# =========================================================================== #
# 1. to_float and MULT tests
# =========================================================================== #

def test_to_float_valid():
    assert to_float("123.45") == 123.45
    assert to_float("123,45") == 123.45
    assert to_float("123 456,78") == 123456.78
    assert to_float("\xa0123\u2009456,78\xa0") == 123456.78  # Unicode spaces
    assert to_float("1.2e3") == 1200.0
    assert to_float("1.2e-3") == 0.0012
    assert to_float("inf") == float("inf")
    assert to_float("-inf") == float("-inf")


def test_to_float_invalid():
    with pytest.raises(ValueError):
        to_float("")
    with pytest.raises(ValueError):
        to_float("   ")
    with pytest.raises(ValueError):
        to_float("abc")
    with pytest.raises(ValueError):
        to_float("12.3.4")
    with pytest.raises(ValueError):
        to_float("12,,4")
    with pytest.raises(TypeError):
        to_float(None)  # type: ignore
    with pytest.raises(TypeError):
        to_float(123.45)  # type: ignore


def test_mult_values():
    assert MULT["тыс"] == 1e3
    assert MULT["млн"] == 1e6
    assert MULT["млрд"] == 1e9
    assert MULT["трлн"] == 1e12
    assert len(MULT) == 4
    # Ensure keys are lowercase
    for k in MULT:
        assert k == k.lower()


# =========================================================================== #
# 2. is_full_model tests
# =========================================================================== #

def test_is_full_model_edge_cases(tmp_path):
    # Case: Empty/non-existent directory
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    assert is_full_model(empty_dir) is False

    # Case: Only adapter_config.json
    peft_only = tmp_path / "peft_only"
    peft_only.mkdir()
    (peft_only / "adapter_config.json").write_text("{}", encoding="utf-8")
    assert is_full_model(peft_only) is False

    # Case: Both files exist (this is an interesting edge case: LoRA adapter wins or is not full model)
    both_exist = tmp_path / "both"
    both_exist.mkdir()
    (both_exist / "config.json").write_text("{}", encoding="utf-8")
    (both_exist / "adapter_config.json").write_text("{}", encoding="utf-8")
    assert is_full_model(both_exist) is False

    # Case: Path is a file instead of a directory
    file_path = tmp_path / "file.json"
    file_path.write_text("{}", encoding="utf-8")
    assert is_full_model(file_path) is False

    # Case: Path is empty string
    # Path("") is relative path to current directory. It is a directory, so it won't raise NotADirectoryError.
    # It will check if "config.json" exists in the current directory.
    # We just want to ensure it doesn't crash.
    assert isinstance(is_full_model(""), bool)


def test_is_full_model_type_exceptions():
    with pytest.raises(TypeError):
        is_full_model(None)  # type: ignore
    with pytest.raises(TypeError):
        is_full_model(12345)  # type: ignore


# =========================================================================== #
# 3. load_seqcls_adapter tests
# =========================================================================== #

def test_load_seqcls_adapter_null_bytes():
    mock_logger = MagicMock()
    # Path containing null byte returns False inside Path.exists() under Python 3.12
    res = load_seqcls_adapter("/some/path\x00with/null", mock_logger, name="test_null")
    assert res is None
    mock_logger.error.assert_called_once()
    assert "test_null_adapter_missing_FALLBACK" in mock_logger.error.call_args[0][0]


def test_load_seqcls_adapter_type_error():
    mock_logger = MagicMock()
    # Invalid type path raises TypeError inside Path()
    res = load_seqcls_adapter(12345, mock_logger, name="test_type")  # type: ignore
    assert res is None
    mock_logger.error.assert_called_once()
    assert "test_type_model_failed_FALLBACK" in mock_logger.error.call_args[0][0]


def test_load_seqcls_adapter_invalid_json(tmp_path):
    mock_logger = MagicMock()
    model_dir = tmp_path / "bad_json"
    model_dir.mkdir()
    (model_dir / "labels.json").write_text("{corrupt_json:", encoding="utf-8")

    res = load_seqcls_adapter(str(model_dir), mock_logger, name="test_bad_json")
    assert res is None
    mock_logger.error.assert_called_once()
    assert "test_bad_json_model_failed_FALLBACK" in mock_logger.error.call_args[0][0]


def test_load_seqcls_adapter_missing_labels_key(tmp_path):
    mock_logger = MagicMock()
    model_dir = tmp_path / "missing_labels_key"
    model_dir.mkdir()
    (model_dir / "labels.json").write_text('{"base": "some-base"}', encoding="utf-8")

    res = load_seqcls_adapter(str(model_dir), mock_logger, name="test_missing_key")
    assert res is None
    mock_logger.error.assert_called_once()
    assert "test_missing_key_model_failed_FALLBACK" in mock_logger.error.call_args[0][0]


def test_load_seqcls_adapter_non_list_labels(tmp_path):
    mock_logger = MagicMock()
    model_dir = tmp_path / "non_list_labels"
    model_dir.mkdir()
    (model_dir / "labels.json").write_text('{"labels": "not-a-list", "base": "some-base"}', encoding="utf-8")

    # In Python/transformers, it will raise TypeError or AttributeError when building labels dictionary.
    res = load_seqcls_adapter(str(model_dir), mock_logger, name="test_non_list_labels")
    assert res is None
    mock_logger.error.assert_called_once()
    assert "test_non_list_labels_model_failed_FALLBACK" in mock_logger.error.call_args[0][0]


def test_load_seqcls_adapter_os_error_permission_denied(tmp_path, monkeypatch):
    mock_logger = MagicMock()
    model_dir = tmp_path / "perm_denied"
    model_dir.mkdir()
    (model_dir / "labels.json").write_text('{"labels": ["a", "b"]}', encoding="utf-8")

    # Mock Path.exists or reading files to raise PermissionError
    def mock_exists(self):
        raise PermissionError("Permission denied")

    monkeypatch.setattr(Path, "exists", mock_exists)

    res = load_seqcls_adapter(str(model_dir), mock_logger, name="test_perm")
    assert res is None
    mock_logger.error.assert_called_once()
    assert "test_perm_model_failed_FALLBACK" in mock_logger.error.call_args[0][0]
