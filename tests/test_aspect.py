"""Тесты F1/F2 (Волна 2): кодировка пар, graceful-фолбэки, интеграция в processing."""

from __future__ import annotations

from dataclasses import dataclass

from geoanalytics.core.types import EntityType
from geoanalytics.nlp import aspect


def test_encode_pair_prefixes_aspect_and_truncates():
    out = aspect.encode_pair("Сбербанк (SBER)", "т" * 2000)
    assert out.startswith("Сбербанк (SBER): ")
    assert len(out) <= len("Сбербанк (SBER): ") + 1000


def test_aspect_name_with_and_without_company():
    assert aspect.aspect_name("SBER", "Сбербанк") == "Сбербанк (SBER)"
    assert aspect.aspect_name("XXXX", None) == "XXXX"
    assert aspect.aspect_name("XXXX", "XXXX") == "XXXX"


def test_analyze_pair_none_without_models(monkeypatch):
    """Модели не настроены → (None, None): конвейер падает на копию тональности статьи."""
    monkeypatch.setattr(aspect, "_get_sentiment_model", lambda: None)
    monkeypatch.setattr(aspect, "_get_saliency_model", lambda: None)
    assert aspect.analyze_pair("X (Y)", "текст") == (None, None)


def test_analyze_pair_with_stub_models(monkeypatch):
    class _Stub:
        def __init__(self, label):
            self._label = label

        def predict_label(self, text):
            assert text.startswith("Сбербанк (SBER): ")
            return self._label

    monkeypatch.setattr(aspect, "_get_sentiment_model", lambda: _Stub("negative"))
    monkeypatch.setattr(aspect, "_get_saliency_model", lambda: _Stub("background"))
    sent, salient = aspect.analyze_pair("Сбербанк (SBER)", "новость")
    assert sent == "negative"
    assert salient is False


def test_model_status_ok_when_not_configured(monkeypatch):
    class _S:
        aspect_sentiment_adapter_path = None
        saliency_adapter_path = None

    monkeypatch.setattr(aspect, "get_settings", lambda: _S())
    monkeypatch.setattr(aspect, "_get_sentiment_model", lambda: None)
    monkeypatch.setattr(aspect, "_get_saliency_model", lambda: None)
    status, detail = aspect.model_status()
    assert status == "ok"
    assert "не настроен" in detail


def test_model_status_degraded_when_configured_but_failed(monkeypatch):
    class _S:
        aspect_sentiment_adapter_path = "data/adapters/no-such"
        saliency_adapter_path = None

    monkeypatch.setattr(aspect, "get_settings", lambda: _S())
    monkeypatch.setattr(aspect, "_get_sentiment_model", lambda: None)
    monkeypatch.setattr(aspect, "_get_saliency_model", lambda: None)
    status, detail = aspect.model_status()
    assert status == "degraded"
    assert "НЕ ЗАГРУЗИЛСЯ" in detail


# --------------------------------------------------------------------------- #
# processing._aspect_links: per-link тональность с фолбэком.
# --------------------------------------------------------------------------- #
@dataclass
class _Link:
    entity_type: EntityType
    entity_id: int
    relevance: float = 1.0


@dataclass
class _Asset:
    ticker: str
    name: str
    company = None


def test_aspect_links_fallback_copies_article_label(monkeypatch):
    from geoanalytics import processing

    monkeypatch.setattr(processing.aspect, "analyze_pair", lambda a, t: (None, None))
    links = [_Link(EntityType.ASSET, 1), _Link(EntityType.SECTOR, 5)]
    cache = {1: _Asset("SBER", "Сбербанк")}
    out = processing._aspect_links(links, "текст", cache, "negative")
    assert out == {("asset", 1): ("negative", None)}  # sector не трогаем


def test_aspect_links_uses_model_labels(monkeypatch):
    from geoanalytics import processing

    monkeypatch.setattr(
        processing.aspect, "analyze_pair", lambda a, t: ("positive", False)
    )
    links = [_Link(EntityType.ASSET, 1)]
    cache = {1: _Asset("VTBR", "Банк ВТБ")}
    out = processing._aspect_links(links, "текст", cache, "negative")
    assert out == {("asset", 1): ("positive", False)}
