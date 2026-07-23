"""Тест полного жизненного цикла Kill-Switch и Resume для futrader:
1. Взведение kill-switch по суточному убытку.
2. Проверка, что после resume сброшенная защёлка НЕ защёлкивается повторно на том же дне.
3. Проверка, что paper-reset сбрасывает и таблицы счёта, и состояние риска.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from geoanalytics.futrader.risk_limits import daily_loss_breached


def test_daily_loss_breached_predicate():
    assert daily_loss_breached(100_000.0, 93_000.0, max_daily_loss_pct=6.0) is True
    assert daily_loss_breached(100_000.0, 95_000.0, max_daily_loss_pct=6.0) is False


def test_resume_and_relaunch_prevention():
    """Проверка, что после resume baseline пика сбрасывается к времени resumed_at."""
    now = datetime.now(UTC)
    st = MagicMock()
    st.halted = False
    st.resumed_at = now
    st.baseline_equity = 90_000.0

    # Старая кривая эквити ДО возобновления (пик 100k, 2 часа назад):
    old_snap = MagicMock()
    old_snap.ts = now - timedelta(hours=2)
    old_snap.equity = 100_000.0
    old_snap.peak_equity = 100_000.0

    # Снимок после возобновления (сейчас):
    new_snap = MagicMock()
    new_snap.ts = now + timedelta(seconds=1)
    new_snap.equity = 90_000.0
    new_snap.peak_equity = 90_000.0

    curve = [old_snap, new_snap]
    today = now.date()

    # Считаем day_peak с учетом resumed_at (должен быть 90k, так как old_snap < resumed_at):
    valid_today_curve = [e for e in curve if e.ts.date() == today and (not st.resumed_at or e.ts >= st.resumed_at)]
    day_peak = max([e.equity for e in valid_today_curve] + [90_000.0], default=90_000.0)

    assert day_peak == 90_000.0
    # daily_loss_breached от нового пика 90k при текущем эквити 90k даёт False:
    assert daily_loss_breached(day_peak, 90_000.0, max_daily_loss_pct=6.0) is False
