"""Тесты календаря событий (H2, Волна 3): парсеры источников и правило алертов."""

from __future__ import annotations

from datetime import date

from geoanalytics.alerts.rules import calendar_alerts
from geoanalytics.context.calendar import (
    dividend_records,
    parse_cbr_calendar,
    parse_ru_date,
    parse_smartlab_dividends,
)

# Минимальный слепок реальной вёрстки cbr.ru/dkp/cal_mp/ (2026): вкладки по годам,
# в панели — блоки main-events_day (дата + события дня). День «Резюме обсуждения»
# заседанием НЕ является; nbsp в дате — как на живой странице.
_CBR_HTML = """
<div class="tabs">
  <a role="tab" class="tab _active" href="#t13"
     data-tabs-tab="t13">2026 год</a>
  <a role="tab" class="tab" href="#t12" data-tabs-tab="t12">2025 год</a>
</div>
<div role="tabpanel" data-tabs-content="t13">
  <div class="calendar-main-events">
    <div class="main-events_day">
      <div class="date col-md-5">13 февраля 2026 года</div>
      <div class="main-events">
        <div class="main-event">
          <div class="title"><span>Заседание Совета директоров Банка России
            по ключевой ставке</span></div>
        </div>
        <div class="main-event">
          <div class="title"><span>Пресс-релиз по ключевой ставке</span></div>
        </div>
      </div>
    </div>
    <div class="main-events_day">
      <div class="date col-md-5">26 февраля 2026 года</div>
      <div class="main-events">
        <div class="main-event">
          <div class="title"><span>Резюме обсуждения ключевой ставки</span></div>
        </div>
      </div>
    </div>
    <div class="main-events_day">
      <div class="date col-md-5">20 марта 2026 года</div>
      <div class="main-events">
        <div class="main-event">
          <div class="title"><span>Заседание Совета директоров Банка России
            по ключевой ставке</span></div>
        </div>
      </div>
    </div>
  </div>
</div>
<div role="tabpanel" data-tabs-content="t12">
  <div class="main-events_day">
    <div class="date">14 февраля 2025 года</div>
    <div class="main-event">
      <div class="title">Заседание Совета директоров Банка России по ключевой ставке</div>
    </div>
  </div>
</div>
"""


class TestParseRuDate:
    def test_parses_full_date(self):
        assert parse_ru_date("13 февраля 2026 года") == date(2026, 2, 13)

    def test_parses_inside_text(self):
        assert parse_ru_date("заседание 1 июля 2026 года в 13:30") == date(2026, 7, 1)

    def test_no_date(self):
        assert parse_ru_date("Пресс-релиз по ключевой ставке") is None

    def test_nbsp_between_day_and_month(self):
        # На живой странице ЦБ день и месяц разделены неразрывным пробелом.
        assert parse_ru_date("13\xa0февраля 2026 года") == date(2026, 2, 13)

    def test_invalid_day(self):
        assert parse_ru_date("31 февраля 2026 года") is None


class TestParseCbrCalendar:
    def test_extracts_meetings_from_active_tab_only(self):
        meetings = parse_cbr_calendar(_CBR_HTML)
        # Только активная вкладка 2026: два заседания; «Резюме обсуждения»
        # (26 февраля) и вкладка 2025 не попадают.
        assert meetings == [date(2026, 2, 13), date(2026, 3, 20)]

    def test_empty_html(self):
        assert parse_cbr_calendar("<html><body></body></html>") == []

    def test_fallback_without_tabs(self):
        # Вёрстка без вкладок: парсим весь документ (фильтр окна — на уровне синка).
        html = """<table><tbody>
            <tr><td>24 апреля 2026 года</td></tr>
            <tr><td>Заседание Совета директоров Банка России по ключевой ставке</td></tr>
        </tbody></table>"""
        assert parse_cbr_calendar(html) == [date(2026, 4, 24)]


class TestDividendRecords:
    def test_builds_records(self):
        rows = [
            {"secid": "SBER", "registryclosedate": "2025-07-18", "value": 34.84,
             "currencyid": "RUB"},
            {"secid": "SBER", "registryclosedate": "2026-07-10", "value": 36.5,
             "currencyid": "RUB"},
        ]
        recs = dividend_records("SBER", rows)
        assert len(recs) == 2
        assert recs[1] == {"event_date": date(2026, 7, 10), "value": 36.5,
                           "currency": "RUB"}

    def test_skips_broken_rows(self):
        rows = [{"registryclosedate": None, "value": 1},
                {"registryclosedate": "не дата", "value": 2},
                {"value": 3}]
        assert dividend_records("X", rows) == []


