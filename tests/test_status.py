"""Тесты статус-фида пайплайна (Волна 6в): свежесть по следам в БД, с мок-сессией."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from geoanalytics.query import status


def _session(values):
    """Мок-сессия: `scalar()` отдаёт значения по порядку вызовов pipeline_status."""
    seq = list(values)
    return SimpleNamespace(scalar=lambda stmt: seq.pop(0))


def test_pipeline_status_fresh():
    now = datetime.now(UTC)
    # порядок: last_ingest, docs_24h, unprocessed, last_alert, last_article
    s = _session([now - timedelta(minutes=10), 42, 3,
                  now - timedelta(hours=1), now - timedelta(hours=2)])
    st = status.pipeline_status(s)
    assert st["fresh"] is True
    assert st["docs_24h"] == 42 and st["unprocessed"] == 3
    assert "мин назад" in st["last_ingest"]
    assert "ч назад" in st["last_alert"]


def test_pipeline_status_stale_when_ingest_old():
    now = datetime.now(UTC)
    s = _session([now - timedelta(hours=5), 0, 0, None, None])
    st = status.pipeline_status(s)
    assert st["fresh"] is False
    assert st["last_alert"] is None and st["last_article"] is None


def test_pipeline_status_no_data():
    st = status.pipeline_status(_session([None, 0, 0, None, None]))
    assert st["fresh"] is False and st["last_ingest"] is None and st["docs_24h"] == 0


def test_ago_buckets():
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    assert status._ago(now - timedelta(seconds=30), now) == "только что"
    assert status._ago(now - timedelta(minutes=15), now) == "15 мин назад"
    assert status._ago(now - timedelta(hours=3), now) == "3 ч назад"
    assert status._ago(now - timedelta(days=2), now) == "2 дн назад"
    assert status._ago(None, now) is None
