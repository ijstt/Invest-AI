"""Тесты TTL-ретеншна (M6): чистая формула срока хранения."""

from __future__ import annotations

from geoanalytics.storage.retention import retention_ttl_days


def test_ttl_endpoints():
    assert retention_ttl_days(0.0, 7, 365) == 7      # шум — минимальный срок
    assert retention_ttl_days(1.0, 7, 365) == 365    # максимум значимости — максимальный срок


def test_ttl_monotonic():
    low = retention_ttl_days(0.2, 7, 365)
    high = retention_ttl_days(0.8, 7, 365)
    assert 7 < low < high < 365


def test_ttl_clamps_out_of_range():
    assert retention_ttl_days(-0.5, 7, 365) == 7
    assert retention_ttl_days(1.5, 7, 365) == 365


def test_ttl_midpoint():
    # значимость 0.5 → ровно середина диапазона
    assert retention_ttl_days(0.5, 10, 100) == 55
