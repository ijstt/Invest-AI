"""Трек 2 / Пул 8: трек-рекорд бумажного счёта — ДОКАЗАТЕЛЬНАЯ результативность песочницы.

Из накопленной кривой эквити (`futures_paper_equity`, снимок/час) и журнала закрытий считаем
метрики, по которым судим «доказана ли результативность за время созревания»: суммарная доходность,
максимальная просадка, реализованный Sharpe (по периодным доходностям кривой), win-rate и
profit-factor по закрытым сделкам, атрибуция P&L по стратегиям и инструментам.

Чистое ядро `compute_track_metrics` (без БД) — предмет тестов; `track_record` — DB-раннер сверху.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from geoanalytics.futrader.evaluation import max_drawdown, profit_factor, sharpe


@dataclass
class TrackMetrics:
    n_points: int = 0
    total_return_pct: float | None = None
    max_drawdown_pct: float | None = None
    sharpe: float | None = None
    n_trades: int = 0
    win_rate: float | None = None
    profit_factor: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None


def compute_track_metrics(equity_points: list[float], closed_pnls: list[float], *,
                          starting_cash: float) -> TrackMetrics:
    """Метрики трек-рекорда из кривой эквити и P&L закрытых сделок (чистая функция)."""
    m = TrackMetrics(n_points=len(equity_points))
    if equity_points and starting_cash > 0:
        m.total_return_pct = round((equity_points[-1] / starting_cash - 1.0) * 100.0, 2)
        m.max_drawdown_pct = round(max_drawdown(equity_points) * 100.0, 2)
        # Периодные доходности кривой → Sharpe (на снимок; без аннуализации на малой выборке).
        rets = [(equity_points[i] - equity_points[i - 1]) / equity_points[i - 1]
                for i in range(1, len(equity_points)) if equity_points[i - 1] > 0]
        sr = sharpe(rets)
        m.sharpe = round(sr, 3) if sr is not None else None
    m.n_trades = len(closed_pnls)
    if closed_pnls:
        wins = [p for p in closed_pnls if p > 0]
        losses = [p for p in closed_pnls if p < 0]
        m.win_rate = round(len(wins) / len(closed_pnls), 3)
        pf = profit_factor(closed_pnls)
        m.profit_factor = round(pf, 3) if pf is not None else None
        m.avg_win = round(sum(wins) / len(wins), 2) if wins else None
        m.avg_loss = round(sum(losses) / len(losses), 2) if losses else None
    return m


@dataclass
class TrackRecord:
    account: str = ""
    starting_cash: float = 0.0
    equity: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    drawdown_pct: float = 0.0
    gross_margin: float = 0.0
    open_positions: int = 0
    metrics: TrackMetrics = field(default_factory=TrackMetrics)
    by_strategy: dict = field(default_factory=dict)
    by_instrument: dict = field(default_factory=dict)
    risk: object = None
    note: str = ""


def track_record(session, *, account: str = "demo",
                 starting_cash: float = 100_000.0) -> TrackRecord:
    """Собрать трек-рекорд счёта из кривой эквити + закрытых сделок (с атрибуцией P&L)."""
    from geoanalytics.storage.repositories import FuturesPaperRepository

    repo = FuturesPaperRepository(session)
    curve = repo.equity_curve(account)
    closed = repo.closed_trades(account)
    pnls = [t.realized_pnl for t in closed if t.realized_pnl is not None]
    rec = TrackRecord(account=account, starting_cash=starting_cash)
    rec.metrics = compute_track_metrics([e.equity for e in curve], pnls,
                                        starting_cash=starting_cash)
    if curve:
        last = curve[-1]
        rec.equity = round(last.equity, 2)
        rec.realized_pnl = round(last.realized_pnl, 2)
        rec.unrealized_pnl = round(last.unrealized_pnl, 2)
        rec.drawdown_pct = round(last.drawdown_pct, 2)
        rec.gross_margin = round(last.gross_margin, 2)
        rec.open_positions = last.open_positions
    else:
        rec.equity = starting_cash
        rec.note = "нет снимков эквити — запустите geo futures-intraday paper для накопления"
    for t in closed:
        if t.realized_pnl is None:
            continue
        rec.by_strategy[t.source] = round(rec.by_strategy.get(t.source, 0.0) + t.realized_pnl, 2)
        rec.by_instrument[t.asset_code] = round(
            rec.by_instrument.get(t.asset_code, 0.0) + t.realized_pnl, 2)

    # Портфельный риск (Пул 9/C): VaR/ES/контрибьюторы/экспозиция по открытым позициям.
    try:
        from geoanalytics.futrader.accumulate import DEFAULT_TICKERS
        from geoanalytics.futrader.paper import _ensure_spec
        from geoanalytics.futrader.portfolio_risk import (
            build_instrument_returns,
            portfolio_risk_report,
        )

        positions = repo.positions(account)
        if positions:
            spec_cache: dict = {}
            for p in positions:
                _ensure_spec(session, spec_cache, p.asset_code)
            rets = build_instrument_returns(session, DEFAULT_TICKERS)
            rec.risk = portfolio_risk_report(positions, rets, spec_cache)
    except Exception:  # noqa: BLE001 — риск-отчёт не критичен для трек-рекорда
        rec.risk = None
    return rec
