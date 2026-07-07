"""Трек 2 / Фаза A: тесты признаков-эдж — as-of forward-fill и сборка вектора контекста."""

from __future__ import annotations

from datetime import UTC, date, datetime

from geoanalytics.futrader.features import EdgeContext, _asof


def _ts(y, m, d):
    return datetime(y, m, d, 12, 0, tzinfo=UTC)


def _ctx(*, regime=None, sent=None, rets=None, asent=None):
    """Сконструировать EdgeContext в обход DB-загрузки (прямой посев приватных полей)."""
    ctx = EdgeContext.__new__(EdgeContext)
    ctx._regime_days = [d for d, _ in (regime or [])]
    ctx._regime = [v for _, v in (regime or [])]
    ctx._sent_days = [d for d, _ in (sent or [])]
    ctx._sent = [v for _, v in (sent or [])]
    ctx._ret_days, ctx._ret_val = {}, {}
    for k, series in (rets or {}).items():
        days = sorted(series)
        ctx._ret_days[k] = days
        ctx._ret_val[k] = [series[d] for d in days]
    ctx._asent_days, ctx._asent = {}, {}
    for code, series in (asent or {}).items():
        days = sorted(series)
        ctx._asent_days[code] = days
        ctx._asent[code] = [series[d] for d in days]
    return ctx


class TestAsof:
    def test_forward_fills_last_le_date(self):
        days = [date(2026, 1, 1), date(2026, 1, 5), date(2026, 1, 10)]
        vals = ["a", "b", "c"]
        assert _asof(days, vals, date(2026, 1, 7)) == "b"   # последний ≤ 7-го
        assert _asof(days, vals, date(2026, 1, 10)) == "c"

    def test_before_first_is_none(self):
        days = [date(2026, 1, 5)]
        assert _asof(days, [1.0], date(2026, 1, 1)) is None

    def test_empty(self):
        assert _asof([], [], date(2026, 1, 1)) is None

    def test_strict_excludes_same_day(self):
        days = [date(2026, 1, 1), date(2026, 1, 5), date(2026, 1, 10)]
        vals = ["a", "b", "c"]
        # strict=True (анти-lookahead интрадей): для самого дня 5 берём ПРЕДЫДУЩИЙ (a), не b.
        assert _asof(days, vals, date(2026, 1, 5), strict=True) == "a"
        assert _asof(days, vals, date(2026, 1, 5), strict=False) == "b"
        assert _asof(days, vals, date(2026, 1, 6), strict=True) == "b"   # D−1 = 5-е
        assert _asof(days, [1.0], date(2026, 1, 1), strict=True) is None  # нет дня < d


class TestFeaturesAt:
    def test_regime_sentiment_and_cross_asset(self):
        ctx = _ctx(
            regime=[(date(2026, 6, 1), (2, 1.8))],
            sent=[(date(2026, 6, 1), (0.12, 0.3))],
            rets={"brent_ret": {date(2026, 6, 1): 0.01},
                  "usd_ret": {date(2026, 6, 1): -0.005},
                  "imoex_ret": {date(2026, 6, 1): 0.002}},
        )
        f = ctx.features_at(_ts(2026, 6, 10))      # forward-fill с 1 июня
        assert f["regime_state"] == 2.0
        assert f["regime_vol"] == 1.8
        assert f["sent_ewma"] == 0.12
        assert f["sent_breadth"] == 0.3
        assert f["brent_ret"] == 1.0               # 0.01 → проценты
        assert f["usd_ret"] == -0.5
        assert f["imoex_ret"] == 0.2

    def test_missing_sources_omitted(self):
        ctx = _ctx(regime=[(date(2026, 6, 1), (0, None))])
        f = ctx.features_at(_ts(2026, 6, 5))
        assert f["regime_state"] == 0.0
        assert "regime_vol" not in f                # vol=None опускается
        assert "sent_ewma" not in f
        assert "brent_ret" not in f

    def test_date_before_history_yields_empty(self):
        ctx = _ctx(regime=[(date(2026, 6, 1), (1, 1.0))])
        assert ctx.features_at(_ts(2026, 5, 1)) == {}


class TestAssetSentiment:
    def test_per_asset_news_sentiment_asof(self):
        # Tier B: сентимент базового актива фьючерса, as-of дате (forward-fill).
        ctx = _ctx(asent={"RTS": {date(2026, 6, 1): (-0.28, 0.0),
                                  date(2026, 6, 10): (0.5, 1.0)}})
        f = ctx.asset_features_at(_ts(2026, 6, 12), "RTS", intraday=True)
        assert f["asset_sent_ewma"] == 0.5 and f["asset_sent_breadth"] == 1.0

    def test_instrument_without_news_is_empty(self):
        # Сырьё/FX без новостного базового актива → признак отсутствует (NaN для модели).
        ctx = _ctx(asent={"RTS": {date(2026, 6, 1): (-0.28, 0.0)}})
        assert ctx.asset_features_at(_ts(2026, 6, 5), "GOLD") == {}

    def test_asset_intraday_excludes_same_day_lookahead(self):
        ctx = _ctx(asent={"BR": {date(2026, 6, 1): (0.1, 0.2),
                                 date(2026, 6, 10): (0.9, 0.8)}})
        intraday = ctx.asset_features_at(_ts(2026, 6, 10), "BR", intraday=True)
        assert intraday["asset_sent_ewma"] == 0.1     # D−1, без same-day утечки

    def test_intraday_excludes_same_day_lookahead(self):
        # Дневной агрегат СВОЕГО дня не виден внутридневному бару: значение дня 10 не должно
        # попасть в бар 10-го при intraday=True (берём D−1 = 1 июня); intraday=False — берём 10-е.
        ctx = _ctx(regime=[(date(2026, 6, 1), (1, 1.0)), (date(2026, 6, 10), (2, 2.0))])
        intraday = ctx.features_at(_ts(2026, 6, 10), intraday=True)
        assert intraday["regime_state"] == 1.0      # D−1, без same-day утечки
        daily = ctx.features_at(_ts(2026, 6, 10), intraday=False)
        assert daily["regime_state"] == 2.0         # дневной бар контемпорален закрытию
