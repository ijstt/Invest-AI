"""Команды трейдинга фьючерсов FORTS: интрадей, стакан (depth), политики и бумажная торговля."""

from __future__ import annotations

import typer
from rich.table import Table

from geoanalytics.cli.common import _fmt, app, console

futures_intraday_app = typer.Typer(help="Интрадей-данные и симулятор фьючерсов FORTS (Трек 2 / T2.1–T2.2).")
app.add_typer(futures_intraday_app, name="futures-intraday")

futures_depth_app = typer.Typer(help="Захват стакана (L2 depth) фьючерсов FORTS (Трек 2, миграция 0037).")
app.add_typer(futures_depth_app, name="futures-depth")


@futures_depth_app.command("capture")
def futures_depth_capture(
    interval_sec: float = typer.Option(5.0, "--interval-sec", help="Период снятия, секунд."),
    once: bool = typer.Option(False, "--once", help="Один снимок и выход (для проверки)."),
    all_hours: bool = typer.Option(False, "--all-hours", help="Снимать вне торговой сессии тоже."),
) -> None:
    """Снять микроструктуру стакана (best/спред/дисбаланс) фронт-контрактов в futures_orderbook.
    Без --once — бесконечный цикл (служба geo-depth): история копится ТОЛЬКО ВПЕРЁД (ISS не отдаёт
    прошлые снимки; полный L2-ладдер анонимно недоступен — берём агрегатную глубину marketdata)."""
    from geoanalytics.futrader.depth import capture_loop, capture_once
    from geoanalytics.storage.db import session_scope

    if once:
        with session_scope() as session:
            n = capture_once(session)
        console.print(f"[green]✓[/] снимков микроструктуры записано: {n}")
        return
    console.print(f"[cyan]Захват микроструктуры каждые {interval_sec}s (Ctrl-C для выхода)…[/]")
    capture_loop(interval_sec=interval_sec, only_in_session=not all_hours)


@futures_depth_app.command("status")
def futures_depth_status() -> None:
    """Сколько снимков стакана накоплено и свежесть по инструментам."""
    from geoanalytics.futrader.accumulate import DEFAULT_TICKERS
    from geoanalytics.futrader.data import _asset_code_for
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesOrderbookRepository

    with session_scope() as session:
        repo = FuturesOrderbookRepository(session)
        total = repo.count()
        console.print(f"Снимков стакана всего: [bold]{total}[/]")
        for tk in DEFAULT_TICKERS:
            row = repo.latest(_asset_code_for(tk))
            if row is not None:
                console.print(f"  {tk:>4}: последний {row.ts:%Y-%m-%d %H:%M} bid={row.best_bid} ask={row.best_ask} imb={row.imbalance}")


@futures_intraday_app.command("backfill")
def futures_intraday_backfill(
    asset: str = typer.Option(..., "--asset", "-a", help="Тикер фьючерса (BR/GD/SI/EU/CNY/RTS)."),
    interval: str = typer.Option("1m", "--interval", "-i", help="1m | 10m | 1h."),
    days: int = typer.Option(7, "--days", "-d", help="Глубина окна, дней."),
    max_contracts: int = typer.Option(3, "--max-contracts", help="Сколько ближайших контрактов."),
) -> None:
    """Загрузить интрадей-свечи фьючерса по контрактам в futures_candles.

    Минутки ISS отдаёт лишь за недавнее окно — берём что есть. Идемпотентно (повтор не плодит)."""
    from geoanalytics.futrader.data import backfill_futures_intraday
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        try:
            added = backfill_futures_intraday(session, asset, interval=interval, days=days, max_contracts=max_contracts)
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
    console.print(f"[green]✓[/] {asset.upper()} {interval}: новых свечей {added}")


