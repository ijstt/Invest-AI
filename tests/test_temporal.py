"""Тесты F3 temporal anchoring: извлечение дат и выбор якоря по статусу."""

from __future__ import annotations

from datetime import date

from geoanalytics.nlp.temporal import (
    FUTURE,
    NONE,
    PAST,
    anchor_event_date,
    extract_event_dates,
)

_PUB = date(2026, 6, 11)


class TestExtractEventDates:
    def test_explicit_ru_date_with_year(self):
        dates = extract_event_dates("Отсечка назначена на 10 июля 2026 года", _PUB)
        assert dates == [date(2026, 7, 10)]

    def test_ru_date_without_year_takes_nearest(self):
        assert extract_event_dates("заседание 19 июня", _PUB) == [date(2026, 6, 19)]
        # Декабрьская новость про январь — следующий год.
        dec = date(2026, 12, 28)
        assert extract_event_dates("каникулы до 9 января", dec) == [date(2027, 1, 9)]

    def test_numeric_dates(self):
        dates = extract_event_dates("реестр закроют 20.07.2026, отчёт был 05.06.26",
                                    _PUB)
        assert dates == [date(2026, 7, 20), date(2026, 6, 5)]

    def test_relative_words(self):
        dates = extract_event_dates("Вчера индекс упал, завтра заседание", _PUB)
        assert dates == [date(2026, 6, 10), date(2026, 6, 12)]

    def test_far_dates_dropped(self):
        assert extract_event_dates("основан 12 июня 1990 года", _PUB) == []

    def test_invalid_and_versions_ignored(self):
        assert extract_event_dates("31 февраля 2026, версия 1.2.3", _PUB) == []

    def test_nbsp_between_day_and_month(self):
        assert extract_event_dates("10\xa0июля 2026", _PUB) == [date(2026, 7, 10)]

    def test_no_duplicates_keeps_order(self):
        text = "10 июля 2026, снова 10.07.2026 и ещё 9 июля"
        assert extract_event_dates(text, _PUB) == [date(2026, 7, 10),
                                                   date(2026, 7, 9)]


class TestAnchorEventDate:
    def test_future_takes_nearest_upcoming(self):
        dates = [date(2026, 6, 10), date(2026, 6, 19), date(2026, 7, 20)]
        assert anchor_event_date(dates, _PUB, FUTURE) == date(2026, 6, 19)

    def test_past_takes_latest_passed(self):
        dates = [date(2026, 6, 1), date(2026, 6, 10), date(2026, 7, 1)]
        assert anchor_event_date(dates, _PUB, PAST) == date(2026, 6, 10)

    def test_publication_day_counts_as_past(self):
        assert anchor_event_date([_PUB], _PUB, PAST) == _PUB

    def test_no_matching_dates(self):
        assert anchor_event_date([date(2026, 6, 1)], _PUB, FUTURE) is None
        assert anchor_event_date([date(2026, 7, 1)], _PUB, PAST) is None

    def test_none_status_has_no_anchor(self):
        assert anchor_event_date([date(2026, 6, 19)], _PUB, NONE) is None
