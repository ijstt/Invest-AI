"""Тесты дедупа near-duplicate (C1): нормализация текста/хеша и проверка дубля в окне.

Чистые функции (`normalized_text`/`normalized_hash`) и `processing._is_duplicate` со
стаб-сессией — без реальной БД (в проекте нет БД-фикстур, см. test_processing)."""

from __future__ import annotations

import pytest

from geoanalytics import processing
from geoanalytics.storage.repositories import (
    PortfolioRepository,
    normalized_hash,
    normalized_text,
)


def test_upsert_position_rejects_nonpositive_quantity():
    """Б-аудит #3: количество ≤ 0 отвергается ДО записи (нет молчаливых нулевых/коротких)."""
    repo = PortfolioRepository(session=None)  # гард срабатывает раньше обращения к сессии
    for bad in (0, -3.5):
        with pytest.raises(ValueError, match="положительным"):
            repo.upsert_position("SBER", bad)


def test_normalized_text_collapses_cosmetic_diffs():
    # HTML-сущности, регистр, повторные пробелы и пунктуация схлопываются.
    assert normalized_text("В Геленджике атаки&nbsp;БПЛА") == normalized_text(
        "в геленджике атаки  бпла"
    )
    assert normalized_text("Газпром: рекордная   прибыль!!!") == "газпром рекордная прибыль"
    # Идемпотентность: повторная нормализация ничего не меняет.
    once = normalized_text("Сбербанк — отчёт, 2026")
    assert normalized_text(once) == once


def test_normalized_hash_equal_for_near_duplicates():
    a = normalized_hash("В Геленджике объявили опасность атаки&nbsp;БПЛА")
    b = normalized_hash("В Геленджике объявили опасность атаки БПЛА")
    assert a == b  # косметическое отличие (&nbsp;) не даёт разных хешей
    # Разный смысл — разный хеш.
    assert normalized_hash("Санкции против банка") != normalized_hash("Дивиденды банка")
    # Пустой/None — стабильно.
    assert normalized_hash("") == normalized_hash(None)


def test_is_duplicate_uses_window_and_hash():
    class _Sess:
        def __init__(self, found):
            self._found = found
            self.calls = 0

        def scalar(self, _stmt):
            self.calls += 1
            return self._found

    # Сессия нашла статью с тем же хешем в окне → дубль.
    s_found = _Sess(found=42)
    assert processing._is_duplicate(s_found, "abc", 72) is True
    assert s_found.calls == 1
    # Ничего не нашла → не дубль.
    s_none = _Sess(found=None)
    assert processing._is_duplicate(s_none, "abc", 72) is False