@futures_intraday_app.command("continuous")
def futures_intraday_continuous(
    asset: str = typer.Option(..., "--asset", "-a", help="Тикер фьючерса."),
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h."),
) -> None:
    """Построить непрерывный (склееный) контракт из futures_candles и показать стыки-роллы."""
    from geoanalytics.futrader.continuous import continuous_series
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        series = continuous_series(session, asset, interval=interval)
    if not series.bars:
        console.print(f"[yellow]Нет данных для {asset.upper()} {interval}. Сначала: geo futures-intraday backfill -a {asset.upper()} -i {interval}[/]")
        return
    b0, b1 = series.bars[0], series.bars[-1]
    console.print(f"Непрерывный контракт [bold]{asset.upper()}[/] {interval}: баров {len(series.bars)}, с {b0.ts:%Y-%m-%d %H:%M} ({b0.close:.2f}) по {b1.ts:%Y-%m-%d %H:%M} ({b1.close:.2f})")
    if series.rolls:
        table = Table(title="Роллы (стыки контрактов)")
        table.add_column("Дата")
        table.add_column("С")
        table.add_column("На")
        table.add_column("Коэф.", justify="right")
        for r in series.rolls:
            table.add_row(f"{r['ts']:%Y-%m-%d}", r["from_secid"], r["to_secid"], f"{r['factor']:.4f}")
        console.print(table)
    else:
        console.print("[dim]Один контракт — роллов нет.[/]")


@futures_intraday_app.command("accumulate")
def futures_intraday_accumulate(
    interval: str = typer.Option(None, "--interval", "-i", help="Один интервал (по умолчанию час+день)."),
    days: int = typer.Option(None, "--days", "-d", help="Окно бэкфилла, дней (по умолч. — по интервалу)."),
    horizon: int = typer.Option(12, "--horizon", "-H", help="Горизонт разметки исхода, баров."),
) -> None:
    """Фаза 0: накопить пулинг-датасет — бэкфилл ВСЕХ фьючерсов × интервалов + лог ВСЕХ стратегий.

    Узкое место Трека 2 — глубина данных. Команда копит её разом по всем инструментам в общий
    размеченный датасет futures_decisions (пулинг для T2.4). Идемпотентно; то же гоняет ежедневный
    джоб scheduler (офф-пик). Тяжёлый проход по ISS — запускайте вне пиковой нагрузки."""
    from geoanalytics.futrader.accumulate import DEFAULT_INTERVALS, accumulate_dataset
    from geoanalytics.storage.db import session_scope

    intervals = (interval,) if interval else DEFAULT_INTERVALS
    with session_scope() as session:
        res = accumulate_dataset(session, intervals=intervals, days=days, horizon_bars=horizon)
    table = Table(title="Накопление пулинг-датасета (Трек 2 / Фаза 0)")
    for col in ("Инструмент", "Интервал", "Свечей+", "Решений", "Размечено"):
        table.add_column(col)
    for s in res.stats:
        table.add_row(s.ticker, s.interval, f"{s.candles}", f"{s.decisions}", f"{s.labeled}")
    console.print(table)
    console.print(f"[green]✓[/] Итого: свечей +{res.candles}, решений {res.decisions}, размечено {res.labeled}")


@futures_intraday_app.command("simulate")
def futures_intraday_simulate(
    asset: str = typer.Option(..., "--asset", "-a", help="Тикер фьючерса (BR/GD/SI/EU/CNY/RTS)."),
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h."),
    cash: float = typer.Option(100_000.0, "--cash", help="Стартовый капитал, ₽."),
    contracts: int = typer.Option(1, "--contracts", "-n", help="Контрактов в buy-and-hold."),
) -> None:
    """T2.2: прогнать симулятор исполнения по непрерывному ряду (демо buy-and-hold лонг).

    Проверка движка end-to-end на живых данных: спека контракта берётся с ISS, P&L считается в ₽
    через стоимость шага цены, учтены ГО/комиссия/проскальзывание. Стратегия — открыть лонг на
    первом баре и держать; реальные политики придут в T2.3/T2.4."""
    from geoanalytics.analytics.history import _front_futures_secid
    from geoanalytics.futrader.continuous import continuous_series
    from geoanalytics.futrader.data import _asset_code_for, fetch_contract_spec
    from geoanalytics.futrader.execution import ExecutionSimulator, Order
    from geoanalytics.storage.db import session_scope

    secid = _front_futures_secid(_asset_code_for(asset))
    if secid is None:
        console.print(f"[red]Нет активного контракта для {asset.upper()}.[/]")
        raise typer.Exit(1)
    spec = fetch_contract_spec(secid)
    if spec is None:
        console.print(f"[red]Не удалось получить спецификацию контракта {secid}.[/]")
        raise typer.Exit(1)
    with session_scope() as session:
        series = continuous_series(session, asset, interval=interval)
    if not series.bars:
        console.print(f"[yellow]Нет данных. Сначала: geo futures-intraday backfill -a {asset.upper()} -i {interval}[/]")
        return

    sim = ExecutionSimulator(spec, starting_cash=cash)
    opened = {"done": False}

    def buy_and_hold(_bar, _sim):
        if opened["done"]:
            return None
        opened["done"] = True
        return Order(side="buy", qty=contracts)

    res = sim.run(series.bars, strategy=buy_and_hold)
    console.print(f"[bold]{asset.upper()}[/] {interval} · контракт {secid} (ГО {spec.initial_margin:,.0f}₽, шаг {spec.tick_size:g}→{spec.tick_value:g}₽, комиссия {spec.fee:g}₽)")
    console.print(f"Баров {len(series.bars)} · сделок {res.n_trades} (отклонено {res.rejected}) · комиссия {res.fees_paid:,.0f}₽")
    color = "green" if res.return_pct >= 0 else "red"
    console.print(f"Эквити {res.starting_cash:,.0f}₽ → [bold {color}]{res.final_equity:,.0f}₽[/] ([{color}]{res.return_pct:+.2f}%[/]) · просадка {res.max_drawdown_rub:,.0f}₽" + (" · [red]ЛИКВИДАЦИЯ[/]" if res.liquidated else ""))


