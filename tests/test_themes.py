"""Тесты классификатора макро-тем (чистая функция по ключевым словам)."""

from __future__ import annotations

from geoanalytics.nlp.themes import classify_themes


def test_classify_sanctions():
    assert "Санкции" in classify_themes("США ввели новые санкции против банков РФ")


def test_classify_key_rate():
    assert "Ключевая ставка" in classify_themes("ЦБ повысил ключевую ставку до 16%")


def test_classify_fx_ruble_weakening():
    assert "Курс валют" in classify_themes("Рубль подешевел к доллару на торгах")


def test_classify_multiple_themes():
    themes = classify_themes("Из-за санкций инфляция ускорилась, ЦБ поднял ставку")
    assert {"Санкции", "Инфляция", "Ключевая ставка"} <= set(themes)


def test_classify_noise_returns_empty():
    assert classify_themes("Теннисистка выиграла турнир Большого шлема") == []
    assert classify_themes("") == []
