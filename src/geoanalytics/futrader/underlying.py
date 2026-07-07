"""Трек 2 / Объективный вход (A0): резолвер фьючерс→базовый актив + его подтверждающие голоса.

Главный архитектурный пробел до этого: фьючерсный трейдер не «знал», на ЧЁМ он торгует.
BR — это Brent, Si — USD/RUB, RTS — индекс МосБиржи. Богатый аналитический слой Трека 1
(факторные ряды сырья/валют, режим рынка L5, рыночный сентимент) был ему недоступен.

Здесь — карта фьючерс→базовый и сборка НЕЗАВИСИМЫХ подтверждающих `Driver`-ов из базового
актива: тренд базового ряда (для сырья/FX), плюс режим + рыночный сентимент + тренд индекса
(для индексных фьючерсов). Драйверы СВОЕЗНАКОВЫ (вверх=+, вниз=−); согласие со стороной
правила проверяет агрегатор conviction (см. `conviction.entry_conviction`).

ЧЕСТНО: у сырьевых/FX/индексных фьючерсов НЕТ пер-активной фундаменталки (нет эмитента) —
поэтому слияние = фактор/макро-тренд + режим + сентимент, без выдуманной equity-стойки.
Всё детерминированно-численно; LLM/GPU не трогаем. Каждый источник изолирован (сбой → пропуск).
"""

from __future__ import annotations

from geoanalytics.analytics.recommendation import Driver, _clamp, sentiment_driver
from geoanalytics.core.logging import get_logger

log = get_logger("futrader.underlying")

# asset_code фьючерса (case-sensitive, как в seed.FUTURES) → (вид базового, ключ базового).
# factor — сырьевой/металл ряд; fx — валютная пара к рублю; index — биржевой индекс (Asset).
UNDERLYING_MAP: dict[str, tuple[str, str]] = {
    "BR": ("factor", "brent"),
    "GOLD": ("factor", "gold"),
    "Si": ("fx", "USD"),
    "Eu": ("fx", "EUR"),
    "CNY": ("fx", "CNY"),
    "RTS": ("index", "IMOEX"),
}

# Веса подтверждающих голосов в агрегаторе conviction (порядок важности; суммой не нормируем —
# `compose_stance` делит на сумму присутствующих весов, отсутствующие не штрафуют).
_W_UNDERLYING_TREND = 1.0
_W_REGIME = 0.6
_W_SENTIMENT = 0.6


def resolve_underlying(asset_code: str) -> tuple[str, str] | None:
    """asset_code фьючерса → (вид, ключ) базового актива, либо None если карта не знает."""
    return UNDERLYING_MAP.get(asset_code)


def _level_series(session, kind: str, key: str) -> list[float]:
    """Уровни базового ряда по дате (хронологически). Переиспуёт загрузчики Трека 1."""
    from geoanalytics.analytics.correlations import (
        _fx_levels,
        _macro_levels,
        _price_levels,
        _world_metal_levels,
    )

    if kind == "fx":
        levels = _fx_levels(session, key)
    elif kind == "factor":
        # металлы (gold/silver/platinum/palladium) — мировая цена; brent/прочее — макро-ряд.
        levels = (_world_metal_levels(session, key)
                  if key in ("gold", "silver", "platinum", "palladium")
                  else _macro_levels(session, key))
    elif kind == "index":
        from geoanalytics.storage.repositories import AssetRepository

        asset = AssetRepository(session).by_ticker(key)
        levels = _price_levels(session, asset.id) if asset else {}
    else:
        levels = {}
    return [levels[d] for d in sorted(levels)]


def _trend_contribution(values: list[float], lookback: int = 20) -> float | None:
    """Знаковый вклад тренда базового ряда: разрыв к SMA(lookback) ⊕ моментум за lookback.

    ±3% к SMA = полный вклад по тренду; ±5% моментум = полный по импульсу. Среднее двух, clamp.
    """
    if len(values) < lookback + 1:
        return None
    last = values[-1]
    sma = sum(values[-lookback:]) / lookback
    base = values[-(lookback + 1)]
    if sma <= 0 or base <= 0:
        return None
    sma_gap = last / sma - 1.0
    momentum = last / base - 1.0
    return _clamp(0.5 * (sma_gap / 0.03) + 0.5 * (momentum / 0.05))


def _regime_contribution(label: str) -> float:
    """Режим рынка L5 → знаковый risk-on/off вклад для ИНДЕКСНОГО базиса.

    Спокойный режим — мягко бычий (risk-on), кризис — выраженно медвежий (risk-off), повышенный/
    переходный — нейтрально. Режим — про волатильность, поэтому вклад умеренный (вес тоже < тренда).
    """
    return {"спокойный": 0.3, "повышенный": -0.05, "переходный": -0.05, "кризис": -0.6}.get(
        label, 0.0)


def underlying_drivers(session, asset_code: str) -> list[Driver]:
    """Независимые подтверждающие голоса базового актива (своезнаковые, не привязаны к стороне).

    factor/fx (BR/GOLD/Si/Eu/CNY): тренд базового ряда.
    index (RTS→IMOEX): тренд индекса + режим рынка + рыночный сентимент.
    Каждый источник в своём try (сбой одного не валит остальные)."""
    resolved = resolve_underlying(asset_code)
    if resolved is None:
        return []
    kind, key = resolved
    drivers: list[Driver] = []

    try:
        values = _level_series(session, kind, key)
        contrib = _trend_contribution(values)
        if contrib is not None:
            arrow = "↑" if contrib > 0 else ("↓" if contrib < 0 else "→")
            drivers.append(Driver(
                "underlying_trend", f"Тренд базового ({key})", contrib, _W_UNDERLYING_TREND,
                f"базовый ряд {arrow} ({len(values)} точек)"))
    except Exception as exc:  # noqa: BLE001 — отсутствие ряда не валит conviction
        log.warning("underlying_trend_failed", asset_code=asset_code, key=key, error=str(exc))

    if kind == "index":
        try:
            from geoanalytics.storage.repositories import MarketRegimeRepository

            regime = MarketRegimeRepository(session).latest()
            if regime is not None:
                drivers.append(Driver(
                    "underlying_regime", "Режим рынка", _regime_contribution(regime.label),
                    _W_REGIME, f"режим «{regime.label}»"))
        except Exception as exc:  # noqa: BLE001
            log.warning("underlying_regime_failed", asset_code=asset_code, error=str(exc))

        try:
            from geoanalytics.analytics import market_sentiment

            agg = market_sentiment.latest(session, scope="market")
            if agg is not None:
                drv = sentiment_driver(agg.sent_ewma, agg.breadth)
                if drv is not None:
                    drivers.append(drv)
        except Exception as exc:  # noqa: BLE001
            log.warning("underlying_sentiment_failed", asset_code=asset_code, error=str(exc))

    return drivers
