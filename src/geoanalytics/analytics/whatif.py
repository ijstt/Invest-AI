"""J4 (Волна 4): сценарный анализ «что-если» поверх факторных бет (G3).

Сценарий = шоки факторов (рынок −5%, USD/RUB +10%, Brent −10%) → ожидаемое
движение актива = Σ β_f · shock_f → P&L портфеля (вес·движение, ₽ от стоимости).

Это ЛИНЕЙНАЯ аппроксимация: беты оценены на исторических дневных движениях,
экстраполяция на шоки крупнее исторических ненадёжна, и модель объясняет лишь
R² дисперсии — остальное идиосинкразия, которую сценарий не видит. Caveats
формируются в раннере и обязаны доходить до пользователя.

Ключевая ставка сознательно НЕ фактор: решений ЦБ ~8 в год, истории ставки в БД
≤ 1.5 года → регрессия на днях изменения имела бы <12 наблюдений, и эффект
закладывается в цены ДО решения. Ставочный сценарий выражается шоками
--market/--usd. Future work: event study вокруг дат решений ЦБ (E1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from geoanalytics.analytics.attribution import (
    DEFAULT_WINDOW,
    MARKET_TICKER,
    _asset_returns,
    attribute_asset,
)
from geoanalytics.analytics.correlations import _price_levels, _returns_by_date
from geoanalytics.analytics.portfolio import settled_day
from geoanalytics.core.logging import get_logger
from geoanalytics.storage.repositories import PortfolioRepository

log = get_logger("analytics.whatif")


@dataclass(frozen=True)
class AssetScenario:
    """Реакция одного актива на сценарий."""

    ticker: str
    expected_move_pct: float
    contributions_pct: dict[str, float] = field(default_factory=dict)  # β_f·shock_f
    r2: float = 0.0
    n_obs: int = 0
    missing_factors: list[str] = field(default_factory=list)  # шок без беты у актива


@dataclass(frozen=True)
class ScenarioResult:
    """Итог сценария: по активам + агрегат портфеля + честные оговорки."""

    shocks_pct: dict[str, float] = field(default_factory=dict)
    assets: list[AssetScenario] = field(default_factory=list)
    portfolio_move_pct: float | None = None
    portfolio_pnl_rub: float | None = None
    total_value_rub: float | None = None
    caveats: list[str] = field(default_factory=list)
    error: str | None = None


def scenario_move(
    betas: dict[str, float], shocks_pct: dict[str, float],
) -> tuple[float, dict[str, float], list[str]]:
    """Чистое ядро: движение актива = Σ β_f·shock_f.

    Возвращает (движение %, вклады по факторам %, факторы шока без беты)."""
    contributions: dict[str, float] = {}
    missing: list[str] = []
    for factor, shock in shocks_pct.items():
        if factor in betas:
            contributions[factor] = round(betas[factor] * shock, 2)
        else:
            missing.append(factor)
    return round(sum(contributions.values()), 2), contributions, missing


def portfolio_scenario(
    weights: dict[str, float], moves_pct: dict[str, float],
) -> float:
    """Движение портфеля = Σ вес·движение актива (по активам с оценкой)."""
    return round(sum(w * moves_pct.get(t, 0.0) for t, w in weights.items()), 2)


def _build_caveats(assets: list[AssetScenario], shocks: dict[str, float]) -> list[str]:
    caveats = [
        "Линейная аппроксимация по историческим бетам: шоки крупнее "
        "исторических дневных движений экстраполируются ненадёжно.",
    ]
    if assets:
        avg_r2 = sum(a.r2 for a in assets) / len(assets)
        caveats.append(
            f"Модель объясняет в среднем {avg_r2:.0%} дисперсии (R²) — "
            "остальное идиосинкразия, сценарий её не видит."
        )
    missing = sorted({f for a in assets for f in a.missing_factors})
    if missing:
        caveats.append(
            f"Факторы без оценённой беты у части активов: {', '.join(missing)} "
            "— их вклад принят за 0."
        )
    if "market" in shocks:
        caveats.append(
            "β_sector частично дублирует рынок — эффект рыночного шока "
            "может занижаться (сектор в сценарии не шокируется)."
        )
    return caveats


def whatif_asset(
    session: Session, ticker: str, shocks_pct: dict[str, float],
    *, window: int = DEFAULT_WINDOW,
) -> ScenarioResult:
    """DB-раннер: сценарий для одного актива (без портфеля)."""
    asset_rets = _asset_returns(session, ticker) or {}
    market_rets = _asset_returns(session, MARKET_TICKER) or {}
    attr = attribute_asset(session, ticker, window=window,
                           day=settled_day(asset_rets, market_rets))
    if attr.error:
        return ScenarioResult(shocks_pct=shocks_pct,
                              error=f"{ticker.upper()}: {attr.error}")
    move, contrib, missing = scenario_move(attr.betas, shocks_pct)
    scenario = AssetScenario(ticker=attr.ticker, expected_move_pct=move,
                             contributions_pct=contrib, r2=attr.r2,
                             n_obs=attr.n_obs, missing_factors=missing)
    return ScenarioResult(shocks_pct=shocks_pct, assets=[scenario],
                          portfolio_move_pct=None,
                          caveats=_build_caveats([scenario], shocks_pct))


def whatif_portfolio(
    session: Session, shocks_pct: dict[str, float],
    *, window: int = DEFAULT_WINDOW, user_id: int | None = None,
) -> ScenarioResult:
    """DB-раннер: сценарий для всего портфеля (веса по текущей стоимости).

    `user_id` (None — владелец) выбирает портфель — чтобы сценарный путь ask был per-user.
    """
    rows = PortfolioRepository(session, user_id=user_id).list_positions()
    if not rows:
        return ScenarioResult(shocks_pct=shocks_pct,
                              error="портфель пуст — geo portfolio add ТИКЕР КОЛ-ВО")

    values: dict[str, float] = {}
    rets: dict[str, dict] = {}
    for asset, pos in rows:
        levels = _price_levels(session, asset.id)
        if levels:
            values[asset.ticker] = levels[max(levels)] * pos.quantity
            rets[asset.ticker] = _returns_by_date(levels)
    total = sum(values.values())
    if total <= 0:
        return ScenarioResult(shocks_pct=shocks_pct,
                              error="ни у одной позиции нет цен в БД")
    weights = {t: v / total for t, v in values.items()}

    market_rets = _asset_returns(session, MARKET_TICKER) or {}
    scenarios: list[AssetScenario] = []
    moves: dict[str, float] = {}
    skipped: list[str] = []
    for asset, _pos in rows:
        attr = attribute_asset(
            session, asset.ticker, window=window,
            day=settled_day(rets.get(asset.ticker, {}), market_rets),
        )
        if attr.error:
            skipped.append(asset.ticker)
            continue
        move, contrib, missing = scenario_move(attr.betas, shocks_pct)
        moves[asset.ticker] = move
        scenarios.append(AssetScenario(
            ticker=attr.ticker, expected_move_pct=move,
            contributions_pct=contrib, r2=attr.r2, n_obs=attr.n_obs,
            missing_factors=missing,
        ))
    if not scenarios:
        return ScenarioResult(shocks_pct=shocks_pct,
                              error="ни по одному активу нет оценённых бет")

    port_move = portfolio_scenario(weights, moves)
    caveats = _build_caveats(scenarios, shocks_pct)
    if skipped:
        caveats.append(
            f"Без оценки (мало данных для регрессии): {', '.join(sorted(skipped))} "
            "— приняты неподвижными."
        )
    result = ScenarioResult(
        shocks_pct=shocks_pct, assets=scenarios,
        portfolio_move_pct=port_move,
        portfolio_pnl_rub=round(port_move / 100 * total, 2),
        total_value_rub=round(total, 2),
        caveats=caveats,
    )
    log.info("whatif_done", shocks=shocks_pct, assets=len(scenarios),
             portfolio_move=port_move)
    return result
