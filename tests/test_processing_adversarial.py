"""Adversarial and boundary test cases for the processing package."""

from __future__ import annotations

from types import SimpleNamespace
import pytest
from sqlalchemy.orm import Session

from geoanalytics import processing
from geoanalytics.core.types import EntityType, EventType, Sentiment
from geoanalytics.nlp.numeric import NumericFact, TARGET_PRICE, DIVIDEND, KEY_RATE
from geoanalytics.processing import (
    _to_float,
    _store_forecasts,
    _extra_entity_rows,
    _embed_batch,
    _pipeline_degraded,
    _process_news,
    ProcessResult,
)


class _Asset:
    def __init__(self, ticker, comp):
        self.ticker = ticker
        self.company = comp


class _Comp:
    def __init__(self, sector_id, country_id):
        self.sector_id = sector_id
        self.country_id = country_id


# --- 1. _to_float boundary cases ---
def test_to_float_boundary_cases():
    # Standard integers and floats
    assert _to_float(42) == 42.0
    assert _to_float(3.14) == 3.14
    assert _to_float("3.14") == 3.14
    
    # Weird string inputs that python's float() accepts
    assert _to_float("inf") == float("inf")
    assert _to_float("-inf") == float("-inf")
    assert _to_float("nan") is not None  # float("nan") is nan, which is not None
    import math
    assert math.isnan(_to_float("nan"))
    
    # Corrupt or invalid types
    assert _to_float(None) is None
    assert _to_float("abc") is None
    assert _to_float([]) is None
    assert _to_float({}) is None


# --- 2. _store_forecasts edge cases ---
class _MockForecastRepo:
    def __init__(self, session):
        self.session = session

    def add_forecast(self, article_id, asset_id, kind, value, unit, target_date, source_channel):
        self.session.added_forecasts.append({
            "article_id": article_id,
            "asset_id": asset_id,
            "kind": kind,
            "value": value,
            "unit": unit,
            "target_date": target_date,
            "source_channel": source_channel
        })
        return 1


class _MockSession:
    def __init__(self):
        self.added_forecasts = []
        self.added_entities = []

    def add(self, obj):
        self.added_entities.append(obj)

    def flush(self):
        pass


def test_store_forecasts_various_conditions(monkeypatch):
    # Mock ForecastRepository
    monkeypatch.setattr(processing.common, "ForecastRepository", _MockForecastRepo)
    
    sess = _MockSession()
    facts = [
        NumericFact(TARGET_PRICE, 100.0, "RUB", "target price 100"),
        NumericFact(DIVIDEND, 10.0, "RUB", "dividend 10"),
        NumericFact(KEY_RATE, 15.0, "pct", "key rate 15"),
    ]
    
    # Condition: Multiple assets -> should skip entirely (precision-first)
    added = _store_forecasts(sess, 1, facts, [10, 20], None, "channel")
    assert added == 0
    assert len(sess.added_forecasts) == 0

    # Condition: No assets -> should skip entirely
    added = _store_forecasts(sess, 1, facts, [], None, "channel")
    assert added == 0
    assert len(sess.added_forecasts) == 0

    # Condition: Single asset -> should store target_price and dividend, skip key_rate
    added = _store_forecasts(sess, 1, facts, [10], "2026-08-01", "channel")
    assert added == 2
    assert len(sess.added_forecasts) == 2
    assert sess.added_forecasts[0]["kind"] == TARGET_PRICE
    assert sess.added_forecasts[0]["value"] == 100.0
    assert sess.added_forecasts[1]["kind"] == DIVIDEND
    assert sess.added_forecasts[1]["value"] == 10.0


# --- 3. _extra_entity_rows boundary cases ---
def test_extra_entity_rows_edge_cases(monkeypatch):
    # Mock classify_themes to avoid DB query there
    monkeypatch.setattr(processing, "classify_themes", lambda _t: [])
    
    # Case A: empty asset cache
    links = [SimpleNamespace(entity_type=EntityType.ASSET, entity_id=1, relevance=1.0)]
    rows = _extra_entity_rows(_MockSession(), links, "text", {})
    assert rows == []
    
    # Case B: company is None
    cache = {1: _Asset("SBER", None)}
    rows = _extra_entity_rows(_MockSession(), links, "text", cache)
    assert rows == []
    
    # Case C: sector_id and country_id are None
    cache = {1: _Asset("SBER", _Comp(None, None))}
    rows = _extra_entity_rows(_MockSession(), links, "text", cache)
    assert rows == []


