"""Трек 2 / Фаза A: тесты торгового календаря FORTS (сессионная дисциплина).

ВАЖНО: свечи MOEX хранятся MSK-настенно с UTC-меткой (parse_moex_systime), поэтому tz-aware
datetime со значениями часов трактуется как MSK-настенное время (конверсии нет)."""

from __future__ import annotations

from datetime import UTC, datetime

from geoanalytics.futrader import session as S


def ts(h, m=0, day=26):
    """2026-06-26 — пятница (будни); часы трактуются как MSK-настенные."""
    return datetime(2026, 6, day, h, m, tzinfo=UTC)


class TestInSession:
    def test_midday_main_open(self):
        assert S.in_session(ts(11)) is True

    def test_before_open_closed(self):
        assert S.in_session(ts(8, 30)) is False

    def test_intraday_clearing_closed(self):
        assert S.in_session(ts(14, 2)) is False
        assert S.in_session(ts(14, 6)) is True          # после клиринга снова открыто

    def test_after_main_close_main_only(self):
        assert S.in_session(ts(19)) is False            # вечерняя при evening=False закрыта

    def test_evening_open_when_enabled(self):
        assert S.in_session(ts(20), evening=True) is True
        assert S.in_session(ts(18, 50), evening=True) is False   # перерыв 18:45–19:00

    def test_weekend_closed(self):
        assert S.in_session(ts(12, day=27)) is False    # суббота


class TestForceFlatDue:
    def test_midday_not_due(self):
        assert S.force_flat_due(ts(12)) is False

    def test_close_window_due(self):
        assert S.force_flat_due(ts(18, 35)) is True     # ≤15 мин до 18:45
        assert S.force_flat_due(ts(18, 20)) is False

    def test_custom_flat_before(self):
        assert S.force_flat_due(ts(18, 20), flat_before_min=30) is True

    def test_after_close_main_only_due(self):
        assert S.force_flat_due(ts(20)) is True         # вечерний бар, evening off → флэт

    def test_evening_not_due_when_enabled(self):
        assert S.force_flat_due(ts(20), evening=True) is False
        assert S.force_flat_due(ts(23, 40), evening=True) is True

    def test_premarket_due(self):
        assert S.force_flat_due(ts(7)) is True

    def test_weekend_due(self):
        assert S.force_flat_due(ts(12, day=27)) is True


class TestEntryAllowed:
    def test_midday_allowed(self):
        assert S.entry_allowed(ts(11)) is True

    def test_close_window_blocked(self):
        assert S.entry_allowed(ts(18, 40)) is False

    def test_clearing_blocked(self):
        assert S.entry_allowed(ts(14, 2)) is False

    def test_clearing_buffer_blocked(self):
        # Буфер клиринга 18:40-19:10 блокирует новый вход
        assert S.in_clearing_window(ts(18, 45)) is True
        assert S.in_clearing_window(ts(19, 0)) is True
        assert S.entry_allowed(ts(18, 45)) is False
        assert S.entry_allowed(ts(19, 0)) is False

    def test_evening_bar_main_only_blocked(self):
        assert S.entry_allowed(ts(20)) is False
        assert S.entry_allowed(ts(20), evening=True) is True


class TestCrossedSession:
    def test_same_day_not_crossed(self):
        assert S.crossed_session(ts(10), ts(17)) is False

    def test_next_day_crossed(self):
        assert S.crossed_session(ts(15, day=25), ts(11, day=26)) is True

    def test_session_date_is_msk_wallclock(self):
        assert S.session_date(ts(23)).day == 26         # вечерний бар — тот же торговый день


class TestWeekendTrading:
    """Рабочие субботы MOEX (перенос праздников) — торгуем, но осторожно (allow_weekend)."""

    def test_is_trading_day_weekend_opt_in(self):
        assert S.is_trading_day(ts(12, day=27).date()) is False                 # суббота, дефолт
        assert S.is_trading_day(ts(12, day=27).date(), allow_weekend=True) is True

    def test_in_session_weekend_when_allowed(self):
        assert S.in_session(ts(12, day=27)) is False
        assert S.in_session(ts(12, day=27), allow_weekend=True) is True          # рабочая суббота

    def test_force_flat_weekend_intraday_not_due_when_allowed(self):
        # На рабочую субботу мид-сессии НЕ форсфлэтим (это нормальный день), лишь к её закрытию.
        assert S.force_flat_due(ts(12, day=27)) is True                  # дефолт: страховка
        assert S.force_flat_due(ts(12, day=27), allow_weekend=True) is False
        assert S.force_flat_due(ts(18, 35, day=27), allow_weekend=True) is True  # окно закрытия

    def test_entry_allowed_weekend_when_allowed(self):
        assert S.entry_allowed(ts(11, day=27)) is False
        assert S.entry_allowed(ts(11, day=27), allow_weekend=True) is True

    def test_low_liquidity_session(self):
        assert S.low_liquidity_session(ts(12, day=27)) is True       # суббота — тонко
        assert S.low_liquidity_session(ts(12)) is False              # будни день — норма
        assert S.low_liquidity_session(ts(20), evening=True) is True  # вечерняя — тонко
        assert S.low_liquidity_session(ts(20)) is False              # вечер без evening-флага


class TestMinutesToClose:
    def test_main_close(self):
        assert S.minutes_to_close(ts(18)) == 45.0

    def test_evening_close(self):
        assert S.minutes_to_close(ts(23), evening=True) == 50.0

    def test_after_close_negative(self):
        assert S.minutes_to_close(ts(19)) < 0