@futures_intraday_app.command("log-decisions")
def futures_intraday_log_decisions(
    asset: str = typer.Option(..., "--asset", "-a", help="Тикер фьючерса (BR/GD/SI/EU/CNY/RTS)."),
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h."),
    source: str = typer.Option("sma_cross", "--strategy", "-s", help="Политика: sma_cross|momentum|rsi|macd|bollinger."),
    horizon: int = typer.Option(12, "--horizon", "-H", help="Горизонт разметки исхода, баров."),
    qty: int = typer.Option(1, "--qty", "-q", help="Контрактов на решение."),
) -> None:
    """T2.3: прогнать политику по непрерывному ряду → лог решений + признаки + исходы.

    Накопительная обучающая выборка для T2.4 (fine-tune на своих исходах). Идемпотентно: повтор
    обновляет разметку по дозревшим данным."""
    from geoanalytics.futrader.decisions import log_decisions
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        try:
            res = log_decisions(session, asset, interval=interval, source=source, qty=qty, horizon_bars=horizon)
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from exc
    if not res.decisions:
        console.print(f"[yellow]Нет данных. Сначала: geo futures-intraday backfill -a {asset.upper()} -i {interval}[/]")
        return
    wr = f"{res.win_rate:.0%}" if res.win_rate is not None else "—"
    console.print(f"[green]✓[/] {asset.upper()} {interval} · политика {source}: решений {len(res.decisions)} (записано {res.stored}), размечено {res.labeled}, win-rate {wr} (горизонт {horizon} баров)")


@futures_intraday_app.command("decisions")
def futures_intraday_decisions(
    asset: str = typer.Option(..., "--asset", "-a", help="Тикер фьючерса."),
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h."),
    source: str = typer.Option(None, "--strategy", "-s", help="Фильтр по политике (опц.)."),
    limit: int = typer.Option(20, "--limit", "-n", help="Сколько последних показать."),
) -> None:
    """Показать последние залогированные решения с признаками и исходом."""
    from geoanalytics.futrader.data import _asset_code_for
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesDecisionRepository

    code = _asset_code_for(asset)
    with session_scope() as session:
        rows = FuturesDecisionRepository(session).recent(code, interval, source=source, limit=limit)
        rows = [(r.ts, r.source, r.action, r.signed_qty, r.price, r.label, r.outcome_return_pct, r.outcome_pnl_rub) for r in rows]
    if not rows:
        console.print(f"[yellow]Нет решений для {asset.upper()} {interval}. Сначала: geo futures-intraday log-decisions -a {asset.upper()} -i {interval}[/]")
        return
    table = Table(title=f"Решения {asset.upper()} {interval}")
    for col in ("Время", "Политика", "Действие", "Qty", "Цена", "Исход", "Δ%", "P&L ₽"):
        table.add_column(col)
    for ts, src, action, sq, price, label, ret, pnl in rows:
        lab = {"win": "[green]win[/]", "loss": "[red]loss[/]", "flat": "[dim]flat[/]"}.get(label, "[dim]—[/]")
        table.add_row(f"{ts:%m-%d %H:%M}", src, action, f"{sq:+d}", f"{price:.2f}", lab, f"{ret:+.2f}" if ret is not None else "—", f"{pnl:+,.0f}" if pnl is not None else "—")
    console.print(table)


