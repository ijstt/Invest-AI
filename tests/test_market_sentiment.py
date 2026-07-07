"""Тесты B1: персистентный индекс настроения (агрегаты, EWMA-перенос, дивергенция)."""

from __future__ import annotations

from datetime import date

import geoanalytics.analytics.market_sentiment as ms
from geoanalytics.analytics.market_sentiment import (
    SentAgg,
    _stats,
    is_divergent,
    record_day,
)


def test_stats_mean_breadth_dispersion_pressure():
    rows = [(0.8, 0.9), (0.4, 0.5), (-0.6, 0.7), (0.0, 0.2)]
    mean, breadth, dispersion, n, pressure = _stats(rows)
    assert n == 4
    assert abs(mean - 0.15) < 1e-9
    # 2 позитива (>0.05), 1 негатив (<-0.05), 0.0 — нейтрально → (2-1)/4 = 0.25
    assert abs(breadth - 0.25) < 1e-9
    assert dispersion > 0
    assert abs(pressure - 2.3) < 1e-9


def test_is_divergent_cases():
    # Цена растёт, настроение падает (оба заметны) → дивергенция.
    assert is_divergent(2.0, -0.3) is True
    # Согласованы (оба вверх) → нет.
    assert is_divergent(2.0, 0.3) is False
    # Слабые движения отсекаются порогами.
    assert is_divergent(0.5, -0.3) is False
    assert is_divergent(2.0, -0.05) is False
    assert is_divergent(None, -0.3) is False


class _StubSession:
    """Сессия-заглушка: глотает execute/flush, копит add(); _prev_ewma не зовут (есть кэш)."""

    def __init__(self):
        self.added = []

    def execute(self, _stmt):
        return None

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


def test_record_day_ewma_carry(monkeypatch):
    """EWMA сглаживает суточное среднее с переносом через ewma_cache (бэкфилл по дням)."""
    means = iter([0.6, -0.4])   # два дня подряд для рынка

    def fake_aggregate(_session, _day, _m=means):
        return [SentAgg("market", None, None, next(_m), 0.2, 0.3, 10, 5.0)]

    monkeypatch.setattr(ms, "aggregate_day", fake_aggregate)
    session = _StubSession()
    cache: dict = {}

    record_day(session, date(2026, 6, 1), span=14, ewma_cache=cache)
    first = session.added[-1].sent_ewma
    assert first == 0.6                      # первый день — EWMA = среднее

    record_day(session, date(2026, 6, 2), span=14, ewma_cache=cache)
    second = session.added[-1].sent_ewma
    a = 2 / 15
    assert abs(second - (a * -0.4 + (1 - a) * 0.6)) < 1e-9   # перенос предыдущего
    assert -0.4 < second < 0.6               # сглажено между средним и прошлым EWMA
