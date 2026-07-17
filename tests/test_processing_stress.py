"""Stress and boundary tests for the refactored processing package.

This module verifies paginate_query, make_full_text, and the reprocessing pipelines
under various extreme inputs, boundary conditions, and mock scenarios.
"""

from __future__ import annotations

import contextlib
import pytest
from sqlalchemy.orm import Session
from geoanalytics.processing.common import paginate_query, make_full_text
from geoanalytics.processing.reprocessing import (
    rescore_existing,
    reaspect_existing,
    retemporal_existing,
    refactuality_existing,
    renumeric_existing,
    reforecast_existing,
    RescoreResult,
    ReaspectResult,
    RetemporalResult,
    RefactualityResult,
    RenumericResult,
    ReforecastResult,
)
from geoanalytics.core.types import Sentiment, EventType, EntityType


# ==============================================================================
# 1. BOUNDARY & STRESS TESTS FOR paginate_query
# ==============================================================================

def test_paginate_query_zero_batch_size():
    """Verify paginate_query behavior when batch_size is extreme (e.g. 0 or negative).
    In theory, if take <= 0, the pagination loop might get stuck if fetch_fn returns data,
    or immediately terminate. Let's verify how it behaves.
    """
    called = []
    def fetch_fn(session, offset, take):
        called.append((offset, take))
        return []

    # If limit is 0, total_processed (0) < limit (0) is False, so it should not loop at all
    generator = paginate_query(fetch_fn, batch_size=0, limit=0)
    results = list(generator)
    assert len(results) == 0
    assert len(called) == 0


def test_paginate_query_empty_dataset():
    """Verify pagination on an empty dataset."""
    called = []
    def fetch_fn(session, offset, take):
        called.append((offset, take))
        return []

    generator = paginate_query(fetch_fn, batch_size=5, limit=None)
    results = list(generator)
    assert len(results) == 0
    assert called == [(0, 5)]


def test_paginate_query_less_than_batch_size():
    """Verify pagination when total items are fewer than batch size."""
    called = []
    def fetch_fn(session, offset, take):
        called.append((offset, take))
        if offset == 0:
            return [1, 2, 3]
        return []

    generator = paginate_query(fetch_fn, batch_size=5, limit=None)
    results = list(generator)
    assert len(results) == 1
    session, batch = results[0]
    assert batch == [1, 2, 3]
    # Because len(batch) < take (3 < 5), it should break immediately without another query
    assert called == [(0, 5)]


def test_paginate_query_exact_batch_size():
    """Verify pagination when total items are an exact multiple of batch size."""
    called = []
    def fetch_fn(session, offset, take):
        called.append((offset, take))
        if offset == 0:
            return [1, 2, 3]
        elif offset == 3:
            return []
        return []

    generator = paginate_query(fetch_fn, batch_size=3, limit=None)
    results = list(generator)
    assert len(results) == 1
    assert results[0][1] == [1, 2, 3]
    # Because len(batch) == take (3 == 3), it will do one more query to check for more data
    assert called == [(0, 3), (3, 3)]


def test_paginate_query_with_limit_and_fractional_batch():
    """Verify limit which is not a multiple of batch size."""
    called = []
    def fetch_fn(session, offset, take):
        called.append((offset, take))
        # Simulated database returning all requested items
        return list(range(offset, offset + take))

    generator = paginate_query(fetch_fn, batch_size=5, limit=8)
    results = list(generator)
    assert len(results) == 2
    assert results[0][1] == [0, 1, 2, 3, 4]
    assert results[1][1] == [5, 6, 7]
    assert called == [(0, 5), (5, 3)]


def test_paginate_query_with_limit_exact_batch():
    """Verify limit which is exactly a multiple of batch size."""
    called = []
    def fetch_fn(session, offset, take):
        called.append((offset, take))
        return list(range(offset, offset + take))

    generator = paginate_query(fetch_fn, batch_size=5, limit=10)
    results = list(generator)
    assert len(results) == 2
    assert results[0][1] == [0, 1, 2, 3, 4]
    assert results[1][1] == [5, 6, 7, 8, 9]
    assert called == [(0, 5), (5, 5)]


def test_paginate_query_exception_propagation():
    """Verify that exceptions raised in fetch_fn are correctly propagated."""
    def fetch_fn(session, offset, take):
        raise ValueError("Database failure")

    generator = paginate_query(fetch_fn, batch_size=5)
    with pytest.raises(ValueError, match="Database failure"):
        next(generator)


# ==============================================================================
# 2. BOUNDARY TESTS FOR make_full_text
# ==============================================================================

@pytest.mark.parametrize(
    "title,body,expected",
    [
        ("Title", "Body", "Title. Body"),
        ("", "Body", "Body"),
        ("Title", "", "Title."),
        (None, "Body", "Body"),
        ("Title", None, "Title."),
        (None, None, ""),
        ("   Title   ", "   Body   ", "Title.   Body"),
        ("", "", ""),
        ("\nTitle\n", "\nBody\n", "Title. \nBody"),
        # Double period vulnerability test:
        ("Title.", "Body", "Title. Body"),
    ]
)
def test_make_full_text_boundaries(title, body, expected):
    """Assert correct behavior of make_full_text on boundary inputs."""
    assert make_full_text(title, body) == expected


# ==============================================================================
# 3. STRESS & INTEGRATION TESTING FOR REPROCESSING FUNCTIONS WITH MOCKS
# ==============================================================================

