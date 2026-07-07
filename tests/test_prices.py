"""Тесты выборки ценовых рядов из БД (порядок/окно).

Регресс на баг: `close_series`/`ohlc_series` брали ПЕРВЫЕ свечи истории вместо
последних → при истории длиннее limit `last` оказывался ценой ~limit дней назад
(Роснефть показывала 430 вместо 386). Фикс: desc + limit + разворот в старое→новое.
"""

from __future__ import annotations

from datetime import datetime

from geoanalytics.analytics.prices import (
    apply_live_last,
    asset_indicators,
    close_series,
    latest_live_market,
    latest_live_prices,
    ohlc_series,
    ohlcv_series,
)


class _FakeSession:
    """Имитирует session.execute(stmt) → отдаёт строки как БД при ORDER BY desc (новое→старое)."""

    def __init__(self, rows):
        self._rows = rows

    def execute(self, _stmt):
        return iter(self._rows)


def test_close_series_keeps_latest_and_returns_oldest_first():
    # БД с ORDER BY ts DESC вернула новое→старое; функция должна развернуть в старое→новое.
    desc_rows = [(386.0,), (393.0,), (396.0,)]  # сегодня, вчера, позавчера
    out = close_series(_FakeSession(desc_rows), asset_id=1)
    assert out == [396.0, 393.0, 386.0]
    assert out[-1] == 386.0  # last — самая свежая цена, а не старая


def test_latest_live_prices_picks_freshest_per_ticker():
    """Берёт первый (самый свежий, ORDER BY fetched_at desc) LAST на тикер; чужие игнорит."""
    rows = [
        ({"ticker": "PLZL", "last": "2060"},),   # свежайший PLZL
        ({"ticker": "SBER", "last": 324.5},),
        ({"ticker": "PLZL", "last": "2015"},),   # старее — не должен перетереть
        ({"ticker": "ZZZZ", "last": "1"},),      # не в запросе — пропускается
        ({"ticker": "BAD", "last": None},),      # битая цена — пропускается
    ]
    out = latest_live_prices(_FakeSession(rows), ["PLZL", "SBER", "BAD"])
    assert out == {"PLZL": 2060.0, "SBER": 324.5}


def test_latest_live_prices_empty_tickers():
    assert latest_live_prices(_FakeSession([]), []) == {}


def test_latest_live_market_today_turnover_and_change():
    """Карта рынка по СЕГОДНЯШНИМ данным: VALTODAY-оборот + изменение из живого среза."""
    rows = [
        ({"ticker": "SBER", "volume": "1500000", "change_pct": 1.2, "last": 324.5},),
        ({"ticker": "GAZP", "volume": 800000.0, "change_pct": "-0.8", "last": 120.0},),
        ({"ticker": "SBER", "volume": "1", "change_pct": 9.9},),  # старее — не перетирает
        ({"ticker": "NODATA", "volume": None, "change_pct": None},),  # битые → None
    ]
    out = latest_live_market(_FakeSession(rows), ["SBER", "GAZP", "NODATA"])
    assert out["SBER"] == (1500000.0, 1.2)
    assert out["GAZP"] == (800000.0, -0.8)
    assert out["NODATA"] == (None, None)


def test_ohlc_series_returns_oldest_first():
    d = datetime(2026, 6, 6)
    d1 = datetime(2026, 6, 5)
    # desc: новое→старое
    desc_rows = [(d, 1, 2, 0.5, 1.5), (d1, 1, 2, 0.5, 1.0)]
    out = ohlc_series(_FakeSession(desc_rows), asset_id=1)
    assert [row[0] for row in out] == [d1, d]  # на выходе старое→новое
    assert out[-1][4] == 1.5  # последняя свеча — самая свежая


def test_ohlc_series_fills_missing_with_close():
    d = datetime(2026, 6, 6)
    out = ohlc_series(_FakeSession([(d, None, None, None, 2.0)]), asset_id=1)
    assert out == [(d, 2.0, 2.0, 2.0, 2.0)]


def test_apply_live_last_overrides_displayed_price_on_daily():
    """A1: свод-цена = живой LAST (как портфель), а не закрытие свечи; D-таймфрейм."""
    ind = {"last": 386.0, "rsi14": 55.0, "trend": "up"}
    rows = [({"ticker": "ROSN", "last": "392.7"},)]
    live = apply_live_last(_FakeSession(rows), "ROSN", ind, period="D")
    assert live == 392.7
    assert ind["last"] == 392.7      # показываемая цена обновлена
    assert ind["rsi14"] == 55.0      # производные по закрытиям не тронуты


def test_apply_live_last_noop_on_weekly_and_when_no_slice():
    """На W/M `last` — закрытие сжатого бара (живой тик не к месту); без среза — без изменений."""
    ind_w = {"last": 386.0}
    assert apply_live_last(_FakeSession([({"ticker": "ROSN", "last": "392.7"},)]),
                           "ROSN", ind_w, period="W") is None
    assert ind_w["last"] == 386.0
    ind_d = {"last": 386.0}
    assert apply_live_last(_FakeSession([]), "ROSN", ind_d, period="D") is None
    assert ind_d["last"] == 386.0


def test_asset_indicators_weekly_uses_resampled_series():
    """A7: period='W' считает индикаторы на недельном ресемпле дневной истории."""
    from datetime import timedelta

    from geoanalytics.analytics.indicators import compute_technical
    from geoanalytics.analytics.resample import resample_ohlcv

    # 40 дневных баров (~8 недель) растущего ряда; БД отдаёт desc (новое→старое).
    base = datetime(2026, 1, 1)
    asc = [(base + timedelta(days=i), 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 1000.0)
           for i in range(40)]
    desc = list(reversed(asc))

    got = asset_indicators(_FakeSession(desc), asset_id=1, period="W").as_dict()

    # Ожидаемое: тот же расчёт на недельном ресемпле полного ряда.
    weekly = resample_ohlcv(ohlcv_series(_FakeSession(desc), asset_id=1), "W")
    exp = compute_technical(
        [r[4] for r in weekly], highs=[r[2] for r in weekly],
        lows=[r[3] for r in weekly], volumes=[r[5] for r in weekly],
    ).as_dict()
    assert got == exp
    assert len(weekly) < 40  # ресемпл реально сжал ряд (недель меньше дней)
