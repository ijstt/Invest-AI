"""Тесты C1: движок рекомендаций (AssetStance) — знаковые драйверы, композит, уверенность."""

from __future__ import annotations

from datetime import date

from geoanalytics.analytics.recommendation import (
    AssetStance,
    Driver,
    _posture,
    _series_change_pct,
    apply_quality_gate,
    compose_stance,
    directional_precision,
    forecast_driver,
    fundamental_driver,
    sentiment_driver,
    signal_from_score,
    technical_driver,
)


def test_signal_thresholds_symmetric():
    assert signal_from_score(0.6) == "buy"
    assert signal_from_score(0.3) == "accumulate"
    assert signal_from_score(0.0) == "hold"
    assert signal_from_score(-0.3) == "reduce"
    assert signal_from_score(-0.6) == "sell"


def test_sentiment_driver_sign_and_scale():
    pos = sentiment_driver(0.3, 0.5)
    assert pos is not None and pos.sign == 1 and pos.contribution > 0
    neg = sentiment_driver(-0.4, -0.6)
    assert neg.sign == -1 and neg.contribution < 0
    assert sentiment_driver(None, None) is None


def test_technical_driver_bullish_trend():
    ind = {"last": 110.0, "sma50": 100.0, "sma200": 90.0, "macd_hist": 1.0, "rsi14": 55.0}
    d = technical_driver(ind)
    assert d is not None and d.sign == 1 and d.contribution > 0


def test_technical_driver_overbought_pulls_down():
    # Цена чуть выше SMA, но RSI перекуплен → вклад смягчается.
    hot = technical_driver({"last": 101.0, "sma50": 100.0, "rsi14": 80.0})
    calm = technical_driver({"last": 101.0, "sma50": 100.0, "rsi14": 55.0})
    assert hot.contribution < calm.contribution


def test_technical_driver_none_without_data():
    assert technical_driver({}) is None


def test_forecast_driver_uses_pending_potential_only():
    fc = [
        {"kind": "target_price", "implied_pct": 20.0, "matured": False},
        {"kind": "target_price", "implied_pct": -100.0, "matured": True},   # игнор (наступил)
        {"kind": "dividend", "implied_pct": None},
    ]
    d = forecast_driver(fc)
    assert d is not None and d.contribution == 1.0    # 20% → полный бычий вклад
    assert forecast_driver([{"kind": "dividend", "implied_pct": None}]) is None


def test_compose_excludes_missing_drivers():
    # Только технический сигнал доступен → балл = его знак, не размывается отсутствующими.
    d = technical_driver({"last": 110.0, "sma50": 100.0, "sma200": 90.0})
    st = compose_stance("SBER", [d, None, None])
    assert isinstance(st, AssetStance)
    assert st.score > 0 and st.signal in ("buy", "accumulate")


def test_compose_conflicting_drivers_lower_conviction():
    bull = Driver("technical", "Теханализ", 0.8, 0.36)
    bear = Driver("sentiment", "Настроение", -0.8, 0.34)
    mixed = compose_stance("X", [bull, bear], n_possible=3)
    aligned = compose_stance("Y", [bull, Driver("sentiment", "Настроение", 0.8, 0.34)],
                             n_possible=3)
    # Несогласие драйверов → ниже балл и ниже уверенность, чем при единогласии.
    assert abs(mixed.score) < abs(aligned.score)
    assert mixed.conviction < aligned.conviction


def test_backtest_factor_modulates_conviction():
    d = Driver("technical", "Теханализ", 0.6, 0.36)
    weak = compose_stance("X", [d], backtest_factor=0.85)
    strong = compose_stance("X", [d], backtest_factor=1.18)
    assert strong.conviction > weak.conviction


def test_fundamental_driver_cheap_quality_bullish():
    d = fundamental_driver({"score": 0.8, "verdict": "ok", "flags": [], "positives": ["ROE 25%"]},
                           {"upside_pct": 24.0, "verdict": "недооценён"})
    assert d is not None and d.sign == 1


def test_fundamental_driver_expensive_weak_bearish():
    d = fundamental_driver({"score": 0.2, "verdict": "avoid", "flags": ["убыток"], "positives": []},
                           {"upside_pct": -25.0, "verdict": "переоценён"})
    assert d.sign == -1


def test_fundamental_driver_none_without_inputs():
    assert fundamental_driver(None, None) is None


def test_quality_gate_blocks_buy_on_avoid():
    st = compose_stance("X", [Driver("fundamental", "Фундаментал", 0.9, 0.5)])
    assert st.signal == "buy"
    gated = apply_quality_gate(st, {"verdict": "avoid", "score": 0.2})
    assert gated.signal == "hold" and "квалити-гейт" in gated.note


def test_quality_gate_keeps_signal_when_ok():
    st = compose_stance("X", [Driver("fundamental", "Фундаментал", 0.9, 0.5)])
    gated = apply_quality_gate(st, {"verdict": "ok", "score": 0.8})
    assert gated.signal == "buy"


def test_empty_drivers_is_hold():
    st = compose_stance("X", [None, None, None])
    assert st.signal == "hold" and st.conviction == 0.0


def test_directional_precision_counts_only_directional_moves():
    pairs = [
        (0.6, 2.0),    # бычья стойка, рынок +2% → верно
        (-0.5, -3.0),  # медвежья, рынок −3% → верно
        (0.5, -2.0),   # бычья, рынок −2% → мимо
        (0.05, 5.0),   # стойка нейтральна (|score|<eps) → не учитывается
        (0.6, 0.2),    # рынок почти не двигался (|abn|<1%) → не учитывается
        (0.4, None),   # нет факта → не учитывается
    ]
    out = directional_precision(pairs)
    assert out["n"] == 3 and out["correct"] == 2
    assert abs(out["precision"] - 2 / 3) < 1e-9


def test_directional_precision_empty():
    assert directional_precision([])["precision"] is None


def test_posture_words():
    assert _posture(0.4, 3, 0, 3) == "бычья"
    assert _posture(0.2, 2, 0, 3) == "умеренно-бычья"
    assert _posture(0.0, 0, 0, 3) == "нейтральная"      # все нейтральны
    assert _posture(-0.4, 0, 3, 3) == "медвежья"
    # Сильный разброс знаков при околонулевом балле → смешанная.
    assert _posture(0.05, 3, 3, 6) == "смешанная"
    assert _posture(0.0, 0, 0, 0) == "нет данных"


def test_series_change_pct():
    series = [(date(2026, 1, 1), 100.0), (date(2026, 1, 20), 110.0),
              (date(2026, 2, 1), 121.0)]
    # ~30 дней назад от 01.02 → 02.01 (база 100) → +21%.
    assert _series_change_pct(series, 30) == 21.0
    assert _series_change_pct([(date(2026, 1, 1), 100.0)], 30) is None


def test_as_dict_shape():
    d = technical_driver({"last": 110.0, "sma50": 100.0})
    st = compose_stance("sber", [d])
    out = st.as_dict()
    assert out["ticker"] == "SBER"
    assert set(out) >= {"signal", "label", "score", "conviction", "drivers", "risk"}
    assert out["drivers"][0]["label"] == "Теханализ"
