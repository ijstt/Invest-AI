"""G3 (Волна 3): факторная атрибуция дневной доходности актива.

OLS-разложение: r_asset = α + Σ β_f · r_factor + ε. Факторы — рынок (IMOEX),
сектор (средняя доходность пиров), USD/RUB, Brent (когда накопится история).
Ответ на главный вопрос при любом алерте: «SBER −3% — это рынок упал или
что-то своё?» Вклад фактора за день = β_f · доходность фактора; остаток —
идиосинкразия, которую и должны объяснять новости.

Беты оцениваются на трейлинг-окне ДО дня разложения (leave-one-out: день не
объясняет сам себя). Факторы включаются динамически — у кого мало истории
(сейчас Brent: дни), тот выпадает и вернётся сам по мере накопления данных.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from geoanalytics.analytics.correlations import (
    _fx_levels,
    _macro_levels,
    _peer_returns,
    _price_levels,
    _returns_by_date,
    _world_metal_levels,
)
from geoanalytics.context.graph import factors_for_asset
from geoanalytics.core.logging import get_logger
from geoanalytics.storage.models import Asset

log = get_logger("analytics.attribution")

# Дефолтное окно оценки бет (торговых дней, ~год) и минимум точек для OLS.
DEFAULT_WINDOW = 250
MIN_POINTS = 60

MARKET_TICKER = "IMOEX"


@dataclass
class AttributionResult:
    """Разложение доходности одного дня + оценённая модель."""

    ticker: str
    day: date
    asset_return_pct: float                      # фактическая доходность дня
    alpha_pct: float                             # дневная альфа модели
    betas: dict[str, float] = field(default_factory=dict)
    contributions_pct: dict[str, float] = field(default_factory=dict)
    idio_pct: float = 0.0                        # остаток дня (идиосинкразия)
    r2: float = 0.0                              # R² на окне оценки
    n_obs: int = 0                               # точек в регрессии
    error: str | None = None


def ols_attribution(
    asset_rets: dict[date, float],
    factor_rets: dict[str, dict[date, float]],
    *,
    day: date | None = None,
    window: int = DEFAULT_WINDOW,
    min_points: int = MIN_POINTS,
) -> AttributionResult | None:
    """Чистое ядро: OLS на трейлинг-окне + разложение дня `day`.

    `day` (дефолт — последняя дата актива) исключается из оценки бет.
    Факторы без покрытия дня или с пересечением < min_points отбрасываются.
    None — данных не хватает даже без факторов.
    """
    if not asset_rets:
        return None
    day = day or max(asset_rets)
    if day not in asset_rets:
        return None

    # Отбор факторов: покрытие дня + достаточное пересечение с активом до дня.
    history = {d for d in asset_rets if d < day}
    used: dict[str, dict[date, float]] = {}
    for name, rets in factor_rets.items():
        if day in rets and len(history & set(rets)) >= min_points:
            used[name] = rets

    common = sorted(history.intersection(*(set(r) for r in used.values()))
                    if used else history)
    common = common[-window:]
    if len(common) < min_points or not used:
        return None

    names = sorted(used)
    y = np.array([asset_rets[d] for d in common])
    x = np.column_stack([np.ones(len(common))] +
                        [[used[n][d] for d in common] for n in names])
    coef, *_ = np.linalg.lstsq(x, y, rcond=None)
    alpha, betas = float(coef[0]), {n: float(b) for n, b in
                                    zip(names, coef[1:], strict=True)}

    resid = y - x @ coef
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum(resid**2)) / ss_tot if ss_tot > 0 else 0.0

    day_ret = asset_rets[day]
    contributions = {n: betas[n] * used[n][day] for n in names}
    idio = day_ret - alpha - sum(contributions.values())
    return AttributionResult(
        ticker="", day=day,
        asset_return_pct=round(day_ret * 100, 2),
        alpha_pct=round(alpha * 100, 4),
        betas={n: round(b, 3) for n, b in betas.items()},
        contributions_pct={n: round(c * 100, 2) for n, c in contributions.items()},
        idio_pct=round(idio * 100, 2),
        r2=round(r2, 3), n_obs=len(common),
    )


def _asset_returns(session: Session, ticker: str) -> dict[date, float] | None:
    asset = session.query(Asset).filter(Asset.ticker == ticker).first()
    if asset is None:
        return None
    return _returns_by_date(_price_levels(session, asset.id))


def attribute_asset(session: Session, ticker: str, *, day: date | None = None,
                    window: int = DEFAULT_WINDOW) -> AttributionResult:
    """DB-раннер: собирает факторы и раскладывает доходность дня тикера."""
    ticker = ticker.upper()
    result = AttributionResult(ticker=ticker, day=day or date.today(),
                               asset_return_pct=0.0, alpha_pct=0.0)
    asset = session.query(Asset).filter(Asset.ticker == ticker).first()
    if asset is None:
        result.error = "актив не найден"
        return result
    asset_rets = _returns_by_date(_price_levels(session, asset.id))

    factors: dict[str, dict[date, float]] = {}
    market = _asset_returns(session, MARKET_TICKER)
    if market and ticker != MARKET_TICKER:
        factors["market"] = market
    fx = _returns_by_date(_fx_levels(session, "USD"))
    if fx:
        factors["usd_rub"] = fx
    brent = _returns_by_date(_macro_levels(session, "brent"))
    if brent:
        factors["brent"] = brent
    # Золото — мировая цена в USD из учётной цены ЦБ с поправкой лага публикации
    # (без поправки фактор на 79% дублирует usd_rub и опаздывает на 2 дня —
    # см. _world_metal_levels). Рублёвую экспозицию OLS соберёт из пары с usd_rub.
    gold = _returns_by_date(_world_metal_levels(session, "gold"))
    if gold:
        factors["gold"] = gold
    peers = factors_for_asset(session, asset).peers
    if peers:
        sector = _peer_returns(session, peers)
        if sector:
            factors["sector"] = sector

    core = ols_attribution(asset_rets, factors, day=day, window=window)
    if core is None:
        result.error = "мало данных для регрессии"
        return result
    core.ticker = ticker
    log.info("attribution_done", ticker=ticker, day=str(core.day),
             r2=core.r2, factors=list(core.betas))
    return core
