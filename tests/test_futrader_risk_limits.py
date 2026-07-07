"""Трек 2 / Пул 9 / B: тесты жёстких риск-лимитов и детекторов аномалий (чистые предикаты)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from geoanalytics.futrader.risk_limits import (
    RiskLimits,
    bar_stale,
    daily_loss_breached,
    entry_bar_too_stale,
    gross_margin_breached,
    interval_hours,
    position_limit_breached,
    pre_trade_check,
    price_jump_anomaly,
    thin_liquidity,
)

NOW = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


class TestSessionFreshness:
    def test_interval_hours(self):
        assert interval_hours("1h") == 1.0
        assert interval_hours("1d") == 24.0
        assert interval_hours("???") == 1.0          # неизвестный → консервативно 1ч

    def test_weekend_bar_blocks_entry(self):
        # пятничный 1h-бар, «сейчас» суббота → старше 3×1ч → вход блокируется
        fri = datetime(2026, 6, 19, 23, 0, tzinfo=UTC)
        sat = datetime(2026, 6, 20, 13, 0, tzinfo=UTC)
        assert entry_bar_too_stale(fri, sat, interval="1h", mult=3.0) is True

    def test_fresh_hourly_bar_allows_entry(self):
        last = NOW - timedelta(hours=1)
        assert entry_bar_too_stale(last, NOW, interval="1h", mult=3.0) is False

    def test_none_ts_safe(self):
        assert entry_bar_too_stale(None, NOW, interval="1h", mult=3.0) is False


class TestThinLiquidity:
    def test_no_volume_is_thin(self):
        assert thin_liquidity(None, 0.5, min_vol_z=-1.5) is True
        assert thin_liquidity(0, 0.5, min_vol_z=-1.5) is True

    def test_low_vol_z_is_thin(self):
        assert thin_liquidity(1000.0, -2.0, min_vol_z=-1.5) is True

    def test_normal_session_ok(self):
        assert thin_liquidity(1000.0, 0.2, min_vol_z=-1.5) is False
        assert thin_liquidity(1000.0, None, min_vol_z=-1.5) is False   # нет z → не блокируем


class TestDailyLoss:
    def test_within_limit(self):
        assert daily_loss_breached(100_000.0, 96_000.0, max_daily_loss_pct=6.0) is False

    def test_at_limit_breached(self):
        assert daily_loss_breached(100_000.0, 94_000.0, max_daily_loss_pct=6.0) is True

    def test_zero_peak_safe(self):
        assert daily_loss_breached(0.0, -5.0, max_daily_loss_pct=6.0) is False


class TestGrossMargin:
    def test_under_ceiling(self):
        assert gross_margin_breached(70_000.0, 100_000.0, max_gross_margin_pct=80.0) is False

    def test_over_ceiling(self):
        assert gross_margin_breached(85_000.0, 100_000.0, max_gross_margin_pct=80.0) is True


class TestPositionLimit:
    def test_within(self):
        assert position_limit_breached(10, max_position=12) is False

    def test_exceeds_either_sign(self):
        assert position_limit_breached(13, max_position=12) is True
        assert position_limit_breached(-13, max_position=12) is True


class TestAnomalies:
    def test_bar_stale_true(self):
        old = NOW - timedelta(hours=80)
        assert bar_stale(old, NOW, max_hours=72.0) is True

    def test_bar_fresh_false(self):
        assert bar_stale(NOW - timedelta(hours=2), NOW, max_hours=72.0) is False

    def test_bar_none_safe(self):
        assert bar_stale(None, NOW, max_hours=72.0) is False

    def test_price_jump_detected(self):
        assert price_jump_anomaly(100.0, 130.0, max_move_pct=25.0) is True

    def test_normal_move_ok(self):
        assert price_jump_anomaly(100.0, 105.0, max_move_pct=25.0) is False

    def test_zero_prev_safe(self):
        assert price_jump_anomaly(0.0, 105.0, max_move_pct=25.0) is False


class TestPreTradeCheck:
    def test_clean_no_halt(self):
        chk = pre_trade_check(day_peak=100_000.0, equity=99_000.0, margin_used=40_000.0,
                              limits=RiskLimits())
        assert chk.halt is False and chk.reasons == ()

    def test_daily_loss_triggers_halt(self):
        chk = pre_trade_check(day_peak=100_000.0, equity=90_000.0, margin_used=10_000.0,
                              limits=RiskLimits())
        assert chk.halt is True
        assert "дневной убыток" in chk.reasons

    def test_margin_triggers_halt(self):
        chk = pre_trade_check(day_peak=100_000.0, equity=100_000.0, margin_used=90_000.0,
                              limits=RiskLimits())
        assert chk.halt is True
        assert "брутто-маржа" in chk.reasons