@futures_intraday_app.command("train-policy")
def futures_intraday_train_policy(
    asset: str = typer.Option(None, "--asset", "-a", help="Тикер (опц.; без него — учить на всех активах политики)."),
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h (для бэктеста)."),
    source: str = typer.Option("sma_cross", "--strategy", "-s", help="Политика: sma_cross|momentum|rsi|macd|bollinger."),
    threshold: float = typer.Option(0.55, "--threshold", "-t", help="Порог P(win) для сделки."),
    min_samples: int = typer.Option(30, "--min-samples", help="Минимум размеченных решений."),
    backtest: bool = typer.Option(True, "--backtest/--no-backtest", help="Сравнить raw-правило vs фильтр в симуляторе."),
) -> None:
    """T2.4: обучить мета-фильтр P(win) на размеченных решениях + честные метрики (+ бэктест).

    Правило предлагает сделку, модель гейтит/сайзит по P(win). Оценка — time-ordered hold-out
    (финданные не перемешиваем). Накопить решения: geo futures-intraday log-decisions."""
    from geoanalytics.futrader.data import _asset_code_for
    from geoanalytics.futrader.policy import evaluate_on_simulator, load_policy, train_policy
    from geoanalytics.storage.db import session_scope

    code = _asset_code_for(asset) if asset else None
    with session_scope() as session:
        res = train_policy(session, source=source, asset_code=code, threshold=threshold, min_samples=min_samples)
        if not res.trained:
            console.print(f"[yellow]Не обучено:[/] {res.note} (всего размечено {res.n_total})")
            return
        console.print(f"[green]✓[/] Политика {source} ({code or 'all'}): обучено на {res.n_train}, тест {res.n_test}. Модель → {res.model_path}")
        base = f"{res.base_win_rate:.0%}" if res.base_win_rate is not None else "—"
        prec = f"{res.model_precision:.0%}" if res.model_precision is not None else "—"
        lift = f"{res.lift:+.1%}" if res.lift is not None else "—"
        auc = f"{res.auc:.3f}" if res.auc is not None else "—"
        console.print(f"  Hold-out: база win-rate {base} → модель {prec} (взято {res.n_taken} сделок, lift {lift}); AUC {auc}")
        if res.lift is not None and res.lift <= 0:
            console.print("  [yellow]Фильтр пока НЕ улучшает базу — нужно больше данных/дозревания (это ожидаемо на крошечной выборке).[/]")

        if backtest and asset:
            policy = load_policy(code, source)
            out = evaluate_on_simulator(session, policy, asset, interval, source=source, threshold=threshold) if policy else None
            if out:
                raw, gated = out
                console.print(f"  Бэктест хвоста {asset.upper()} {interval}: raw {raw.return_pct:+.2f}% (сделок {raw.n_trades}) | фильтр {gated.return_pct:+.2f}% (сделок {gated.n_trades})")


@futures_intraday_app.command("evaluate")
def futures_intraday_evaluate(
    asset: str = typer.Option(None, "--asset", "-a", help="Тикер (опц.; без него — пулинг)."),
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h | 1d."),
    strategy: str = typer.Option(None, "--strategy", "-s", help="Одна политика (по умолч. все)."),
    threshold: float = typer.Option(0.55, "--threshold", "-t", help="Порог P(win) гейта."),
    splits: int = typer.Option(5, "--splits", help="Число walk-forward фолдов."),
    record: bool = typer.Option(True, "--record/--no-record", help="Писать прогон в реестр + обновлять чемпиона."),
) -> None:
    """Фаза B: walk-forward оценка политики (Sharpe/Sortino/maxDD/deflated Sharpe) + реестр моделей.

    Несколько последовательных OOS-окон с эмбарго (против утечки времени). deflated Sharpe штрафует
    за мультитестинг (число прогоняемых политик). Запись в `futures_model_runs`, авто-выбор чемпиона
    (только при положительном lift и улучшении DSR — консервативно к шуму)."""
    from geoanalytics.futrader.data import _asset_code_for
    from geoanalytics.futrader.decisions import SIGNAL_FNS
    from geoanalytics.futrader.evaluation import evaluate_and_record, run_walk_forward
    from geoanalytics.futrader.signals import CROSS_SECTIONAL
    from geoanalytics.storage.db import session_scope

    code = _asset_code_for(asset) if asset else None
    strategies = [strategy] if strategy else list(SIGNAL_FNS) + list(CROSS_SECTIONAL)
    n_trials = len(strategies)
    table = Table(title=f"Walk-forward оценка ({code or 'пулинг'} {interval})")
    for col in ("Политика", "Решений", "Фолд", "Взято", "База", "Модель", "Lift", "AUC", "Sharpe", "maxDD", "PF", "DSR", "Brier", "CalGap"):
        table.add_column(col)
    with session_scope() as session:
        for s in strategies:
            runner = evaluate_and_record if record else run_walk_forward
            res = runner(session, source=s, asset_code=code, interval=interval, threshold=threshold, n_splits=splits, n_trials=n_trials)
            table.add_row(
                s, str(res.n_samples), str(res.n_folds), str(res.n_taken),
                _fmt(res.base_win_rate, pct=True), _fmt(res.model_win_rate, pct=True),
                _fmt(res.lift, pct=True), _fmt(res.auc), _fmt(res.sharpe),
                _fmt(res.max_drawdown, pct=True), _fmt(res.profit_factor),
                _fmt(res.deflated_sharpe, ".2f"), _fmt(res.brier, ".3f"), _fmt(res.calib_gap, ".3f")
            )
    console.print(table)
    if record:
        console.print("[dim]Записано в реестр futures_model_runs; чемпионы — geo futures-intraday models.[/]")


