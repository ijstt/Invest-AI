"""Тесты Волны 1: рыночная разметка новостей (E2), event study (E1), исходы алертов (E4),
торговая дата новости (Б3) — чистые ядра, без БД."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from geoanalytics.alerts.outcomes import compute_alert_outcome, is_hit
from geoanalytics.analytics.event_study import (
    TypeStats,
    aggregate,
    confounded_ids,
    empirical_weights,
)
from geoanalytics.analytics.outcomes import compute_outcome, estimate_beta
from geoanalytics.core.dates import trading_effective_date

MSK = timedelta(hours=3)  # МСК = UTC+3


def _dt_msk(y, m, d, hh, mm=0):
    """Момент в МСК как UTC-datetime (публикации хранятся в UTC)."""
    return datetime(y, m, d, hh, mm, tzinfo=UTC) - MSK


# --------------------------------------------------------------------------- #
# Б3: торговая дата новости.
# --------------------------------------------------------------------------- #
def test_effective_date_intraday_keeps_day():
    # 12:00 МСК — до закрытия, торговая дата = день публикации.
    assert trading_effective_date(_dt_msk(2026, 6, 1, 12)) == date(2026, 6, 1)


def test_effective_date_after_close_shifts_next_day():
    # 20:00 МСК — после закрытия 18:50, новость влияет только на следующий день.
    assert trading_effective_date(_dt_msk(2026, 6, 1, 20)) == date(2026, 6, 2)


def test_effective_date_boundary_at_close_keeps_day():
    # Ровно 18:50 МСК — ещё внутри сессии (сдвиг строго ПОСЛЕ закрытия).
    assert trading_effective_date(_dt_msk(2026, 6, 1, 18, 50)) == date(2026, 6, 1)


def test_effective_date_naive_treated_as_utc():
    # Naive 16:00 (≈19:00 МСК) — после закрытия → следующий день.
    assert trading_effective_date(datetime(2026, 6, 1, 16, 0)) == date(2026, 6, 2)


# --------------------------------------------------------------------------- #
# E2: compute_outcome / estimate_beta.
# --------------------------------------------------------------------------- #
def _days(start: date, n: int) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def test_compute_outcome_returns_from_pre_news_close():
    dates = _days(date(2026, 1, 1), 10)
    closes = [100, 100, 100, 110, 120, 130, 100, 100, 100, 100]
    # Новость с торговой датой 3 янв: база = 2 янв (индекс 1, close 100) —
    # последнее закрытие СТРОГО ДО новости.
    out = compute_outcome(dates, closes, date(2026, 1, 3))
    assert out is not None
    assert out.base_date == date(2026, 1, 2)
    assert out.rets[1] == pytest.approx((closes[2] / closes[1] - 1) * 100)  # 0.0
    assert out.rets[3] == pytest.approx((closes[4] / closes[1] - 1) * 100)  # 20.0
    assert out.rets[5] == pytest.approx((closes[6] / closes[1] - 1) * 100)  # 0.0


def test_compute_outcome_none_until_horizon_matures():
    dates = _days(date(2026, 1, 1), 5)
    closes = [100.0] * 5
    # База 2 янв (idx 1), горизонт 5 требует idx 6 — истории не хватает.
    assert compute_outcome(dates, closes, date(2026, 1, 3)) is None


def test_compute_outcome_none_before_history():
    dates = _days(date(2026, 1, 10), 10)
    closes = [100.0] * 10
    # Новость старше первой свечи — нет pre-news базы.
    assert compute_outcome(dates, closes, date(2026, 1, 5)) is None


def test_compute_outcome_market_adjusted_with_unit_beta():
    n = 100
    dates = _days(date(2026, 1, 1), n)
    # Актив повторяет индекс день в день (бета = 1, ненулевая дисперсия) →
    # abnormal ≈ 0 на всех горизонтах.
    pattern = [0.01, -0.02, 0.015, -0.005, 0.02]
    idx = [1000.0]
    for i in range(1, n):
        idx.append(idx[-1] * (1 + pattern[i % len(pattern)]))
    closes = [v / 10 for v in idx]
    event = dates[90]
    out = compute_outcome(dates, closes, event, dates, idx)
    assert out is not None
    assert out.beta is not None and abs(out.beta - 1.0) < 1e-6
    assert out.abns is not None
    for h in (1, 3, 5):
        assert abs(out.abns[h]) < 1e-6  # реакция = рынок → abnormal ноль


def test_estimate_beta_two_x_index():
    idx_rets = [0.01, -0.02, 0.015, -0.005, 0.02] * 20
    asset_rets = [2 * r for r in idx_rets]
    beta = estimate_beta(asset_rets, idx_rets)
    assert beta is not None
    assert abs(beta - 2.0) < 1e-9


def test_estimate_beta_insufficient_data_none():
    assert estimate_beta([0.01] * 10, [0.01] * 10) is None


# --------------------------------------------------------------------------- #
# E1: aggregate / empirical_weights / confounded_ids.
# --------------------------------------------------------------------------- #
def _row(i, etype, abn1, asset_id=1, base=date(2026, 1, 1)):
    return {"id": i, "asset_id": asset_id, "base_date": base, "event_type": etype,
            "abn_1": abn1, "abn_3": abn1, "abn_5": abn1}


def test_aggregate_respects_min_n_and_sorts_by_impact():
    rows = [_row(i, "sanctions", 5.0) for i in range(6)]
    rows += [_row(10 + i, "other", 0.5) for i in range(6)]
    rows += [_row(20 + i, "merger", 9.9) for i in range(2)]  # < min_n — выпадает
    stats = aggregate(rows, min_n=5)
    assert [s.event_type for s in stats] == ["sanctions", "other"]
    assert stats[0].n == 6
    assert stats[0].mean_abs[1] == 5.0
    assert stats[0].hit_rate == 1.0       # |5| ≥ 2
    assert stats[1].hit_rate == 0.0       # |0.5| < 2


def test_aggregate_aar_keeps_sign_mean_abs_does_not():
    rows = [_row(1, "macro", 3.0), _row(2, "macro", -3.0),
            _row(3, "macro", 3.0), _row(4, "macro", -3.0), _row(5, "macro", 3.0)]
    stats = aggregate(rows, min_n=5)
    assert stats[0].aar[1] == 0.6          # знаковая взаимогасится
    assert stats[0].mean_abs[1] == 3.0     # сила реакции — нет


def test_empirical_weights_normalized_to_max():
    stats = [
        TypeStats("sanctions", 10, {5: 4.0}, {5: 4.0}, 0.9),
        TypeStats("other", 10, {5: 1.0}, {5: 1.0}, 0.1),
    ]
    w = empirical_weights(stats)
    assert w == {"sanctions": 1.0, "other": 0.25}


def test_confounded_ids_same_asset_other_type_nearby():
    rows = [
        _row(1, "sanctions", 5.0, asset_id=1, base=date(2026, 1, 10)),
        _row(2, "earnings", 1.0, asset_id=1, base=date(2026, 1, 11)),   # рядом, другой тип
        _row(3, "sanctions", 5.0, asset_id=1, base=date(2026, 1, 20)),  # далеко
        _row(4, "earnings", 1.0, asset_id=2, base=date(2026, 1, 10)),   # другой актив
    ]
    bad = confounded_ids(rows, window_days=2)
    assert bad == {1, 2}


def test_confounded_ids_same_type_not_confounded():
    rows = [
        _row(1, "sanctions", 5.0, base=date(2026, 1, 10)),
        _row(2, "sanctions", 4.0, base=date(2026, 1, 11)),
    ]
    assert confounded_ids(rows) == set()


# --------------------------------------------------------------------------- #
# E4: compute_alert_outcome / is_hit.
# --------------------------------------------------------------------------- #
def test_alert_outcome_from_alert_day_close():
    dates = _days(date(2026, 1, 1), 10)
    closes = [100, 100, 100, 100, 110, 100, 100, 100, 100, 100]
    out = compute_alert_outcome(dates, closes, date(2026, 1, 2), horizon=3)
    assert out is not None
    assert out["base_date"] == date(2026, 1, 2)
    assert out["move_pct"] == 10.0  # closes[1]=100 → closes[4]=110


def test_alert_outcome_weekend_falls_back_to_previous_close():
    dates = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7),
             date(2026, 1, 8), date(2026, 1, 9), date(2026, 1, 12)]
    closes = [100, 100, 100, 100, 100, 105]
    # Алерт в субботу 10 янв → база = пятница 9 янв.
    out = compute_alert_outcome(dates, closes, date(2026, 1, 10), horizon=1)
    assert out is not None
    assert out["base_date"] == date(2026, 1, 9)
    assert out["move_pct"] == 5.0


def test_alert_outcome_pending_when_horizon_not_reached():
    dates = _days(date(2026, 1, 1), 3)
    assert compute_alert_outcome(dates, [100, 100, 100], date(2026, 1, 3), 3) is None


def test_alert_outcome_abnormal_subtracts_index():
    dates = _days(date(2026, 1, 1), 6)
    closes = [100, 100, 100, 100, 110, 110]
    idx = [1000, 1000, 1000, 1000, 1050, 1050]
    out = compute_alert_outcome(dates, closes, date(2026, 1, 1), 4, dates, idx)
    assert out is not None
    assert out["move_pct"] == 10.0
    assert abs(out["abn_move_pct"] - 5.0) < 1e-9  # 10% − 5% рынка


def test_is_hit_prefers_abnormal_and_falls_back_to_raw():
    assert is_hit(10.0, 0.5, threshold_pct=2.0) is False  # рынок объяснил движение
    assert is_hit(1.0, -3.0, threshold_pct=2.0) is True   # против рынка — hit
    assert is_hit(3.0, None, threshold_pct=2.0) is True   # индекса нет — по сырому


# --------------------------------------------------------------------------- #
# F6: ближайший сосед в скользящем окне (числовое ядро кластеризации).
# --------------------------------------------------------------------------- #
def test_nearest_in_context_picks_closest_story():
    from collections import deque

    import numpy as np

    from geoanalytics.context.stories import nearest_in_context

    a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    probe = np.array([0.9, 0.1, 0.0], dtype=np.float32)
    probe /= np.linalg.norm(probe)
    ctx = deque([(101, None, a), (202, None, b)])
    sid, dist = nearest_in_context(probe, ctx)
    assert sid == 101
    assert dist < 0.05


def test_nearest_in_context_empty_none():
    from collections import deque

    import numpy as np

    from geoanalytics.context.stories import nearest_in_context

    assert nearest_in_context(np.ones(3), deque()) is None