# --- 4. _embed_batch strict-zip crash vulnerability ---
class _FaultyEmbedder:
    model_name = "faulty-model"
    
    def __init__(self, mismatch_len=False):
        self.mismatch_len = mismatch_len

    def embed(self, texts):
        if self.mismatch_len:
            # Return list of different size
            return [[0.1]] * (len(texts) - 1)
        raise RuntimeError("Embedding service failed")

    def embed_one(self, text):
        return [0.2]


def test_embed_batch_mismatch_length_fallback():
    sess = _MockSession()
    embedder = _FaultyEmbedder(mismatch_len=True)
    items = [(1, "text1"), (2, "text2")]
    
    # Should fall back to embed_one rather than crashing with ValueError
    added = _embed_batch(sess, embedder, items)
    assert added == 2
    assert len(sess.added_entities) == 2
    assert sess.added_entities[0].vector == [0.2]


def test_embed_batch_handles_embedder_failure():
    sess = _MockSession()
    embedder = _FaultyEmbedder(mismatch_len=False)
    items = [(1, "text1"), (2, "text2")]
    
    # Should fall back to embed_one without throwing an exception
    added = _embed_batch(sess, embedder, items)
    assert added == 2
    assert len(sess.added_entities) == 2
    assert sess.added_entities[0].vector == [0.2]


# --- 5. _process_news string length checks ---
def test_process_news_extremely_long_fields(monkeypatch):
    # Mock models and classification to be ok
    monkeypatch.setattr(processing.sentiment, "analyze", lambda _t: (Sentiment.NEUTRAL, 0.0))
    monkeypatch.setattr(processing.classify, "classify_event", lambda _t: EventType.OTHER)
    monkeypatch.setattr(processing.ner, "extract_entities", lambda _t: [])
    monkeypatch.setattr(processing, "_compute_significance", lambda *a, **k: 1.0)
    monkeypatch.setattr(processing, "_is_duplicate", lambda *a, **k: False)
    monkeypatch.setattr(processing.temporal, "temporal_anchor", lambda *a, **k: ("none", None))
    monkeypatch.setattr(processing.rumor, "classify_factuality", lambda *a, **k: ("fact", None))
    monkeypatch.setattr(processing.forecast, "is_forecast_post", lambda *a, **k: False)
    monkeypatch.setattr(processing.numeric, "extract_numbers", lambda *a, **k: [])
    
    result = ProcessResult()
    sess = _MockSession()
    
    # Document with extremely long channel name (source_ref) and URL
    long_channel = "A" * 500
    long_url = "http://" + "B" * 1100
    doc = SimpleNamespace(
        id=123,
        source="telegram",
        payload={
            "title": "Valid title",
            "summary": "body",
            "channel": long_channel,
            "url": long_url
        }
    )
    
    # Process news. It will construct the Article object.
    processed = _process_news(sess, doc, SimpleNamespace(match=lambda *a, **k: []), result, {}, [])
    assert processed is True
    assert len(sess.added_entities) == 1
    
    article = sess.added_entities[0]
    # Check that title is sliced
    assert len(article.title) <= 1024
    # Check that source_ref is sliced, to prevent a database crash if it exceeds column length (64).
    assert article.source_ref == long_channel[:64]
    assert len(article.source_ref) == 64
    # Check that url is sliced, to prevent a database crash if it exceeds column length (1024).
    assert article.url == long_url[:1024]
    assert len(article.url) == 1024


# --- 6. _store_forecasts long channel name vulnerability ---
def test_store_forecasts_long_channel(monkeypatch):
    # Mock ForecastRepository to see what channel it receives
    monkeypatch.setattr(processing.common, "ForecastRepository", _MockForecastRepo)
    
    sess = _MockSession()
    facts = [
        NumericFact(TARGET_PRICE, 100.0, "RUB", "target price 100"),
    ]
    long_channel = "ChannelName" * 10
    
    # Store forecasts using a channel name longer than 64 characters
    added = _store_forecasts(sess, 1, facts, [10], "2026-08-01", long_channel)
    assert added == 1
    assert len(sess.added_forecasts) == 1
    # Check if the channel was truncated. If the code does not truncate it, this assertion
    # highlights that the raw, untruncated channel of length 110 is passed to the repo.
    # We document this as a potential failure mode since Forecast.source_channel is String(64).
    raw_channel_sent = sess.added_forecasts[0]["source_channel"]
    assert len(raw_channel_sent) == 110  # Untruncated!