@futures_intraday_app.command("models")
def futures_intraday_models(
    interval: str = typer.Option(None, "--interval", "-i", help="Фильтр интервала (опц.)."),
    limit: int = typer.Option(15, "--limit", "-n", help="Сколько последних прогонов."),
) -> None:
    """Показать реестр оценок политик (последние прогоны, чемпионы помечены ★)."""
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesModelRunRepository

    with session_scope() as session:
        rows = FuturesModelRunRepository(session).recent(interval=interval, limit=limit)
        rows = [(r.ts, r.source, r.asset_code, r.interval, r.n_taken, r.lift, r.sharpe, r.deflated_sharpe, r.is_champion) for r in rows]
    if not rows:
        console.print("[yellow]Реестр пуст. Сначала: geo futures-intraday evaluate[/]")
        return
    table = Table(title="Реестр политик (futures_model_runs)")
    for col in ("Время", "Политика", "Актив", "Инт.", "Взято", "Lift", "Sharpe", "DSR", ""):
        table.add_column(col)
    for ts, src, code, itv, taken, lift, shp, dsr, champ in rows:
        table.add_row(f"{ts:%m-%d %H:%M}", src, code or "пулинг", itv, str(taken), _fmt(lift, pct=True), _fmt(shp), _fmt(dsr, ".2f"), "★" if champ else "")
    console.print(table)


@futures_intraday_app.command("pbo")
def futures_intraday_pbo(
    interval: str = typer.Option("1h", "--interval", "-i", help="1m | 10m | 1h | 1d."),
    splits: int = typer.Option(6, "--splits", help="Число purged K-fold блоков."),
) -> None:
    """Пул 4: PBO — вероятность переобучения бэктеста (CSCV по стратегиям, purged K-fold).

    Насколько отбор лучшей стратегии по in-sample переносится на out-of-sample. PBO<0.3 — надёжно;
    >0.5 — выбор по бэктесту не лучше монетки (ловим шум). Главный тест «доказанности» эджа."""
    from geoanalytics.futrader.decisions import SIGNAL_FNS
    from geoanalytics.futrader.evaluation import run_cpcv_pbo
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        res = run_cpcv_pbo(session, sources=list(SIGNAL_FNS), interval=interval, n_splits=splits)
    if res.pbo is None:
        console.print(f"[yellow]{res.note}[/]")
        return
    color = "green" if res.pbo < 0.3 else ("yellow" if res.pbo < 0.5 else "red")
    console.print(f"[bold]PBO = [{color}]{res.pbo:.2f}[/][/] ({res.note})")
    means = ", ".join(f"{s}={v:+.3f}" for s, v in res.oos_sharpe_mean.items())
    console.print(f"  Средний OOS-Sharpe по стратегиям: {means}")
    console.print("  [dim]PBO<0.3 — отбор стратегии надёжен; >0.5 — бэктест ловит шум.[/]")


