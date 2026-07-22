"""Команды тестирования торговых стратегий и walk-forward валидации."""

from __future__ import annotations

import typer
from rich.table import Table

from geoanalytics.cli.common import app, console


@app.command()
def backtest(
    ticker: str = typer.Argument(..., help="Тикер, напр. SBER"),
    strategy: str = typer.Option(
        "sma_cross", "--strategy", "-S",
        help="Стратегия: sma_cross | momentum | rsi | macd_cross | bollinger "
             "| candles (свечные паттерны Нисона) | sentiment.",
    ),
    fast: int | None = typer.Option(None, "--fast", help="Быстрая SMA (для sma_cross)."),
    slow: int | None = typer.Option(None, "--slow", help="Медленная SMA (для sma_cross)."),
    lookback: int | None = typer.Option(None, "--lookback", help="Окно моментума."),
    cost_bps: float | None = typer.Option(
        None, "--cost-bps", help="Издержка за сторону сделки, б.п. (по умолчанию из настроек)."
    ),
    sentiment_filter: bool = typer.Option(
        False, "--sentiment-filter", "-f",
        help="Тональный фильтр поверх ценового сигнала (лонг только при неотрицательном фоне)."
    ),
) -> None:
    """Бэктест торгового сигнала по истории котировок актива."""
    from geoanalytics.analytics.backtest import backtest_asset

    # Собираем только явно заданные параметры стратегии.
    params = {k: v for k, v in
              {"fast": fast, "slow": slow, "lookback": lookback}.items() if v is not None}
    try:
        res = backtest_asset(ticker, strategy=strategy, params=params, cost_bps=cost_bps,
                             sentiment_filter=sentiment_filter)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if res is None:
        console.print(f"[yellow]Актив {ticker.upper()} не найден[/]")
        return
    if res.bars < 2:
        console.print(
            f"[yellow]Недостаточно истории. Загрузите: geo backfill -t {ticker.upper()}[/]"
        )
        return

    table = Table(title=f"Бэктест {ticker.upper()} — {strategy}")
    table.add_column("Метрика")
    table.add_column("Значение", justify="right")

    def _fmt(v, suffix="", sign=False):
        if v is None:
            return "—"
        return (f"{v:+.2f}" if sign else f"{v:.2f}") + suffix

    edge = res.total_return_pct - res.buy_hold_return_pct
    edge_str = f"[{'green' if edge >= 0 else 'red'}]{edge:+.2f}%[/]"
    rows = [
        ("Баров", str(res.bars)),
        ("Доходность (чистая)", f"{res.total_return_pct:+.2f}%"),
        ("Доходность (грязная)", f"{res.total_return_gross_pct:+.2f}%"),
        ("Издержки, б.п./сторона", f"{res.cost_bps:g}"),
        ("Buy & Hold", f"{res.buy_hold_return_pct:+.2f}%"),
        ("Преимущество vs B&H", edge_str),
        ("Индекс IMOEX (B&H)", _fmt(res.index_return_pct, "%", sign=True)),
        ("Alpha к индексу", _fmt(res.alpha_pct, "%", sign=True)),
        ("CAGR", _fmt(res.cagr_pct, "%", sign=True)),
        ("Шарп", _fmt(res.sharpe)),
        ("Сортино", _fmt(res.sortino)),
        ("Кальмар", _fmt(res.calmar)),
        ("Макс. просадка", f"-{res.max_drawdown_pct:.2f}%"),
        ("Сделок", str(res.num_trades)),
        ("Доля прибыльных", "—" if res.hit_rate is None else f"{res.hit_rate * 100:.0f}%"),
        ("Profit-factor", _fmt(res.profit_factor)),
        ("Ср. прибыль/убыток", f"{_fmt(res.avg_win_pct, '%', sign=True)} / "
                               f"{_fmt(res.avg_loss_pct, '%', sign=True)}"),
        ("Экспозиция", f"{res.exposure * 100:.0f}%"),
    ]
    for name, value in rows:
        table.add_row(name, value)
    console.print(table)


