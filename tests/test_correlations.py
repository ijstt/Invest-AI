"""Тесты корреляций: Пирсон и расчёт доходностей по датам."""

from __future__ import annotations

from datetime import date

from geoanalytics.analytics.correlations import _aligned, _returns_by_date, pearson


def test_pearson_perfect_positive():
    assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == 1.0


def test_pearson_perfect_negative():
    assert pearson([1, 2, 3, 4], [8, 6, 4, 2]) == -1.0


def test_pearson_zero_variance():
    assert pearson([1, 1, 1], [1, 2, 3]) is None


def test_pearson_length_mismatch():
    assert pearson([1, 2, 3], [1, 2]) is None


def test_returns_by_date():
    series = {date(2026, 1, 1): 100.0, date(2026, 1, 2): 110.0, date(2026, 1, 3): 99.0}
    rets = _returns_by_date(series)
    assert rets[date(2026, 1, 2)] == 0.1
    assert round(rets[date(2026, 1, 3)], 3) == -0.1
    # для самой ранней даты доходности нет
    assert date(2026, 1, 1) not in rets


def test_returns_by_date_skips_long_gap():
    """Б14: разрыв больше max_gap_days не даёт многодневной «1-шаговой» доходности."""
    series = {
        date(2026, 1, 1): 100.0,
        date(2026, 1, 2): 110.0,   # +0.10, гэп 1 день — оставляем
        date(2026, 1, 20): 99.0,   # гэп 18 дней — пропускаем
        date(2026, 1, 21): 108.9,  # +0.10 от 99 — оставляем
    }
    rets = _returns_by_date(series, max_gap_days=4)
    assert round(rets[date(2026, 1, 2)], 3) == 0.1
    assert date(2026, 1, 20) not in rets          # доходность через большую дыру выкинута
    assert round(rets[date(2026, 1, 21)], 3) == 0.1
    # max_gap_days=None — явный отказ от гарда (дыра даёт доходность)
    assert date(2026, 1, 20) in _returns_by_date(series, max_gap_days=None)


def test_peer_returns_coverage_threshold(monkeypatch):
    """Б14: дата с покрытием ниже порога не попадает в средний пир-фактор."""
    from geoanalytics.analytics import correlations as corr

    # Три пира; на дне d3 торговал только один — день тонкий.
    levels = {
        1: {date(2026, 6, 1): 100.0, date(2026, 6, 2): 110.0, date(2026, 6, 3): 121.0},
        2: {date(2026, 6, 1): 50.0, date(2026, 6, 2): 55.0},
        3: {date(2026, 6, 1): 10.0, date(2026, 6, 2): 11.0},
    }
    peers = [type("P", (), {"id": i, "ticker": f"T{i}"})() for i in (1, 2, 3)]
    monkeypatch.setattr(corr, "_price_levels", lambda _s, pid: levels[pid])

    class _Scal:
        def scalars(self, _stmt):
            return peers

    out = corr._peer_returns(_Scal(), ["T1", "T2", "T3"], min_coverage=0.5)
    # d2: все 3 пира дали доходность (покрытие 3/3) — включаем.
    assert date(2026, 6, 2) in out
    # d3: только пир 1 (покрытие 1/3 < 0.5) — исключаем.
    assert date(2026, 6, 3) not in out


def test_aligned_common_dates():
    a = {date(2026, 1, 2): 0.1, date(2026, 1, 3): -0.1}
    b = {date(2026, 1, 3): 0.2, date(2026, 1, 4): 0.3}
    xs, ys = _aligned(a, b)
    assert xs == [-0.1] and ys == [0.2]


def test_cross_levels_usd_eur(monkeypatch):
    """Кросс USD/EUR = (USD/RUB)/(EUR/RUB) по общим датам, нулевой знаменатель пропущен."""
    from datetime import date

    from geoanalytics.analytics import correlations as corr

    fx = {
        "USD": {date(2026, 6, 1): 80.0, date(2026, 6, 2): 82.0, date(2026, 6, 3): 81.0},
        "EUR": {date(2026, 6, 1): 100.0, date(2026, 6, 2): 0.0},
    }
    monkeypatch.setattr(corr, "_fx_levels", lambda _s, cur: fx[cur])
    out = corr._cross_levels(None, "USD", "EUR")
    assert out == {date(2026, 6, 1): 0.8}


def test_shift_positions_back_with_ffill_tail():
    """Сдвиг ряда к рынку: значение дня i+k встаёт на дату i, хвост — ffill."""
    from datetime import date

    from geoanalytics.analytics.correlations import shift_positions

    levels = {date(2026, 6, d): float(d) for d in (1, 2, 3, 4, 5)}
    out = shift_positions(levels, 2)
    assert out[date(2026, 6, 1)] == 3.0
    assert out[date(2026, 6, 3)] == 5.0
    # хвост заполнен последним известным значением (доходность там 0)
    assert out[date(2026, 6, 4)] == 5.0 and out[date(2026, 6, 5)] == 5.0
    assert len(out) == 5


def test_shift_positions_degenerate():
    from datetime import date

    from geoanalytics.analytics.correlations import shift_positions

    levels = {date(2026, 6, 1): 1.0}
    assert shift_positions(levels, 2) == levels   # короче сдвига — как есть
    assert shift_positions(levels, 0) == levels


def test_world_metal_levels_divides_and_shifts(monkeypatch):
    """Мировой металл = ₽-цена/курс той же даты, затем сдвиг на лаг публикации."""
    from datetime import date

    from geoanalytics.analytics import correlations as corr

    metal = {date(2026, 6, d): 100.0 * d for d in (1, 2, 3, 4)}
    usd = {date(2026, 6, d): 80.0 for d in (1, 2, 3, 4)}
    monkeypatch.setattr(corr, "_macro_levels", lambda _s, _i: metal)
    monkeypatch.setattr(corr, "_fx_levels", lambda _s, _c: usd)
    out = corr._world_metal_levels(None, "gold")
    # деление: день 3 = 300/80; сдвиг -2: это значение встаёт на день 1
    assert out[date(2026, 6, 1)] == 300.0 / 80.0
    assert out[date(2026, 6, 2)] == 400.0 / 80.0
    # хвост ffill последним мировым уровнем
    assert out[date(2026, 6, 3)] == out[date(2026, 6, 4)] == 400.0 / 80.0