@futures_intraday_app.command("drift")
def futures_intraday_drift(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
    interval: str = typer.Option("1h", "--interval", "-i", help="Интервал."),
    no_halt: bool = typer.Option(False, "--no-halt", help="Только наблюдать, не взводить halt."),
) -> None:
    """Пул 9/D: live-дрейф чемпиона — PSI признаков, калибровка по бумажным исходам, decay win-rate.

    Сдвиг входных данных (PSI), поломка калибровки или просадка win-rate относительно OOS-ожидания —
    сигнал деградации модели. При жёстком дрейфе взводит kill-switch (если не --no-halt) + алерт."""
    from geoanalytics.futrader.decisions import SIGNAL_FNS
    from geoanalytics.futrader.monitoring import run_drift_monitor
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        reports = run_drift_monitor(session, sources=list(SIGNAL_FNS), account=account, interval=interval, auto_halt=not no_halt)
    table = Table(title=f"Дрейф чемпионов ({account} {interval})")
    for col in ("Стратегия", "PSI-max", "Признак", "Live-Brier", "CalGap", "WR live", "WR ожид.", "Decay", "Сделок", "Halt"):
        table.add_column(col)
    for r in reports:
        table.add_row(r.source, _fmt(r.psi_max, ".2f"), r.psi_worst_feature or "—", _fmt(r.live_brier, ".3f"), _fmt(r.live_calib_gap, ".3f"), _fmt(r.win_rate_live, pct=True), _fmt(r.win_rate_expected, pct=True), _fmt(r.win_rate_decay, ".3f"), str(r.n_live_trades), "⛔" if r.should_halt else "")
    console.print(table)
    console.print("[dim]PSI>0.25 — заметный сдвиг входов; >0.5/калибр>0.3/decay>0.2 при ≥20 сделках → halt.[/]")


@futures_intraday_app.command("paper")
def futures_intraday_paper(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
    interval: str = typer.Option("1h", "--interval", "-i", help="Интервал торговли."),
    cash: float = typer.Option(100_000.0, "--cash", help="Стартовый капитал счёта, ₽."),
    risk: float = typer.Option(1.0, "--risk", help="Целевой риск на сделку, % эквити."),
    max_dd: float = typer.Option(25.0, "--max-dd", help="Лимит просадки (circuit-breaker), %."),
) -> None:
    """Фаза D (T2.5): один бумажный цикл — квалифицированные чемпионы торгуют на свежих барах.

    Гейт качества пускает лишь стратегии с положительным OOS lift+Sharpe и достаточной выборкой
    (см. реестр geo futures-intraday models). Размер — vol-targeting×Келли; circuit-breaker по
    просадке. Реальных ордеров нет — всё на бумажном счёте. Перед запуском: evaluate (чемпионы)."""
    from geoanalytics.futrader.paper import run_paper_cycle
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        res = run_paper_cycle(session, account=account, interval=interval, starting_cash=cash, target_risk_pct=risk, max_dd_pct=max_dd)
    q = ", ".join(res.qualified_strategies) or "—"
    console.print(f"[green]✓[/] Бумажный цикл [{account}] {interval}: открыто {res.opened}, закрыто {res.closed}, маркеров {res.marked}; квалиф. стратегии: {q}")
    regime = f"режим «{res.regime}»" + (" — входы заблокированы" if res.blocked_regime else "")
    if res.halted:
        console.print(f"[bold red]⛔ KILL-SWITCH: {res.halt_reason}[/] — новые входы заблокированы (выходы идут). Снять: geo futures-intraday resume.")
    console.print(
        f"  Гейт отсёк {res.skipped_gate} стратегий, брейкер {res.blocked_breaker}, режим {res.blocked_regime}, conviction {res.blocked_conviction}, "
        f"бюджет {res.blocked_budget}, halt {res.blocked_halt}, аномалий {res.anomalies}, устар./выходные {res.blocked_stale}, "
        f"ликвидность {res.blocked_liquidity}, сессия {res.blocked_session}, издержки {res.blocked_cost}; {regime}"
    )
    if res.session_flat or res.barrier_exits:
        console.print(f"  [yellow]⏰ Дисциплина выхода: барьер {res.barrier_exits} (SL/TP/тайм-стоп), флэт к закрытию {res.session_flat} (не держим под закрытие/овернайт)[/]")
    console.print(f"  реализ. P&L {res.realized_pnl:+,.0f}₽, нереализ. {res.unrealized_pnl:+,.0f}₽, эквити {res.equity:,.0f}₽; просадка {res.drawdown_pct:.1f}%, риск-скейл {res.risk_scale:.2f}, маржа {res.gross_margin:,.0f}₽")