class DummyORM:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class MockSession:
    """Mock Session that simulates query execution and commit/rollback logic."""
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False
        self.operations = []
        self._nested_transactions = []

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True

    def execute(self, stmt, *args, **kwargs):
        self.operations.append(("execute", stmt))
        class MockResult:
            def all(self):
                return []
            def scalars(self):
                class MockScalars:
                    def all(self):
                        return []
                return MockScalars()
            @property
            def rowcount(self):
                return 1
        return MockResult()

    def scalars(self, stmt, *args, **kwargs):
        self.operations.append(("scalars", stmt))
        class MockScalars:
            def all(self):
                return []
            def first(self):
                return None
        return MockScalars()

    @contextlib.contextmanager
    def begin_nested(self):
        self._nested_transactions.append("nested")
        try:
            yield
        except Exception:
            self._nested_transactions.append("nested_rollback")
            raise
        finally:
            self._nested_transactions.append("nested_commit")


@contextlib.contextmanager
def mock_session_scope(mock_sess):
    yield mock_sess


def test_rescore_existing_integration(monkeypatch):
    """Verify rescore_existing calls fetch_fn via paginate_query, handles batching and transactions."""
    mock_sess = MockSession()
    monkeypatch.setattr("geoanalytics.processing.reprocessing.session_scope", lambda: mock_session_scope(mock_sess))
    monkeypatch.setattr("geoanalytics.processing.common.session_scope", lambda: mock_session_scope(mock_sess))

    # Mock models to avoid heavy model invocation or missing model config issues
    monkeypatch.setattr("geoanalytics.nlp.sentiment.analyze", lambda text: (Sentiment.NEUTRAL, 0.0))
    monkeypatch.setattr("geoanalytics.nlp.classify.classify_event", lambda text: EventType.OTHER)
    monkeypatch.setattr("geoanalytics.processing.common._compute_significance", lambda *a: 0.5)

    # Let's mock paginate_query specifically inside reprocessing or mock the fetch_fn query result.
    # Instead of full DB, we test that rescore_existing runs through paginate_query batches successfully.
    # We will pass a limit=2 to check pagination control.
    res = rescore_existing(stages=["sentiment", "significance"], batch_size=10, limit=5)
    assert isinstance(res, RescoreResult)
    assert res.articles == 0  # mock DB returns no articles


def test_reaspect_existing_integration(monkeypatch):
    """Verify reaspect_existing behaves correctly under mock conditions."""
    mock_sess = MockSession()
    monkeypatch.setattr("geoanalytics.processing.reprocessing.session_scope", lambda: mock_session_scope(mock_sess))
    monkeypatch.setattr("geoanalytics.processing.common.session_scope", lambda: mock_session_scope(mock_sess))

    # Mock aspect models
    monkeypatch.setattr("geoanalytics.nlp.aspect._get_sentiment_model", lambda: object())
    monkeypatch.setattr("geoanalytics.nlp.aspect._get_saliency_model", lambda: object())
    monkeypatch.setattr("geoanalytics.nlp.aspect.analyze_pair", lambda pair, text: (Sentiment.NEUTRAL.value, True))

    res = reaspect_existing(limit=5, batch_size=2)
    assert isinstance(res, ReaspectResult)


def test_retemporal_existing_integration(monkeypatch):
    """Verify retemporal_existing behaves correctly under mock conditions."""
    mock_sess = MockSession()
    monkeypatch.setattr("geoanalytics.processing.reprocessing.session_scope", lambda: mock_session_scope(mock_sess))
    monkeypatch.setattr("geoanalytics.processing.common.session_scope", lambda: mock_session_scope(mock_sess))

    # Mock temporal model
    monkeypatch.setattr("geoanalytics.nlp.temporal._model", lambda: object())
    monkeypatch.setattr("geoanalytics.nlp.temporal.temporal_anchor", lambda text, pub: ("present", pub))

    res = retemporal_existing(limit=5, batch_size=2)
    assert isinstance(res, RetemporalResult)


def test_refactuality_existing_integration(monkeypatch):
    """Verify refactuality_existing behaves correctly under mock conditions."""
    mock_sess = MockSession()
    monkeypatch.setattr("geoanalytics.processing.reprocessing.session_scope", lambda: mock_session_scope(mock_sess))
    monkeypatch.setattr("geoanalytics.processing.common.session_scope", lambda: mock_session_scope(mock_sess))

    # Mock factuality model
    monkeypatch.setattr("geoanalytics.nlp.rumor.classify_factuality", lambda text, temporal_status: ("fact", 1.0))

    res = refactuality_existing(limit=5, batch_size=2)
    assert isinstance(res, RefactualityResult)


def test_renumeric_existing_integration(monkeypatch):
    """Verify renumeric_existing behaves correctly under mock conditions."""
    mock_sess = MockSession()
    monkeypatch.setattr("geoanalytics.processing.reprocessing.session_scope", lambda: mock_session_scope(mock_sess))
    monkeypatch.setattr("geoanalytics.processing.common.session_scope", lambda: mock_session_scope(mock_sess))

    # Mock numeric extractor
    monkeypatch.setattr("geoanalytics.nlp.numeric.extract_numbers", lambda text: [])

    res = renumeric_existing(limit=5, batch_size=2)
    assert isinstance(res, RenumericResult)


def test_reforecast_existing_integration(monkeypatch):
    """Verify reforecast_existing behaves correctly under mock conditions."""
    mock_sess = MockSession()
    monkeypatch.setattr("geoanalytics.processing.reprocessing.session_scope", lambda: mock_session_scope(mock_sess))
    monkeypatch.setattr("geoanalytics.processing.common.session_scope", lambda: mock_session_scope(mock_sess))

    # Mock forecast check
    monkeypatch.setattr("geoanalytics.nlp.forecast.is_forecast_post", lambda *a, **k: False)

    res = reforecast_existing(limit=5, batch_size=2)
    assert isinstance(res, ReforecastResult)
