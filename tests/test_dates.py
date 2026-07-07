"""Тесты парсинга дат из источников."""

from __future__ import annotations

from datetime import UTC

from geoanalytics.core.dates import parse_cbr_date, parse_moex_systime, parse_rss_date


def test_parse_cbr_date():
    dt = parse_cbr_date("03.06.2026")
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2026, 6, 3)
    assert dt.tzinfo == UTC


def test_parse_cbr_date_invalid():
    assert parse_cbr_date("не дата") is None
    assert parse_cbr_date(None) is None


def test_parse_moex_systime_to_day():
    dt = parse_moex_systime("2026-06-03 19:05:00")
    assert dt is not None
    assert (dt.hour, dt.minute, dt.second) == (0, 0, 0)
    assert (dt.year, dt.month, dt.day) == (2026, 6, 3)


def test_parse_moex_systime_full():
    dt = parse_moex_systime("2026-06-03 19:05:00", to_day=False)
    assert dt is not None and dt.hour == 19


def test_parse_rss_date():
    dt = parse_rss_date("Tue, 03 Jun 2026 10:00:00 +0300")
    assert dt is not None
    assert dt.tzinfo == UTC
    assert dt.hour == 7  # 10:00 MSK → 07:00 UTC
