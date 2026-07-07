"""Тесты health-check фолбэков (I4/Б1): сборка статусов без тяжёлых моделей и БД."""

from __future__ import annotations

from geoanalytics import health
from geoanalytics.health import (
    STATUS_DEGRADED,
    STATUS_OK,
    ComponentHealth,
    check,
    degraded,
)


def _patch_all_ok(monkeypatch):
    """Все компоненты «ок» без загрузки моделей/БД."""
    from geoanalytics.nlp import (
        aspect,
        classify,
        embeddings,
        ner,
        sentiment,
        significance,
        temporal,
    )

    monkeypatch.setattr(health, "_db_status", lambda: (STATUS_OK, "ok"))
    monkeypatch.setattr(health, "_llm_status", lambda: (STATUS_OK, "ok"))
    monkeypatch.setattr(health, "_orphan_assets_status", lambda: (STATUS_OK, "ok"))
    monkeypatch.setattr(health, "_sig_gates_status", lambda: (STATUS_OK, "ok"))
    for mod in (aspect, classify, embeddings, ner, sentiment, significance, temporal):
        monkeypatch.setattr(mod, "model_status", lambda: (STATUS_OK, "ok"))


def test_check_all_ok(monkeypatch):
    _patch_all_ok(monkeypatch)
    components = check()
    assert {c.name for c in components} == {
        "db", "sentiment", "events", "significance", "sig_gates", "aspect", "temporal",
        "embedder", "ner", "llm", "assets",
    }
    assert degraded(components) == []


def test_check_reports_degraded_component(monkeypatch):
    from geoanalytics.nlp import significance

    _patch_all_ok(monkeypatch)
    monkeypatch.setattr(
        significance, "model_status",
        lambda: (STATUS_DEGRADED, "активна ФОРМУЛА (Б1)"),
    )
    bad = degraded(check())
    assert [c.name for c in bad] == ["significance"]
    assert "ФОРМУЛА" in bad[0].detail


def test_check_survives_crashing_probe(monkeypatch):
    """Падение самой проверки — это degraded-статус, а не исключение (Б15-страховка)."""
    from geoanalytics.nlp import sentiment

    _patch_all_ok(monkeypatch)

    def _boom():
        raise RuntimeError("model exploded")

    monkeypatch.setattr(sentiment, "model_status", _boom)
    components = check()
    bad = {c.name: c for c in degraded(components)}
    assert "sentiment" in bad
    assert "model exploded" in bad["sentiment"].detail


def test_degraded_filters_only_not_ok():
    comps = [
        ComponentHealth("a", STATUS_OK, ""),
        ComponentHealth("b", STATUS_DEGRADED, "x"),
    ]
    assert [c.name for c in degraded(comps)] == ["b"]


def test_significance_model_status_degraded_when_configured_but_missing(monkeypatch):
    """Б1: адаптер настроен, но не загрузился → статус degraded (а не тихая формула)."""
    from geoanalytics.nlp import significance

    monkeypatch.setattr(significance, "_get_model", lambda: None)

    class _S:
        significance_adapter_path = "data/adapters/no-such-dir"

    monkeypatch.setattr(significance, "get_settings", lambda: _S())
    status, detail = significance.model_status()
    assert status == STATUS_DEGRADED
    assert "ФОРМУЛА" in detail


def test_significance_model_status_ok_without_adapter(monkeypatch):
    from geoanalytics.nlp import significance

    monkeypatch.setattr(significance, "_get_model", lambda: None)

    class _S:
        significance_adapter_path = None

    monkeypatch.setattr(significance, "get_settings", lambda: _S())
    status, _ = significance.model_status()
    assert status == STATUS_OK


def test_orphan_assets_status_degraded(monkeypatch):
    """Б9: акции без company_id видны в health (вне секторных скоупов)."""
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    session = MagicMock()
    session.scalars.return_value = ["XXXX", "YYYY"]
    session.scalar.return_value = 2

    @contextmanager
    def _scope():
        yield session

    import geoanalytics.storage.db as db_mod
    monkeypatch.setattr(db_mod, "session_scope", _scope)
    status, detail = health._orphan_assets_status()
    assert status == STATUS_DEGRADED
    assert "XXXX" in detail and "2" in detail


def test_orphan_assets_status_ok(monkeypatch):
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    session = MagicMock()
    session.scalars.return_value = []

    @contextmanager
    def _scope():
        yield session

    import geoanalytics.storage.db as db_mod
    monkeypatch.setattr(db_mod, "session_scope", _scope)
    status, _ = health._orphan_assets_status()
    assert status == STATUS_OK
