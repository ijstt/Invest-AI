"""Тесты чистой NLP-логики без БД и тяжёлых моделей."""

from __future__ import annotations

import pytest

from geoanalytics.core.types import EventType, Sentiment
from geoanalytics.nlp import classify as _classify
from geoanalytics.nlp.classify import classify_event
from geoanalytics.nlp.sentiment import _lexicon_sentiment
from geoanalytics.nlp.text import clean_text


@pytest.fixture(autouse=True)
def _force_event_rules(monkeypatch):
    """Тесты правил классификации не должны зависеть от наличия дообученной модели
    событий в окружении (.env GEO_EVENT_ADAPTER_PATH) - форсим путь правил."""
    monkeypatch.setattr(_classify, "_get_classifier", lambda: None)


def test_classify_sanctions():
    assert classify_event("США ввели новые санкции против банка") == EventType.SANCTIONS


def test_classify_dividends():
    assert classify_event("Совет директоров рекомендовал дивиденды") == EventType.DIVIDENDS


def test_classify_earnings():
    assert classify_event("Компания отчиталась: чистая прибыль выросла") == EventType.EARNINGS


def test_classify_other():
    assert classify_event("Сегодня солнечная погода в столице") == EventType.OTHER


def test_classify_noise():
    # Спорт/происшествия/культура → NOISE (нерелевантный рынку шум), не OTHER.
    assert classify_event("Мирра Андреева выиграла теннисный турнир") == EventType.NOISE
    assert classify_event("ДТП на трассе: погибли три человека") == EventType.NOISE
    # но финансовое содержание побеждает шум
    assert classify_event("Санкции затронули спортивные клубы") == EventType.SANCTIONS


def test_lexicon_positive():
    label, score = _lexicon_sentiment("Прибыль выросла, рекордный рост и успех")
    assert label == Sentiment.POSITIVE
    assert score > 0


def test_lexicon_negative():
    label, score = _lexicon_sentiment("Обвал рынка, убыток и санкции, кризис")
    assert label == Sentiment.NEGATIVE
    assert score < 0


def test_lexicon_neutral_empty():
    assert _lexicon_sentiment("") == (Sentiment.NEUTRAL, 0.0)


def test_clean_text_strips_html():
    assert clean_text("<p>Привет   <b>мир</b></p>") == "Привет мир"


def test_clean_text_none():
    assert clean_text(None) == ""


def test_classify_falls_back_to_rules_without_adapter():
    """Без GEO_EVENT_ADAPTER_PATH классификатор использует правила (модель не грузится)."""
    from geoanalytics.nlp.classify import _get_classifier

    assert _get_classifier() is None  # адаптер не настроен в тестовой среде
    assert classify_event("Совет директоров рекомендовал дивиденды") == EventType.DIVIDENDS


def test_event_label_mapping():
    from geoanalytics.nlp.classify import _label_to_event

    assert _label_to_event("sanctions") == EventType.SANCTIONS
    assert _label_to_event("nonsense") == EventType.OTHER


def test_significance_bucket_and_predict_fallback(monkeypatch):
    from geoanalytics.nlp import significance

    assert significance.significance_bucket(0.9) == "high"
    assert significance.significance_bucket(0.5) == "medium"
    assert significance.significance_bucket(0.1) == "low"
    # Когда модель недоступна → формульный фолбэк (None из модели). Форсим отсутствие
    # модели, чтобы тест не зависел от .env (GEO_SIGNIFICANCE_ADAPTER_PATH в проде).
    monkeypatch.setattr(significance, "_get_model", lambda: None)
    assert significance.predict_significance("любой текст") is None
