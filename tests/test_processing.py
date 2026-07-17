"""Тесты переразметки уже сохранённых статей (`rescore_existing` / `_rescore_article`).

DB-раннер `rescore_existing` не тестируется целиком (в проекте нет БД-фикстур), но
проверяются: валидация стадий (срабатывает до обращения к БД) и чистая логика
`_rescore_article` (в dry-run не трогает сессию — её можно вызвать со стабом статьи).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from geoanalytics import processing
from geoanalytics.core.types import EntityType, EventType, Sentiment
from geoanalytics.nlp import significance as nlp_significance
from geoanalytics.processing import (
    ProcessResult,
    RescoreResult,
    _embed_batch,
    _extra_entity_rows,
    _pipeline_degraded,
    _process_news,
    _rescore_article,
    rescore_existing,
)


class _Art:
    """Минимальный стаб ORM-статьи."""

    def __init__(self, **kw):
        self.id = 1
        self.title = "Заголовок"
        self.text = "Тело новости"
        self.sentiment = "neutral"
        self.sentiment_score = 0.0
        self.event_type = "other"
        self.significance = 0.3
        self.__dict__.update(kw)


class _Sess:
    """Стаб сессии: записывает выполненные statements (для проверки апдейта связей)."""

    def __init__(self):
        self.executed = []

    def execute(self, stmt):
        self.executed.append(stmt)
        return None


def _patch_models(monkeypatch, *, label=Sentiment.NEGATIVE, score=-0.8, event=EventType.SANCTIONS):
    monkeypatch.setattr(processing.sentiment, "analyze", lambda _t: (label, score))
    monkeypatch.setattr(processing.classify, "classify_event", lambda _t: event)
    # Форсим формульную значимость (фолбэк), чтобы тест не зависел от наличия
    # дообученной модели значимости в окружении (.env GEO_SIGNIFICANCE_ADAPTER_PATH).
    monkeypatch.setattr(processing, "predict_significance", lambda _t: None)


# --- Валидация стадий (до БД) ------------------------------------------------ #

def test_rescore_rejects_unknown_stage():
    with pytest.raises(ValueError, match="Неизвестные стадии"):
        rescore_existing(["sentiment", "bogus"])


def test_rescore_rejects_empty_stages():
    with pytest.raises(ValueError, match="ни одной стадии"):
        rescore_existing([])


# --- Логика одной статьи (dry-run, без сессии) ------------------------------- #

def test_rescore_article_dry_run_counts_change_without_mutating(monkeypatch):
    _patch_models(monkeypatch)
    art = _Art(sentiment="neutral", sentiment_score=0.0, significance=0.3)
    result = RescoreResult(dry_run=True)

    _rescore_article(None, art, [], stages=("sentiment",), do_significance=True,
                     result=result, dry_run=True)

    # Эффект посчитан...
    assert result.articles == 1
    assert result.sentiment_changed == 1
    assert result.sentiment_before["neutral"] == 1
    assert result.sentiment_after["negative"] == 1
    assert result.significance_changed == 1
    # ...но статья НЕ изменена (dry-run).
    assert art.sentiment == "neutral"
    assert art.significance == 0.3


def test_rescore_article_applies_and_syncs_links_when_not_dry_run(monkeypatch):
    _patch_models(monkeypatch, label=Sentiment.NEGATIVE, score=-0.8)
    art = _Art(sentiment="neutral", sentiment_score=0.0, significance=0.3)
    sess = _Sess()
    result = RescoreResult()

    _rescore_article(sess, art, [], stages=("sentiment",), do_significance=True,
                     result=result, dry_run=False)

    # Поля статьи обновлены.
    assert art.sentiment == "negative"
    assert art.sentiment_score == -0.8
    # Значимость пересчитана по формуле: 0.5*type(other=0.1) + 0.3*|−0.8| + 0.2*links(0) = 0.29.
    assert art.significance == pytest.approx(0.29, abs=1e-3)
    # Денормализованная копия тональности в связях синхронизирована ровно одним апдейтом.
    assert len(sess.executed) == 1


def test_rescore_article_events_stage_updates_type(monkeypatch):
    _patch_models(monkeypatch, event=EventType.SANCTIONS)
    art = _Art(event_type="other")
    result = RescoreResult()

    _rescore_article(_Sess(), art, [], stages=("events",), do_significance=True,
                     result=result, dry_run=False)

    assert art.event_type == EventType.SANCTIONS.value
    assert result.event_changed == 1


def test_rescore_article_no_link_sync_for_significance_only(monkeypatch):
    """Стадия только significance не трогает связи (тональность не менялась)."""
    _patch_models(monkeypatch)
    art = _Art(sentiment="negative", sentiment_score=-0.5)
    sess = _Sess()
    result = RescoreResult()

    _rescore_article(sess, art, [0.9], stages=("significance",), do_significance=True,
                     result=result, dry_run=False)

    assert result.sentiment_changed == 0
    assert sess.executed == []  # связи не апдейтили


# --- Б4: транзиентный скип не финализируется при деградации моделей ---------- #

def test_pipeline_degraded_all_ok(monkeypatch):
    monkeypatch.setattr(processing.sentiment, "model_status", lambda: ("ok", "x"))
    monkeypatch.setattr(processing.classify, "model_status", lambda: ("ok", "x"))
    monkeypatch.setattr(nlp_significance, "model_status", lambda: ("ok", "формула"))
    assert _pipeline_degraded() is False


def test_pipeline_degraded_when_one_model_fallback(monkeypatch):
    monkeypatch.setattr(processing.sentiment, "model_status", lambda: ("ok", "x"))
    monkeypatch.setattr(processing.classify, "model_status", lambda: ("ok", "x"))
    monkeypatch.setattr(nlp_significance, "model_status", lambda: ("degraded", "формула Б1"))
    assert _pipeline_degraded() is True


def _patch_noise(monkeypatch):
    """Заставляет _process_news пойти по шумовому скипу (низкая значимость, нет связей)."""
    monkeypatch.setattr(processing.sentiment, "analyze", lambda _t: (Sentiment.NEUTRAL, 0.0))
    monkeypatch.setattr(processing.classify, "classify_event", lambda _t: EventType.OTHER)
    monkeypatch.setattr(processing.ner, "extract_entities", lambda _t: [])
    monkeypatch.setattr(processing, "_compute_significance", lambda *a, **k: 0.0)
    monkeypatch.setattr(processing, "_is_duplicate", lambda *a, **k: False)


def _noise_doc():
    return SimpleNamespace(id=1, source="rss",
                           payload={"title": "Спортивный матч завершился",
                                    "summary": "краткий обзор", "url": "u"})


_NULL_INDEX = SimpleNamespace(match=lambda *_a, **_k: [])


def test_noise_skip_finalized_when_models_ok(monkeypatch):
    _patch_noise(monkeypatch)
    result = ProcessResult()
    processed = _process_news(None, _noise_doc(), _NULL_INDEX, result, {}, [], degraded=False)
    assert processed is True            # модель жива → шум финализируется
    assert result.skipped == 1 and result.deferred == 0


def test_noise_skip_deferred_when_models_degraded(monkeypatch):
    _patch_noise(monkeypatch)
    result = ProcessResult()
    processed = _process_news(None, _noise_doc(), _NULL_INDEX, result, {}, [], degraded=True)
    assert processed is False           # фолбэк → не финализируем, пересмотрим позже
    assert result.deferred == 1 and result.skipped == 0


def test_no_title_finalized_even_when_degraded():
    result = ProcessResult()
    doc = SimpleNamespace(id=1, source="rss", payload={"title": "", "summary": ""})
    processed = _process_news(None, doc, _NULL_INDEX, result, {}, [], degraded=True)
    assert processed is True            # терминальный скип финализируется всегда
    assert result.skipped == 1


# --- D1: батч-эмбеддинги (_embed_batch) -------------------------------------- #

class _AddSess:
    """Стаб сессии: копит добавленные объекты (Embedding-строки)."""

    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def get(self, *_a, **_k):  # noqa: D401 — должна НЕ вызываться в горячем цикле
        raise AssertionError("session.get не должен вызываться (N+1 убран)")


class _Embedder:
    model_name = "test-model"

    def __init__(self, fail_batch=False):
        self.fail_batch = fail_batch
        self.batch_calls = 0
        self.one_calls = 0

    def embed(self, texts):
        self.batch_calls += 1
        if self.fail_batch:
            raise RuntimeError("batch boom")
        return [[float(len(t))] for t in texts]

    def embed_one(self, text):
        self.one_calls += 1
        return [float(len(text))]


def test_embed_batch_single_call_for_whole_batch():
    sess, emb = _AddSess(), _Embedder()
    added = _embed_batch(sess, emb, [(1, "aa"), (2, "bbbb")])
    assert added == 2
    assert emb.batch_calls == 1 and emb.one_calls == 0  # один батч-вызов на весь список
    assert {e.article_id for e in sess.added} == {1, 2}
    assert {tuple(e.vector) for e in sess.added} == {(2.0,), (4.0,)}


def test_embed_batch_falls_back_to_per_article_on_batch_failure():
    sess, emb = _AddSess(), _Embedder(fail_batch=True)
    added = _embed_batch(sess, emb, [(1, "aa"), (2, "bbbb")])
    assert added == 2  # выход не потерян — откатились на embed_one
    assert emb.batch_calls == 1 and emb.one_calls == 2


def test_embed_batch_noop_without_embedder_or_items():
    assert _embed_batch(_AddSess(), None, [(1, "x")]) == 0
    assert _embed_batch(_AddSess(), _Embedder(), []) == 0


# --- D2: derived-связи через asset_cache (без N+1) --------------------------- #

class _Link:
    def __init__(self, entity_id, relevance=1.0):
        self.entity_type = EntityType.ASSET
        self.entity_id = entity_id
        self.mention = "SBER"
        self.relevance = relevance


class _Comp:
    def __init__(self, sector_id, country_id):
        self.sector_id = sector_id
        self.country_id = country_id


class _Asset:
    def __init__(self, ticker, comp):
        self.ticker = ticker
        self.company = comp


def test_extra_entity_rows_uses_cache_no_session_get(monkeypatch):
    monkeypatch.setattr(processing, "classify_themes", lambda _t: [])
    cache = {7: _Asset("SBER", _Comp(sector_id=3, country_id=5))}
    rows = _extra_entity_rows(_AddSess(), [_Link(7, relevance=1.0)], "текст", cache)
    # derived сектор/страна ×0.8 от relevance; session.get не дёргался (иначе AssertionError).
    assert ("sector", 3, "SBER", 0.8) in rows
    assert ("country", 5, "SBER", 0.8) in rows


def test_extra_entity_rows_skips_asset_missing_from_cache(monkeypatch):
    monkeypatch.setattr(processing, "classify_themes", lambda _t: [])
    rows = _extra_entity_rows(_AddSess(), [_Link(99)], "текст", {})  # нет в кэше → пропуск
    assert rows == []


# --- F10 forecast-путь ------------------------------------------------------- #
from geoanalytics.nlp.numeric import DIVIDEND, KEY_RATE, TARGET_PRICE, NumericFact  # noqa: E402
from geoanalytics.processing import _store_forecasts  # noqa: E402


class _FcSess:
    """Стаб сессии для _store_forecasts: возвращает rowcount, копит вставки."""

    def __init__(self):
        self.calls = []

    def execute(self, stmt):
        self.calls.append(stmt)
        return SimpleNamespace(fetchall=lambda: [(1,)])  # одна вставленная строка


def test_store_forecasts_single_asset_filters_kinds():
    facts = [
        NumericFact(TARGET_PRICE, 350.0, "RUB", "целевая цена 350 руб"),
        NumericFact(DIVIDEND, 25.0, "RUB", "дивиденд 25 руб"),
        NumericFact(KEY_RATE, 14.0, "pct", "ставка 14%"),  # макро, не прогноз по активу
    ]
    sess = _FcSess()
    added = _store_forecasts(sess, 7, facts, [1], None, "SberInvestments")
    # target_price + dividend к единственному активу = 2; key_rate отфильтрован.
    assert added == 2
    assert len(sess.calls) == 2


def test_store_forecasts_skips_multi_asset_post():
    # Дайджест с несколькими тикерами — не привязываем число ко всем (precision-first).
    sess = _FcSess()
    facts = [NumericFact(DIVIDEND, 70.0, "RUB", "x")]
    assert _store_forecasts(sess, 7, facts, [1, 2], None, "SberInvestments") == 0
    assert sess.calls == []


def test_store_forecasts_noop_without_assets():
    sess = _FcSess()
    facts = [NumericFact(TARGET_PRICE, 350.0, "RUB", "x")]
    assert _store_forecasts(sess, 7, facts, [], None, "SberInvestments") == 0
    assert sess.calls == []


def test_paginate_query_standard(monkeypatch):
    import contextlib
    from geoanalytics.processing.common import paginate_query

    sessions = []

    class MockSession:
        def __init__(self):
            self.rolled_back = False
            self.committed = False
            self.closed = False
            sessions.append(self)

        def rollback(self):
            self.rolled_back = True

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    @contextlib.contextmanager
    def mock_session_scope():
        sess = MockSession()
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    monkeypatch.setattr("geoanalytics.processing.common.session_scope", mock_session_scope)

    # Mock fetch function
    data = list(range(10))
    def fetch_fn(session, offset, take):
        return data[offset:offset+take]

    # Run generator to completion
    results = list(paginate_query(fetch_fn, batch_size=3))
    
    assert len(results) == 4  # 0-2, 3-5, 6-8, 9
    assert len(sessions) == 4
    for s in sessions:
        assert s.committed is True
        assert s.rolled_back is False
        assert s.closed is True


def test_paginate_query_generator_exit(monkeypatch):
    import contextlib
    from geoanalytics.processing.common import paginate_query

    sessions = []

    class MockSession:
        def __init__(self):
            self.rolled_back = False
            self.committed = False
            self.closed = False
            sessions.append(self)

        def rollback(self):
            self.rolled_back = True

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    @contextlib.contextmanager
    def mock_session_scope():
        sess = MockSession()
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    monkeypatch.setattr("geoanalytics.processing.common.session_scope", mock_session_scope)

    data = list(range(10))
    def fetch_fn(session, offset, take):
        return data[offset:offset+take]

    gen = paginate_query(fetch_fn, batch_size=3)
    sess1, batch1 = next(gen)
    # Break early from generator (simulates exception/break in caller's loop)
    # Closing the generator will raise GeneratorExit at the yield statement.
    gen.close()

    assert len(sessions) == 1
    assert sessions[0].rolled_back is True


def test_paginate_query_custom_exception(monkeypatch):
    import contextlib
    from geoanalytics.processing.common import paginate_query

    sessions = []

    class MockSession:
        def __init__(self):
            self.rolled_back = False
            self.committed = False
            self.closed = False
            sessions.append(self)

        def rollback(self):
            self.rolled_back = True

        def commit(self):
            self.committed = True

        def close(self):
            self.closed = True

    @contextlib.contextmanager
    def mock_session_scope():
        sess = MockSession()
        try:
            yield sess
            sess.commit()
        except Exception:
            sess.rollback()
            raise
        finally:
            sess.close()

    monkeypatch.setattr("geoanalytics.processing.common.session_scope", mock_session_scope)

    data = list(range(10))
    def fetch_fn(session, offset, take):
        return data[offset:offset+take]

    gen = paginate_query(fetch_fn, batch_size=3)
    sess1, batch1 = next(gen)

    class CustomException(Exception):
        pass

    with pytest.raises(CustomException):
        for sess, batch in gen:
            raise CustomException("Oops")

    gen.close()

    # The exception happened during yield inside generator, so it raises BaseException
    # (since Exception inherits from BaseException) inside paginate_query.
    # That block calls session.rollback() and reraises.
    assert len(sessions) == 2  # first session for next(), second session for the loop
    assert sessions[0].committed is True
    assert sessions[1].rolled_back is True


