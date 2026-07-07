"""Тесты межпроцессного замка LLM-генерации (core/locks): busy / захват / fail-open."""

from __future__ import annotations

import contextlib

import pytest

from geoanalytics.core import locks


class _Session:
    def __init__(self, got: bool) -> None:
        self._got = got

    def execute(self, *_a, **_k):
        got = self._got

        class _R:
            def scalar(self_inner):
                return got

        return _R()


def _scope_with(got: bool):
    @contextlib.contextmanager
    def _cm():
        yield _Session(got)

    return _cm


def test_lock_acquired_runs_block(monkeypatch):
    monkeypatch.setattr("geoanalytics.storage.db.session_scope", _scope_with(True))
    ran = []
    with locks.llm_generation_lock():
        ran.append(1)
    assert ran == [1]


def test_lock_busy_raises(monkeypatch):
    """Замок занят другим запросом (pg_try_advisory_xact_lock=false) → LLMBusy."""
    monkeypatch.setattr("geoanalytics.storage.db.session_scope", _scope_with(False))
    with pytest.raises(locks.LLMBusy):
        with locks.llm_generation_lock():
            raise AssertionError("блок не должен выполниться при занятом замке")


def test_lock_fail_open_on_db_error(monkeypatch):
    """Сбой БД при взятии замка → fail-open: блок выполняется (ответы не падают из-за БД)."""
    @contextlib.contextmanager
    def _boom():
        raise RuntimeError("db down")
        yield  # noqa: unreachable — нужно для генератора-контекстменеджера

    monkeypatch.setattr("geoanalytics.storage.db.session_scope", _boom)
    ran = []
    with locks.llm_generation_lock():
        ran.append(1)
    assert ran == [1]