@futures_intraday_app.command("risk-status")
def futures_intraday_risk_status(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
) -> None:
    """Пул 9/B: состояние kill-switch счёта + действующие жёсткие лимиты."""
    from geoanalytics.futrader.risk_limits import RiskLimits
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesRiskStateRepository

    with session_scope() as session:
        st = FuturesRiskStateRepository(session).get(account)
    lim = RiskLimits()
    if st and st.halted:
        console.print(f"[bold red]⛔ ОСТАНОВЛЕН[/] [{account}]: {st.reason or '—'} (с {st.updated_at:%Y-%m-%d %H:%M}). Снять: geo futures-intraday resume.")
    else:
        console.print(f"[green]✓ Активен[/] [{account}] — kill-switch не взведён.")
    console.print(f"  Лимиты: дневной убыток {lim.max_daily_loss_pct:.0f}%, брутто-маржа {lim.max_gross_margin_pct:.0f}%, позиция ≤{lim.max_position_per_instrument}, устаревание бара {lim.max_bar_staleness_hours:.0f}ч, скачок цены {lim.max_price_jump_pct:.0f}%")


@futures_intraday_app.command("paper-reset")
def futures_intraday_paper_reset(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Без подтверждения."),
) -> None:
    """Чистый сброс бумажного счёта: позиции + снимки эквити + лог сделок (датасет НЕ трогаем).

    Для рестарта песочницы «с чистого листа» (напр. после legacy-переплеча): маржа→0, транзиентный
    halt снимется сам, эквити вернётся к стартовому. Обучающие свечи/решения сохраняются."""
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesPaperRepository

    if not yes:
        ok = typer.confirm(f"Сбросить бумажный счёт [{account}] (позиции/эквити/сделки)?")
        if not ok:
            console.print("[dim]Отменено.[/]")
            raise typer.Exit()
    with session_scope() as session:
        deleted = FuturesPaperRepository(session).reset_account(account)
    console.print(f"[green]✓[/] Счёт [{account}] сброшен: позиций {deleted['positions']}, снимков эквити {deleted['equity']}, сделок {deleted['trades']}. Датасет сохранён. Маржа→0, halt снят.")


@futures_intraday_app.command("resume")
def futures_intraday_resume(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
) -> None:
    """Пул 9/B: снять kill-switch вручную (после разбора причины остановки)."""
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesRiskStateRepository

    with session_scope() as session:
        repo = FuturesRiskStateRepository(session)
        was = repo.is_halted(account)
        repo.set_state(account, halted=False, reason=None)
    if was:
        console.print(f"[green]✓[/] Kill-switch снят для [{account}] — торговля возобновлена.")
    else:
        console.print(f"[dim]Счёт [{account}] и так не был остановлен.[/]")


@futures_intraday_app.command("paper-status")
def futures_intraday_paper_status(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
    cash: float = typer.Option(100_000.0, "--cash", help="Стартовый капитал (для эквити)."),
    trades: int = typer.Option(0, "--trades", "-t", help="Показать N последних сделок."),
) -> None:
    """Состояние бумажного счёта: открытые позиции, реализованный P&L, эквити."""
    from geoanalytics.analytics.history import _front_futures_secid
    from geoanalytics.futrader.data import fetch_contract_spec
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import FuturesPaperRepository

    with session_scope() as session:
        repo = FuturesPaperRepository(session)
        positions = repo.positions(account)
        realized = sum(p.realized_pnl for p in positions)
        unreal = 0.0
        ptable = Table(title=f"Бумажные позиции [{account}]")
        for col in ("Инстр.", "Инт.", "Стратегия", "Qty", "Сред.", "Послед.", "Реал.P&L", "Нереал."):
            ptable.add_column(col)
        spec_cache: dict = {}
        for p in positions:
            u = 0.0
            if p.net_qty and p.avg_price and p.last_price:
                if p.asset_code not in spec_cache:
                    sid = _front_futures_secid(p.asset_code)
                    spec_cache[p.asset_code] = fetch_contract_spec(sid) if sid else None
                spec = spec_cache[p.asset_code]
                if spec is not None:
                    u = spec.pnl_rub(p.last_price - p.avg_price, p.net_qty)
                    unreal += u
            ptable.add_row(p.asset_code, p.interval, p.source, f"{p.net_qty:+d}", f"{p.avg_price:.2f}" if p.avg_price else "—", f"{p.last_price:.2f}" if p.last_price else "—", f"{p.realized_pnl:+,.0f}", f"{u:+,.0f}")
        if positions:
            console.print(ptable)
        else:
            console.print(f"[yellow]Нет позиций на счёте [{account}]. Сначала: geo futures-intraday paper[/]")
        equity = cash + realized + unreal
        color = "green" if equity >= cash else "red"
        console.print(f"Реализованный P&L [bold]{realized:+,.0f}₽[/], нереализованный {unreal:+,.0f}₽; эквити [{color}]{equity:,.0f}₽[/] (старт {cash:,.0f}₽)")
        if trades:
            tt = Table(title="Последние бумажные сделки")
            for col in ("Время", "Инстр.", "Стр.", "Действ.", "Qty", "Цена", "P(win)", "P&L"):
                tt.add_column(col)
            for t in repo.recent_trades(account, limit=trades):
                tt.add_row(f"{t.ts:%m-%d %H:%M}", t.asset_code, t.source, t.action, f"{t.signed_qty:+d}", f"{t.price:.2f}", f"{t.p_win:.2f}" if t.p_win is not None else "—", f"{t.realized_pnl:+,.0f}" if t.realized_pnl is not None else "—")
            console.print(tt)


