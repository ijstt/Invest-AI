"""Тесты G5: индекс новостного давления."""

from __future__ import annotations

from geoanalytics.analytics.pressure import news_pressure


class _FakeRow:
    def __init__(self, val):
        self._val = val

    def scalar(self):
        return self._val


class _FakeSession:
    def __init__(self, sig_sum: float):
        self._sig_sum = sig_sum

    def execute(self, _stmt):
        return _FakeRow(self._sig_sum)


def test_pressure_normalises_by_window():
    """Давление = сумма sig / window."""
    session = _FakeSession(sig_sum=3.5)
    result = news_pressure(session, asset_id=1, window=7)
    assert abs(result - 3.5 / 7) < 1e-9


def test_pressure_zero_when_no_articles():
    """Нет новостей → давление 0."""
    session = _FakeSession(sig_sum=0.0)
    result = news_pressure(session, asset_id=1, window=7)
    assert result == 0.0


def test_pressure_window_1():
    """Окно 1 день: давление = сумма sig."""
    session = _FakeSession(sig_sum=0.85)
    result = news_pressure(session, asset_id=1, window=1)
    assert abs(result - 0.85) < 1e-9


def test_pressure_window_30():
    """Большое окно уменьшает значение."""
    session = _FakeSession(sig_sum=3.5)
    r7 = news_pressure(session, asset_id=1, window=7)
    r30 = news_pressure(session, asset_id=1, window=30)
    assert r7 > r30
