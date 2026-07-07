"""Тесты G6: тональный моментум (EWMA сентимента)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

from geoanalytics.analytics.sentiment_trend import latest_momentum, sentiment_momentum


def _make_row(day_str: str, avg: float):
    """Имитация строки SQL-результата с .day и .avg_sent."""
    d = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=UTC)
    return SimpleNamespace(day=d, avg_sent=avg)


class _FakeExecResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, _stmt):
        return _FakeExecResult(self._rows)


def test_momentum_empty_when_no_data():
    """Нет данных → пустой список."""
    session = _FakeSession(rows=[])
    result = sentiment_momentum(session, asset_id=1)
    assert result == []


def test_momentum_returns_values_for_small_dataset():
    """Меньше span точек → fallback на raw-значения (не EMA)."""
    rows = [_make_row("2026-06-01", 0.3), _make_row("2026-06-02", 0.5)]
    session = _FakeSession(rows)
    result = sentiment_momentum(session, asset_id=1, days=60, span=14)
    assert len(result) == 2
    for d, v in result:
        assert isinstance(d, date)
        assert isinstance(v, float)


def test_momentum_ema_smooths_volatility():
    """EMA должна быть между экстремумами."""
    rows = [
        _make_row(f"2026-05-{i:02d}", 0.5 if i % 2 == 0 else -0.5)
        for i in range(1, 21)
    ]
    session = _FakeSession(rows)
    result = sentiment_momentum(session, asset_id=1, days=60, span=5)
    assert result  # не пустой
    for _, v in result:
        assert -1.0 <= v <= 1.0, f"EMA вышел за диапазон: {v}"


def test_latest_momentum_scalar():
    """latest_momentum возвращает скаляр или None."""
    rows = [_make_row(f"2026-05-{i:02d}", 0.3) for i in range(1, 20)]
    session = _FakeSession(rows)
    val = latest_momentum(session, asset_id=1, span=5)
    assert val is not None
    assert isinstance(val, float)


def test_latest_momentum_none_when_no_data():
    """Нет данных → None."""
    session = _FakeSession(rows=[])
    assert latest_momentum(session, asset_id=1) is None
