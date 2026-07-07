"""Тесты оценки влияния события на актив (чистая функция assess_impact)."""

from __future__ import annotations

from geoanalytics.context.events import assess_impact
from geoanalytics.core.types import EventType, Sentiment


def test_sanctions_negative_strong():
    direction, magnitude = assess_impact(
        EventType.SANCTIONS.value, Sentiment.NEGATIVE.value, -0.9, 1.0
    )
    assert direction == "negative"
    assert magnitude > 0.8  # санкции с сильным негативом → высокая сила


def test_dividends_positive():
    direction, magnitude = assess_impact(
        EventType.DIVIDENDS.value, Sentiment.POSITIVE.value, 0.6, 1.0
    )
    assert direction == "positive"
    assert 0 < magnitude <= 1.0


def test_neutral_direction():
    direction, _ = assess_impact(
        EventType.MACRO.value, Sentiment.NEUTRAL.value, 0.0, 1.0
    )
    assert direction == "neutral"


def test_magnitude_clamped():
    _, magnitude = assess_impact(EventType.SANCTIONS.value, Sentiment.NEGATIVE.value, 5.0, 1.0)
    assert magnitude <= 1.0


def test_low_relevance_reduces_magnitude():
    _, high = assess_impact(EventType.EARNINGS.value, Sentiment.POSITIVE.value, 0.8, 1.0)
    _, low = assess_impact(EventType.EARNINGS.value, Sentiment.POSITIVE.value, 0.8, 0.3)
    assert low < high


def test_missing_score_uses_floor():
    # без score сила всё равно ненулевая (есть пол 0.2)
    _, magnitude = assess_impact(EventType.SANCTIONS.value, Sentiment.NEGATIVE.value, None, 1.0)
    assert magnitude > 0


def test_ensure_event_resyncs_type_after_reclassify():
    """Б8: переклассифицированная статья досинхронизирует тип существующего события."""
    from unittest.mock import MagicMock

    from geoanalytics.context.events import _ensure_event

    existing = MagicMock()
    existing.event_type = "macro"
    session = MagicMock()
    session.scalars.return_value.first.return_value = existing

    art = MagicMock()
    art.id = 1
    art.event_type = "sanctions"
    ev = _ensure_event(session, art)
    assert ev is existing
    assert existing.event_type == "sanctions"   # тип обновился
    session.add.assert_not_called()             # новое событие не создавалось


def test_reconcile_impacts_prunes_and_rebuilds(monkeypatch):
    """Сверка импактов: удаляет собранные устаревшие id и перестраивает по событиям.

    Настоящий фикс мины устаревших EventImpact (model-data-errors #1)."""
    from types import SimpleNamespace

    from sqlalchemy.sql.dml import Delete

    from geoanalytics.context import events

    stale_ids = [11, 22, 33]
    pairs = [(SimpleNamespace(id=1), SimpleNamespace(id=1)),
             (SimpleNamespace(id=2), SimpleNamespace(id=2))]

    class _Sess:
        def __init__(self):
            self.deleted_in = None

        def scalars(self, _q):           # stale_q → id устаревших
            return iter(stale_ids)

        def execute(self, q):
            if isinstance(q, Delete):    # DELETE по собранным id
                self.deleted_in = stale_ids
                return SimpleNamespace(rowcount=len(stale_ids))
            return SimpleNamespace(all=lambda: pairs)  # ev_q → (event, article)

    built = []
    monkeypatch.setattr(events, "_build_impacts",
                        lambda _s, art, ev: built.append((art.id, ev.id)) or 1)

    r = events.reconcile_impacts(_Sess())
    assert r == {"pruned": 3, "rebuilt": 2}
    assert built == [(1, 1), (2, 2)]   # перестроено по каждому событию


def test_reconcile_impacts_noop_when_clean(monkeypatch):
    """Нет устаревших и нет событий → пустой результат без падений."""
    from types import SimpleNamespace

    from geoanalytics.context import events

    class _Sess:
        def scalars(self, _q):
            return iter([])

        def execute(self, _q):
            return SimpleNamespace(all=lambda: [])

    monkeypatch.setattr(events, "_build_impacts", lambda *a: 0)
    assert events.reconcile_impacts(_Sess()) == {"pruned": 0, "rebuilt": 0}


def test_embedder_status_degraded_on_dim_mismatch(monkeypatch):
    """Б16: размерность модели ≠ схемы БД → health degraded, а не тихий лог."""
    from unittest.mock import MagicMock

    from geoanalytics.nlp import embeddings

    emb = MagicMock()
    emb.dim = 384
    emb.model_name = "tiny-model"
    monkeypatch.setattr(embeddings, "get_embedder", lambda: emb)
    status, detail = embeddings.model_status()
    assert status == "degraded"
    assert "384" in detail and str(embeddings.EMBEDDING_DIM) in detail

    emb.dim = embeddings.EMBEDDING_DIM
    assert embeddings.model_status()[0] == "ok"