@futures_intraday_app.command("track-record")
def futures_intraday_track_record(
    account: str = typer.Option("demo", "--account", help="Бумажный счёт."),
    cash: float = typer.Option(100_000.0, "--cash", help="Стартовый капитал (база доходности)."),
) -> None:
    """Пул 8: трек-рекорд песочницы — доходность/просадка/Sharpe/win-rate по кривой эквити.

    Накопленная кривая `futures_paper_equity` (снимок/час) + закрытые сделки → метрики, по которым
    судим «доказана ли результативность». Атрибуция P&L по стратегиям и инструментам. Чем дольше
    созревает счёт, тем достовернее цифры (точек больше)."""
    from geoanalytics.futrader.track import track_record
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        rec = track_record(session, account=account, starting_cash=cash)
    if rec.note:
        console.print(f"[yellow]{rec.note}[/]")
    m = rec.metrics
    color = "green" if rec.equity >= rec.starting_cash else "red"
    console.print(f"[bold]Трек-рекорд [{account}][/] — эквити [{color}]{rec.equity:,.0f}₽[/] (старт {rec.starting_cash:,.0f}₽), доходность {_fmt(m.total_return_pct, '+.2f')}%, снимков {m.n_points}")
    console.print(f"  реализ. {rec.realized_pnl:+,.0f}₽, нереализ. {rec.unrealized_pnl:+,.0f}₽; просадка {rec.drawdown_pct:.1f}%, maxDD {_fmt(m.max_drawdown_pct, '.1f')}%, Sharpe {_fmt(m.sharpe, '.2f')}; открытых позиций {rec.open_positions}")
    console.print(f"  сделок {m.n_trades}, win-rate {_fmt(m.win_rate, pct=True)}, profit-factor {_fmt(m.profit_factor, '.2f')}, ср.прибыль {_fmt(m.avg_win, '+.0f')}₽, ср.убыток {_fmt(m.avg_loss, '+.0f')}₽")
    if rec.by_strategy:
        by_s = ", ".join(f"{k} {v:+,.0f}₽" for k, v in sorted(rec.by_strategy.items(), key=lambda kv: kv[1], reverse=True))
        console.print(f"  по стратегиям: {by_s}")
    if rec.by_instrument:
        by_i = ", ".join(f"{k} {v:+,.0f}₽" for k, v in sorted(rec.by_instrument.items(), key=lambda kv: kv[1], reverse=True))
        console.print(f"  по инструментам: {by_i}")
    risk = rec.risk
    if risk is not None and risk.n_instruments:
        console.print(f"  [bold]портфельный риск[/]: VaR95 {_fmt(risk.var_pct, '.2f')}%, ES95 {_fmt(risk.es_pct, '.2f')}%; брутто {risk.gross_exposure:,.0f}₽, нетто {risk.net_exposure:+,.0f}₽")
        if risk.contributions:
            contrib = ", ".join(f"{k} {v:+.0f}%" for k, v in sorted(risk.contributions.items(), key=lambda kv: abs(kv[1]), reverse=True))
            console.print(f"  риск-контрибьюторы: {contrib}")
        if risk.top_correlations:
            corr = ", ".join(f"{pair} {v:+.2f}" for pair, v in risk.top_correlations)
            console.print(f"  топ-корреляции: {corr}")