# Слепок таблицы smart-lab.ru/dividends: колонки ищутся по заголовкам;
# вторая строка — дивиденд рекомендован, дата отсечки не назначена.
_SMARTLAB_HTML = """
<table>
  <tr><th>Название</th><th>Тикер</th><th>Период</th><th>Дивиденд, руб</th>
      <th>Див. Дох.</th><th>СД</th><th>Купить До</th>
      <th>Дата закрытия реестра</th><th>Выплата До</th><th>Цена акции</th></tr>
  <tr><td>Сбербанк</td><td>SBER</td><td>2025год</td><td>36,5</td><td>11,2%</td>
      <td>21.04.2026</td><td>09.07.2026</td><td>10.07.2026</td><td>24.07.2026</td>
      <td>326</td></tr>
  <tr><td>Лукойл</td><td>LKOH</td><td>2025год</td><td>541</td><td>8,1%</td>
      <td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td>
      <td>6680</td></tr>
  <tr><td>ТНСэМаЭл-п</td><td>MISBP</td><td>2025год</td><td>6,2055034</td>
      <td>11,7%</td><td>&nbsp;</td><td>12.06.2026</td><td>15.06.2026</td>
      <td>25.06.2026</td><td>53,2</td></tr>
</table>
<table>
  <tr><th>Дата</th><th>Описание</th><th>Ссылка</th></tr>
  <tr><td>10.06.2026</td><td>прочее</td><td>—</td></tr>
</table>
"""


class TestParseSmartlabDividends:
    def test_extracts_rows_with_cutoff_date(self):
        recs = parse_smartlab_dividends(_SMARTLAB_HTML)
        assert {"ticker": "SBER", "event_date": date(2026, 7, 10),
                "value": 36.5} in recs
        # MISBP — не наш актив, но парсер отдаёт всё; фильтр — на синке.
        assert any(r["ticker"] == "MISBP" for r in recs)

    def test_skips_rows_without_date(self):
        recs = parse_smartlab_dividends(_SMARTLAB_HTML)
        assert not any(r["ticker"] == "LKOH" for r in recs)

    def test_ignores_unrelated_tables(self):
        recs = parse_smartlab_dividends(_SMARTLAB_HTML)
        assert len(recs) == 2

    def test_empty_html(self):
        assert parse_smartlab_dividends("<html></html>") == []


class TestCalendarAlerts:
    def test_cbr_meeting_is_market_warning(self):
        alerts = calendar_alerts([{
            "kind": "cbr_rate_meeting", "ticker": None,
            "title": "Заседание СД Банка России по ключевой ставке",
            "event_date": date(2026, 6, 19), "days_left": 1,
        }])
        assert len(alerts) == 1
        a = alerts[0]
        assert a.alert_type == "calendar"
        assert a.ticker is None
        assert a.severity == "warning"
        assert a.dedup_key == "cal:cbr_rate_meeting:MKT:2026-06-19"
        assert "Завтра" in a.message

    def test_dividend_is_info_with_ticker(self):
        alerts = calendar_alerts([{
            "kind": "dividend_cutoff", "ticker": "SBER",
            "title": "Дивидендная отсечка SBER (34.84 RUB)",
            "event_date": date(2026, 7, 10), "days_left": 0,
            "payload": {"value": 34.84, "currency": "RUB"},
        }])
        a = alerts[0]
        assert a.severity == "info"
        assert a.ticker == "SBER"
        assert a.dedup_key == "cal:dividend_cutoff:SBER:2026-07-10"
        assert "Сегодня" in a.message
        assert a.payload["value"] == 34.84

    def test_skips_items_without_kind_or_date(self):
        assert calendar_alerts([{"ticker": "X", "event_date": date(2026, 1, 1)},
                                {"kind": "dividend_cutoff", "ticker": "X"}]) == []

    def test_unknown_kind_defaults_to_info(self):
        a = calendar_alerts([{"kind": "earnings", "ticker": "GAZP",
                              "title": "Отчёт МСФО", "event_date": date(2026, 8, 1),
                              "days_left": 3}])[0]
        assert a.severity == "info"
        assert "Через 3 дн." in a.message
