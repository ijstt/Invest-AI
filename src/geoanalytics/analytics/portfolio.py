"""J1 (Волна 4): виртуальный портфель — агрегированная аналитика по позициям.

Отчёт собирает: стоимость и веса позиций → портфельная серия доходностей
(взвешенная сумма по пересечению дат) → риск (волатильность, исторический VaR,
максимальная просадка) → корреляции между холдингами → агрегированная факторная
экспозиция (Σ вес·β из атрибуции G3) → новостной контекст (давление G5,
моментум G6) → текущий режим рынка (G2).

VaR исторический (эмпирический квантиль потерь), не параметрический: хвосты
рублёвых акций толстые, нормальность занижала бы риск. VaR99 на окне ~250 дней
опирается на 2–3 наблюдения — выводится как ориентировочный, основной VaR95.
Активы с покрытием дат < min_points исключаются из риск-серии (веса
ренормализуются), но остаются в стоимости — короткая история не повод
врать про VaR.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from geoanalytics.analytics.attribution import (
    MARKET_TICKER,
    _asset_returns,
    attribute_asset,
)
from geoanalytics.analytics.backtest import _max_drawdown
from geoanalytics.analytics.correlations import (
    _aligned,
    _price_levels,
    _returns_by_date,
    pearson,
)
from geoanalytics.analytics.pressure import news_pressure
from geoanalytics.analytics.regimes import market_regimes
from geoanalytics.analytics.sentiment_trend import latest_momentum
from geoanalytics.core.logging import get_logger
from geoanalytics.storage.repositories import (
    CashBalanceRepository,
    PortfolioRepository,
    PortfolioSnapshotRepository,
)

log = get_logger("analytics.portfolio")

# Минимум общих дат, чтобы актив участвовал в риск-серии/корреляциях.
MIN_POINTS = 20
# Окно риск-расчёта и оценки бет (торговых дней, ~год).
DEFAULT_WINDOW = 250


@dataclass(frozen=True)
class PositionReport:
    """Одна позиция: стоимость, P&L, беты, новостной контекст."""

    ticker: str
    quantity: float
    last_close: float | None = None      # None — нет цен в БД
    value_rub: float | None = None
    weight_pct: float | None = None
    avg_price: float | None = None
    pnl_pct: float | None = None         # от avg_price, если задана
    betas: dict[str, float] = field(default_factory=dict)
    r2: float | None = None
    pressure: float = 0.0                # G5, окно 7д
    momentum: float | None = None        # G6, EWMA-14
    sector: str | None = None            # сектор (для аллокации/drill-down)
    risk_contribution_pct: float | None = None  # вклад в дисперсию портфеля, % (Σ≈100)
    note: str | None = None              # «нет цен», «мало истории для риска»


@dataclass(frozen=True)
class PortfolioReport:
    """Агрегат портфеля: стоимость, риск, корреляции, экспозиция, режим."""

    day: date | None = None
    total_value_rub: float = 0.0
    positions: list[PositionReport] = field(default_factory=list)
    n_obs: int = 0                        # дней в риск-окне (пересечение дат)
    daily_vol_pct: float | None = None
    var95_1d_pct: float | None = None
    var99_1d_pct: float | None = None     # ориентировочный (2–3 точки хвоста)
    var95_1d_rub: float | None = None
    max_drawdown_pct: float | None = None
    correlations: dict[tuple[str, str], float] = field(default_factory=dict)
    exposure: dict[str, float] = field(default_factory=dict)  # Σ вес·β
    avg_r2: float | None = None           # средневзвешенный R² атрибуции
    regime: str | None = None
    value_series: list[tuple[date, float]] = field(default_factory=list)  # стоимость во времени
    value_series_source: str = "reconstructed"   # "snapshots" (реальная) | "reconstructed"
    pnl_series: list[tuple[date, float]] = field(default_factory=list)    # P&L (value−cost), ₽
    sector_alloc: list[tuple[str, float]] = field(default_factory=list)   # сектор → вес %, ↓
    error: str | None = None


def portfolio_returns(
    weights: dict[str, float],
    rets: dict[str, dict[date, float]],
    *,
    min_points: int = MIN_POINTS,
    window: int = DEFAULT_WINDOW,
) -> tuple[dict[date, float], list[str]]:
    """Чистое ядро: взвешенная портфельная доходность по пересечению дат.

    Активы с покрытием общих дат < min_points исключаются (возвращаются вторым
    элементом), веса оставшихся ренормализуются. Пустой результат — нет
    активов с достаточной общей историей."""
    usable = {t: r for t, r in rets.items() if weights.get(t) and r}
    excluded: list[str] = [t for t in weights if t not in usable]
    while usable:
        common = set.intersection(*(set(r) for r in usable.values()))
        if len(common) >= min_points:
            break
        # Выкидываем актив с самой короткой историей — он и режет пересечение.
        shortest = min(usable, key=lambda t: len(usable[t]))
        excluded.append(shortest)
        usable.pop(shortest)
    if not usable:
        return {}, sorted(excluded)
    common_dates = sorted(common)[-window:]
    total_w = sum(weights[t] for t in usable)
    out: dict[date, float] = {}
    for d in common_dates:
        out[d] = sum(weights[t] / total_w * usable[t][d] for t in usable)
    return out, sorted(excluded)


def historical_var(returns: list[float], level: float = 0.95,
                   *, min_points: int = MIN_POINTS) -> float | None:
    """Исторический VaR: эмпирический квантиль потерь, в долях (положительный).

    None — точек меньше min_points (квантиль по горстке наблюдений — шум)."""
    if len(returns) < min_points:
        return None
    return float(-np.percentile(np.asarray(returns), (1.0 - level) * 100.0))


def correlation_matrix(
    rets: dict[str, dict[date, float]], *, min_points: int = MIN_POINTS,
) -> dict[tuple[str, str], float]:
    """Попарные корреляции Пирсона доходностей, верхний треугольник."""
    out: dict[tuple[str, str], float] = {}
    tickers = sorted(rets)
    for i, a in enumerate(tickers):
        for b in tickers[i + 1:]:
            xs, ys = _aligned(rets[a], rets[b])
            if len(xs) >= min_points:
                r = pearson(xs, ys)
                if r is not None:
                    out[(a, b)] = r
    return out


def aggregate_exposure(
    weights: dict[str, float], betas_by_ticker: dict[str, dict[str, float]],
) -> dict[str, float]:
    """Агрегированная факторная экспозиция: Σ вес·β по каждому фактору.

    Отсутствующая бета = 0 (фактор не оценён у актива — например, brent без
    истории или индекс без атрибуции), а не падение."""
    out: dict[str, float] = {}
    for ticker, w in weights.items():
        for factor, beta in betas_by_ticker.get(ticker, {}).items():
            out[factor] = out.get(factor, 0.0) + w * beta
    return {f: round(v, 3) for f, v in out.items()}


def settled_day(asset_rets: dict[date, float],
                market_rets: dict[date, float]) -> date | None:
    """Последний день, покрытый и активом, и рынком.

    Интрадей-бар актива появляется раньше закрытия индекса → атрибуция на
    «сегодня» выкидывает фактор market по покрытию дня. Считаем беты на
    последнем ОБЩЕМ дне — для экспозиции/сценариев важен сам вектор бет,
    а не разложение конкретного дня."""
    common = set(asset_rets) & set(market_rets)
    return max(common) if common else None


def _equity_from_returns(returns: list[float]) -> list[float]:
    """Кривая капитала из дневных доходностей (старт 1.0)."""
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1.0 + r))
    return equity


def _sector_of(asset) -> str | None:
    """Сектор актива через company.sector (None — не привязан/нет компании). Без падения на
    мок-активах без атрибутов (`getattr` мягко)."""
    company = getattr(asset, "company", None)
    sector = getattr(company, "sector", None) if company is not None else None
    return getattr(sector, "name", None) if sector is not None else None


def _latest_fx_rate(session: Session, currency: str) -> float | None:
    """Последний рублёвый курс валюты из `fx_rates` (None — нет данных)."""
    from sqlalchemy import desc, select

    from geoanalytics.storage.models import FxRate

    row = session.execute(
        select(FxRate.value).where(FxRate.currency == currency.upper())
        .order_by(desc(FxRate.ts)).limit(1)
    ).first()
    return float(row[0]) if row is not None else None


def _fx_returns_by_date(session: Session, currency: str) -> dict[date, float]:
    """Дневные доходности рублёвого курса валюты (RUB за единицу) из `fx_rates` — FX-риск
    валютного кэша. RUB (база) → пусто (риска нет). {дата: доходность}."""
    if currency.upper() == "RUB":
        return {}
    from sqlalchemy import select

    from geoanalytics.storage.models import FxRate

    rows = session.execute(
        select(FxRate.ts, FxRate.value).where(FxRate.currency == currency.upper())
        .order_by(FxRate.ts)
    ).all()
    levels = {(ts.date() if hasattr(ts, "date") else ts): float(v) for ts, v in rows}
    return _returns_by_date(levels)


def _cash_positions(session: Session, user_id: int | None) -> list[dict]:
    """Балансы кэша/валют портфеля, оценённые в ₽ (расширение состава). RUB — по номиналу,
    прочие — по последнему курсу ЦБ. Валюта без курса пропускается (не падаем)."""
    out: list[dict] = []
    for ccy, amount in CashBalanceRepository(session, user_id=user_id).list_balances():
        if ccy == "RUB":
            rate, rub = 1.0, amount
        else:
            rate = _latest_fx_rate(session, ccy)
            if rate is None:
                log.warning("cash_no_fx_rate", currency=ccy)
                continue
            rub = amount * rate
        out.append({"ccy": ccy, "amount": amount, "rub": round(rub, 2), "rate": rate})
    return out


def _risk_contributions(
    weights: dict[str, float],
    rets: dict[str, dict[date, float]],
    port_rets: dict[date, float],
) -> dict[str, float]:
    """Вклад каждой позиции в дисперсию портфеля (Euler-разложение), в % (Σ≈100).

    Вклад_i = w_i·cov(r_i, r_port) / var_port. Сумма по входящим в риск-серию активам = 100%
    (т.к. r_port = Σ w_i·r_i). Веса ренормализуются на вошедшие — как в `portfolio_returns`.
    Пустой dict — портфель из одного актива или вырожденная дисперсия."""
    dates = sorted(port_rets)
    if len(dates) < MIN_POINTS:
        return {}
    p = np.asarray([port_rets[d] for d in dates])
    var_p = float(np.var(p))
    usable = {t: w for t, w in weights.items()
              if w and rets.get(t) and all(d in rets[t] for d in dates)}
    total_w = sum(usable.values())
    if var_p <= 0 or total_w <= 0:
        return {}
    pdev = p - p.mean()
    out: dict[str, float] = {}
    for t, w in usable.items():
        xs = np.asarray([rets[t][d] for d in dates])
        cov = float(((xs - xs.mean()) * pdev).mean())
        out[t] = round((w / total_w) * cov / var_p * 100, 1)
    return out


def portfolio_report(session: Session, *, window: int = DEFAULT_WINDOW,
                     live_prices: dict[str, float] | None = None,
                     user_id: int | None = None) -> PortfolioReport:
    """DB-раннер: собирает полный отчёт по портфелю.

    `live_prices` (тикер→интрадей-LAST) переопределяет цену ОЦЕНКИ (стоимость/вес/P&L), чтобы
    портфель совпадал с живой ценой дашборда; риск/доходности/беты всегда считаются по
    EOD-истории. Нет живой цены по тикеру → берётся последнее дневное закрытие. `user_id`
    (5c) — чей портфель: None — владельца (дашборд/CLI), иначе личный бот-пользователя.
    """
    rows = PortfolioRepository(session, user_id=user_id).list_positions()
    cash_rows = _cash_positions(session, user_id)
    if not rows and not cash_rows:
        return PortfolioReport(error="портфель пуст — geo portfolio add ТИКЕР КОЛ-ВО")

    live = live_prices or {}
    # Стоимость позиций + сырьё для риска.
    levels: dict[str, dict[date, float]] = {}
    raw: list[dict] = []
    for asset, pos in rows:
        lv = _price_levels(session, asset.id)
        levels[asset.ticker] = lv
        eod_close = lv[max(lv)] if lv else None
        # Цена оценки — живой LAST, если есть, иначе EOD-закрытие.
        last_close = live.get(asset.ticker, eod_close)
        value = last_close * pos.quantity if last_close is not None else None
        avg = float(pos.avg_price) if pos.avg_price is not None else None
        pnl = (round((last_close / avg - 1) * 100, 2)
               if last_close is not None and avg else None)
        raw.append({
            "asset": asset, "pos": pos, "last_close": last_close,
            "value": value, "avg": avg, "pnl": pnl,
        })

    equity_value = sum(r["value"] for r in raw if r["value"] is not None)
    cash_total = sum(c["rub"] for c in cash_rows)
    total = equity_value + cash_total
    if total <= 0:
        return PortfolioReport(error="ни у одной позиции нет цен в БД")
    weights = {r["asset"].ticker: r["value"] / total
               for r in raw if r["value"] is not None}
    for c in cash_rows:                                # доля кэша в портфеле
        weights[c["ccy"]] = c["rub"] / total

    rets = {t: _returns_by_date(lv) for t, lv in levels.items() if lv}
    # Кэш в риск-серии: RUB (база) — нулевая доходность (разбавляет волатильность/VaR без
    # собственного риска); ВАЛЮТА — реальная доходность курса ЦБ (FX-риск: ₽-стоимость валюты
    # колеблется). Совмещаем с датами риск-серии активов; где курса нет — 0 (не рвём пересечение).
    # Без истории активов кэш в риск-серию не попадёт (риск просто не считается).
    if cash_rows and rets:
        cash_dates = sorted({d for r in rets.values() for d in r})
        for c in cash_rows:
            if c["ccy"] == "RUB":
                rets[c["ccy"]] = dict.fromkeys(cash_dates, 0.0)
            else:
                fx = _fx_returns_by_date(session, c["ccy"])
                rets[c["ccy"]] = {d: fx.get(d, 0.0) for d in cash_dates}
    market_rets = _asset_returns(session, MARKET_TICKER) or {}

    # Беты/новостной контекст по позициям.
    positions: list[PositionReport] = []
    betas_by_ticker: dict[str, dict[str, float]] = {}
    r2_by_ticker: dict[str, float] = {}
    for r in raw:
        asset, pos = r["asset"], r["pos"]
        note = None
        if r["last_close"] is None:
            note = "нет цен — не входит в стоимость и риск"
        attr = attribute_asset(
            session, asset.ticker, window=window,
            day=settled_day(rets.get(asset.ticker, {}), market_rets),
        )
        betas = {} if attr.error else attr.betas
        r2 = None if attr.error else attr.r2
        if betas:
            betas_by_ticker[asset.ticker] = betas
            r2_by_ticker[asset.ticker] = attr.r2
        positions.append(PositionReport(
            ticker=asset.ticker, quantity=pos.quantity,
            last_close=r["last_close"], value_rub=r["value"],
            weight_pct=(round(weights[asset.ticker] * 100, 2)
                        if asset.ticker in weights else None),
            avg_price=r["avg"], pnl_pct=r["pnl"], betas=betas, r2=r2,
            pressure=news_pressure(session, asset.id, window=7),
            momentum=latest_momentum(session, asset.id, span=14),
            sector=_sector_of(asset),
            note=note,
        ))

    # Кэш/валюта — позиции состава. RUB — база (вне риска); валюта несёт FX-риск по курсу ЦБ.
    for c in cash_rows:
        note = ("рубли — база портфеля, вне риска" if c["ccy"] == "RUB"
                else "валютный кэш — риск по курсу ЦБ")
        positions.append(PositionReport(
            ticker=c["ccy"], quantity=c["amount"], last_close=c["rate"],
            value_rub=c["rub"], weight_pct=round(weights[c["ccy"]] * 100, 2),
            sector="Кэш", note=note,
        ))

    # Риск-серия по пересечению дат.
    port_rets, excluded = portfolio_returns(weights, rets, window=window)
    if excluded:
        positions = [
            p if p.ticker not in excluded or p.note else
            replace(p, note="мало общей истории — вне риск-серии")
            for p in positions
        ]
    port_dates = sorted(port_rets)
    series = [port_rets[d] for d in port_dates]
    vol = float(np.std(series, ddof=1)) if len(series) >= MIN_POINTS else None
    var95 = historical_var(series, 0.95)
    var99 = historical_var(series, 0.99)
    equity = _equity_from_returns(series) if series else []
    mdd = _max_drawdown(equity) if series else None

    # Стоимость портфеля во времени: кривая капитала, отмасштабированная к текущей стоимости
    # (последняя точка = total). equity[i+1] — капитал на конец дня port_dates[i].
    value_series: list[tuple[date, float]] = []
    if equity and equity[-1] > 0:
        scale = total / equity[-1]
        value_series = [(port_dates[i], round(equity[i + 1] * scale, 2))
                        for i in range(len(port_dates))]
    value_series_source = "reconstructed"
    pnl_series: list[tuple[date, float]] = []
    # Реальная история по снимкам (≥2 точки) бьёт реконструкцию: дневной job пишет фактическую
    # стоимость/базу. P&L во времени = стоимость − база покупки (только где база известна).
    snaps = PortfolioSnapshotRepository(session, user_id=user_id).history(limit=window)
    if len(snaps) >= 2:
        value_series = [(d, v) for d, v, _ in snaps]
        value_series_source = "snapshots"
        pnl_series = [(d, round(v - c, 2)) for d, v, c in snaps if c is not None]

    # Вклад позиций в риск + аллокация по секторам.
    rc = _risk_contributions(weights, rets, port_rets)
    if rc:
        positions = [replace(p, risk_contribution_pct=rc[p.ticker]) if p.ticker in rc else p
                     for p in positions]
    sector_w: dict[str, float] = {}
    for p in positions:
        if p.weight_pct is not None:
            sector_w[p.sector or "—"] = sector_w.get(p.sector or "—", 0.0) + p.weight_pct
    sector_alloc = sorted(((s, round(w, 1)) for s, w in sector_w.items()),
                          key=lambda kv: kv[1], reverse=True)

    # Средневзвешенный R² — честность экспозиции (какую долю объясняет модель).
    avg_r2 = None
    if r2_by_ticker:
        w_sum = sum(weights.get(t, 0.0) for t in r2_by_ticker)
        if w_sum > 0:
            avg_r2 = round(sum(weights.get(t, 0.0) * r2 for t, r2 in
                               r2_by_ticker.items()) / w_sum, 3)

    regime = market_regimes(session)
    report = PortfolioReport(
        day=max(port_rets) if port_rets else None,
        total_value_rub=round(total, 2),
        positions=positions,
        n_obs=len(series),
        daily_vol_pct=round(vol * 100, 2) if vol is not None else None,
        var95_1d_pct=round(var95 * 100, 2) if var95 is not None else None,
        var99_1d_pct=round(var99 * 100, 2) if var99 is not None else None,
        var95_1d_rub=round(var95 * total, 2) if var95 is not None else None,
        max_drawdown_pct=mdd,
        correlations=correlation_matrix({t: r for t, r in rets.items()
                                         if t not in excluded}),
        exposure=aggregate_exposure(weights, betas_by_ticker),
        avg_r2=avg_r2,
        regime=None if regime.error else regime.current,
        value_series=value_series,
        value_series_source=value_series_source,
        pnl_series=pnl_series,
        sector_alloc=sector_alloc,
    )
    log.info("portfolio_report", positions=len(positions),
             total=report.total_value_rub, n_obs=report.n_obs,
             var95=report.var95_1d_pct, excluded=excluded)
    return report


def live_portfolio_report(session: Session, *, window: int = DEFAULT_WINDOW,
                          user_id: int | None = None) -> PortfolioReport:
    """Отчёт с интрадей-оценкой: подмешивает свежий LAST из `raw_documents` (как дашборд).

    Общий вход для дашборда и бота — оценка (стоимость/вес/P&L) по живой цене, риск по EOD.
    `user_id` (5c) — чей портфель (None — владельца).
    """
    from geoanalytics.analytics.prices import latest_live_prices

    tickers = [a.ticker
               for a, _ in PortfolioRepository(session, user_id=user_id).list_positions()]
    return portfolio_report(session, window=window, user_id=user_id,
                            live_prices=latest_live_prices(session, tickers))


def _cost_basis_rub(report: PortfolioReport) -> float | None:
    """База покупки портфеля для P&L во времени: Σ avg_price·qty по позициям-АКЦИЯМ + текущая
    ₽-стоимость кэша (кэш входит и в стоимость, и в базу → нулевой вклад в P&L). None, если у
    какой-либо акции нет avg_price (неполная база → P&L был бы искажён)."""
    equity = [p for p in report.positions if p.sector != "Кэш"]
    if not equity or any(p.avg_price is None for p in equity):
        return None
    cost = sum(p.avg_price * p.quantity for p in equity)
    cost += sum(p.value_rub or 0.0 for p in report.positions if p.sector == "Кэш")
    return round(cost, 2)


def snapshot_portfolios(session: Session, *, today: date | None = None) -> int:
    """Дневной снимок стоимости/базы каждого портфеля (владелец + юзеры с позициями/кэшем).

    Идемпотентно за день (upsert по дате). Реконструкция стоимости знала лишь текущий состав —
    снимки копят ФАКТИЧЕСКУЮ историю. Возвращает число записанных портфелей.
    """
    from datetime import date as _date

    from sqlalchemy import select as _select

    from geoanalytics.storage.models import CashBalance, PortfolioPosition

    day = today or _date.today()
    uids: set[int | None] = {None}
    uids |= {u for (u,) in session.execute(_select(PortfolioPosition.user_id).distinct())}
    uids |= {u for (u,) in session.execute(_select(CashBalance.user_id).distinct())}
    written = 0
    for uid in uids:
        rep = live_portfolio_report(session, user_id=uid)
        if rep.total_value_rub <= 0:
            continue
        PortfolioSnapshotRepository(session, user_id=uid).upsert(
            day, rep.total_value_rub, _cost_basis_rub(rep))
        written += 1
    log.info("portfolio_snapshots", portfolios=written, day=str(day))
    return written