@app.command()
def walkforward(
    ticker: str = typer.Argument(..., help="Тикер, напр. SBER"),
    strategy: str = typer.Option(
        "sma_cross", "--strategy", "-S",
        help="Стратегия: sma_cross | momentum | rsi | macd_cross | bollinger "
             "| candles (свечные паттерны Нисона).",
    ),
    train: int = typer.Option(120, "--train", help="Длина in-sample окна (баров)."),
    test: int = typer.Option(40, "--test", help="Длина out-of-sample окна (баров)."),
    objective: str = typer.Option(
        "sharpe", "--objective", "-o",
        help="Цель подбора: sharpe | sortino | calmar | cagr | total_return.",
    ),
    anchored: bool = typer.Option(
        False, "--anchored", help="Заякоренный train (растущее окно от начала)."
    ),
    cost_bps: float | None = typer.Option(
        None, "--cost-bps", help="Издержка за сторону сделки, б.п. (по умолчанию из настроек)."
    ),
) -> None:
    """Walk-forward: честная out-of-sample оценка с подбором параметров (анти-overfit)."""
    from geoanalytics.analytics.backtest import walk_forward_asset

    try:
        res = walk_forward_asset(
            ticker, strategy=strategy, train=train, test=test,
            objective=objective, anchored=anchored, cost_bps=cost_bps,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=1) from exc

    if res is None:
        console.print(f"[yellow]Актив {ticker.upper()} не найден[/]")
        return
    if not res.folds:
        console.print(
            f"[yellow]Недостаточно истории для окон train={train}+test={test}. "
            f"Уменьшите окна или загрузите: geo backfill -t {ticker.upper()}[/]"
        )
        return

    eff_str = "—"
    if res.efficiency is not None:
        color = "green" if res.efficiency >= 0.5 else "red"
        eff_str = f"[{color}]{res.efficiency:.2f}[/]"

    summary = Table(title=f"Walk-forward {ticker.upper()} — {strategy} (цель: {objective})")
    summary.add_column("Метрика")
    summary.add_column("Значение", justify="right")
    for name, value in [
        ("Фолдов", str(len(res.folds))),
        (f"Окна (train/test, {'якорь' if res.anchored else 'скольж.'})",
         f"{res.train}/{res.test}"),
        ("OOS доходность (честная)", f"{res.oos_return_pct:+.2f}%"),
        ("OOS Buy & Hold", f"{res.oos_buy_hold_pct:+.2f}%"),
        ("OOS Индекс IMOEX", "—" if res.oos_index_pct is None
         else f"{res.oos_index_pct:+.2f}%"),
        ("OOS Alpha к индексу", "—" if res.oos_alpha_pct is None
         else f"{res.oos_alpha_pct:+.2f}%"),
        ("OOS Шарп", "—" if res.oos_sharpe is None else f"{res.oos_sharpe:.2f}"),
        ("OOS макс. просадка", f"-{res.oos_max_drawdown_pct:.2f}%"),
        ("IS доходность (оптимизм)", f"{res.is_return_pct:+.2f}%"),
        ("Walk-forward efficiency", eff_str),
    ]:
        summary.add_row(name, value)
    console.print(summary)

    folds = Table(title="Фолды (подобранные параметры и IS→OOS доходность)")
    folds.add_column("#", justify="right")
    folds.add_column("Train")
    folds.add_column("Test")
    folds.add_column("Параметры")
    folds.add_column("IS %", justify="right")
    folds.add_column("OOS %", justify="right")
    for i, f in enumerate(res.folds, 1):
        params = ", ".join(f"{k}={v}" for k, v in sorted(f.best_params.items())) or "—"
        oos_color = "green" if f.test_return_pct >= 0 else "red"
        folds.add_row(
            str(i), f"{f.train_start}:{f.train_end}", f"{f.test_start}:{f.test_end}",
            params, f"{f.train_return_pct:+.2f}",
            f"[{oos_color}]{f.test_return_pct:+.2f}[/]",
        )
    console.print(folds)
    console.print(
        "[dim]efficiency = прирост OOS / прирост IS; <0.5 — признак переобучения, "
        "<0 — стратегия теряет вне выборки.[/]"
    )
