"""Команды управления виртуальным портфелем, фундаменталкой эмитентов и сегментами выручки."""

from __future__ import annotations

import typer
from rich.table import Table

from geoanalytics.cli.common import app, console

portfolio_app = typer.Typer(
    help="Виртуальный портфель (J1): позиции, риск, экспозиция.",
    invoke_without_command=True,
)
app.add_typer(portfolio_app, name="portfolio")

fundamentals_app = typer.Typer(help="Фундаменталка эмитентов из отчётов (H5).")
app.add_typer(fundamentals_app, name="fundamentals")

segments_app = typer.Typer(help="Сегменты выручки эмитента (L2: состав компании).")
app.add_typer(segments_app, name="segments")


@portfolio_app.callback()
def portfolio_main(ctx: typer.Context) -> None:
    """Без сабкоманды — полный отчёт по портфелю."""
    if ctx.invoked_subcommand is not None:
        return
    from geoanalytics.analytics.portfolio import portfolio_report
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        rep = portfolio_report(session)
    if rep.error:
        console.print(f"[yellow]{rep.error}[/]")
        return

    regime_str = f", режим рынка: [bold]{rep.regime}[/]" if rep.regime else ""
    console.print(f"Портфель: [bold]{rep.total_value_rub:,.0f} ₽[/]{regime_str}")

    table = Table(title="Позиции")
    table.add_column("Тикер")
    table.add_column("Кол-во", justify="right")
    table.add_column("Цена", justify="right")
    table.add_column("Стоимость, ₽", justify="right")
    table.add_column("Вес", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("β рынок", justify="right")
    table.add_column("Давл. 7д", justify="right")
    table.add_column("Сент. EMA", justify="right")
    for p in rep.positions:
        pnl = f"{p.pnl_pct:+.1f}%" if p.pnl_pct is not None else "—"
        beta = f"{p.betas['market']:+.2f}" if "market" in p.betas else "—"
        mom = f"{p.momentum:+.3f}" if p.momentum is not None else "—"
        note = f" [dim]({p.note})[/]" if p.note else ""
        table.add_row(
            f"{p.ticker}{note}", f"{p.quantity:g}",
            f"{p.last_close:.2f}" if p.last_close is not None else "—",
            f"{p.value_rub:,.0f}" if p.value_rub is not None else "—",
            f"{p.weight_pct:.1f}%" if p.weight_pct is not None else "—",
            pnl, beta, f"{p.pressure:.3f}", mom,
        )
    console.print(table)

    risk = Table(title=f"Риск (окно {rep.n_obs} торговых дней)")
    risk.add_column("Метрика")
    risk.add_column("Значение", justify="right")
    if rep.daily_vol_pct is not None:
        risk.add_row("Дневная волатильность", f"{rep.daily_vol_pct:.2f}%")
    if rep.var95_1d_pct is not None:
        risk.add_row("VaR 95% (1д)", f"{rep.var95_1d_pct:.2f}% "
                                     f"({rep.var95_1d_rub:,.0f} ₽)")
    if rep.var99_1d_pct is not None:
        risk.add_row("VaR 99% (1д), ориентир.", f"{rep.var99_1d_pct:.2f}%")
    if rep.max_drawdown_pct is not None:
        risk.add_row("Max просадка (окно)", f"{rep.max_drawdown_pct:.2f}%")
    if risk.row_count:
        console.print(risk)
    else:
        console.print("[yellow]Мало общей истории для риск-метрик.[/]")

    if rep.exposure:
        expo = Table(title="Факторная экспозиция (Σ вес·β)"
                     + (f", ср. R²={rep.avg_r2:.2f}" if rep.avg_r2 is not None else ""))
        expo.add_column("Фактор")
        expo.add_column("β портфеля", justify="right")
        for factor, beta in sorted(rep.exposure.items()):
            expo.add_row(factor, f"{beta:+.3f}")
        console.print(expo)

    if rep.correlations:
        corr = Table(title="Корреляции холдингов")
        corr.add_column("Пара")
        corr.add_column("ρ", justify="right")
        for (a, b), r in sorted(rep.correlations.items()):
            corr.add_row(f"{a} / {b}", f"{r:+.2f}")
        console.print(corr)


@portfolio_app.command("add")
def portfolio_add(
    ticker: str = typer.Argument(..., help="Тикер, напр. SBER."),
    quantity: float = typer.Argument(..., help="Количество (добавляется к позиции)."),
    price: float | None = typer.Option(None, "--price", help="Цена входа (для P&L)."),
) -> None:
    """Добавить/нарастить позицию портфеля."""
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import PortfolioRepository

    with session_scope() as session:
        try:
            pos = PortfolioRepository(session).upsert_position(ticker, quantity, price)
        except ValueError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(code=1) from exc
        if pos is None:
            console.print(f"[red]Актив {ticker.upper()} не найден в БД.[/]")
            raise typer.Exit(code=1)
        qty = pos.quantity
    console.print(f"[green]✓[/] {ticker.upper()}: позиция {qty:g}")


@portfolio_app.command("remove")
def portfolio_remove(
    ticker: str = typer.Argument(..., help="Тикер позиции."),
) -> None:
    """Удалить позицию из портфеля целиком."""
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import PortfolioRepository

    with session_scope() as session:
        removed = PortfolioRepository(session).remove_position(ticker)
    if removed:
        console.print(f"[green]✓[/] {ticker.upper()} удалён из портфеля.")
    else:
        console.print(f"[yellow]{ticker.upper()} в портфеле не было.[/]")


@portfolio_app.command("cash")
def portfolio_cash(
    currency: str = typer.Argument(None, help="Валюта (RUB/USD/EUR/CNY). Пусто — список."),
    amount: float = typer.Argument(None, help="Остаток (перезаписывает; 0/пусто — удалить)."),
) -> None:
    """Кэш/валютные балансы портфеля владельца: показать (без аргументов), задать или удалить.

    `geo portfolio cash` — список; `geo portfolio cash USD 1500` — задать; `geo portfolio cash
    USD 0` — удалить. Оценивается в ₽ по курсу ЦБ (входит в стоимость и снижает риск)."""
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.repositories import CashBalanceRepository

    with session_scope() as session:
        repo = CashBalanceRepository(session)
        if currency is None:
            balances = repo.list_balances()
            if not balances:
                console.print("[yellow]Кэш не задан. geo portfolio cash USD 1500[/]")
                return
            table = Table(title="Кэш/валюта")
            table.add_column("Валюта")
            table.add_column("Сумма", justify="right")
            for ccy, amt in balances:
                table.add_row(ccy, f"{amt:,.2f}")
            console.print(table)
            return
        if amount is None or amount <= 0:
            removed = repo.remove(currency)
            console.print(f"[green]✓[/] {currency.upper()} удалён." if removed
                          else f"[yellow]{currency.upper()} не было.[/]")
            return
        repo.set_balance(currency, amount)
    console.print(f"[green]✓[/] {currency.upper()}: остаток {amount:,.2f}")


@portfolio_app.command("snapshot")
def portfolio_snapshot() -> None:
    """Записать дневной снимок стоимости каждого портфеля (владелец + бот-юзеры).

    Обычно делается ежедневным джобом scheduler; здесь — ручной прогон/сидирование истории
    (графики «Стоимость во времени»/«P&L» переключаются на факт при ≥2 снимках)."""
    from geoanalytics.analytics.portfolio import snapshot_portfolios
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        n = snapshot_portfolios(session)
    console.print(f"[green]✓[/] Снимки записаны: портфелей {n}")


@fundamentals_app.command("add")
def fundamentals_add(
    ticker: str = typer.Argument(..., help="Тикер актива (SBER)."),
    path: str = typer.Argument(..., help="PDF-отчёт или .txt с текстом отчёта."),
    period: str = typer.Option(None, "--period", "-p",
                               help="Период (2024 / 2024-H1); без него — автодетект."),
    source: str = typer.Option("pdf", "--source", help="Метка источника."),
) -> None:
    """Разобрать отчёт эмитента (PDF/текст) → фундаментальные метрики в карточку актива.

    `geo fundamentals add SBER report.pdf` — извлечёт выручку/прибыль/EBITDA/… (rule-based,
    precision-first) и сохранит. Идемпотентно (повторный разбор того же периода перезапишет)."""
    from geoanalytics.analytics.fundamentals import (
        ingest_fundamentals,
        read_source_text,
    )
    from geoanalytics.storage.db import session_scope

    try:
        text = read_source_text(path)
    except (FileNotFoundError, RuntimeError) as exc:
        console.print(f"[red]Ошибка чтения[/]: {exc}")
        raise typer.Exit(1) from exc
    with session_scope() as session:
        res = ingest_fundamentals(session, ticker, text, period=period, source=source)
    if not res.found:
        console.print(f"[yellow]{res.note}[/]")
        raise typer.Exit(1)
    if not res.stored:
        console.print("[yellow]Метрик не извлечено (нет узнаваемых меток/единиц в тексте).[/]")
        return
    console.print(f"[green]✓[/] {res.ticker}: извлечено метрик {res.stored}")
    fundamentals_list(res.ticker)


@fundamentals_app.command("scrape")
def fundamentals_scrape(
    ticker: str = typer.Argument(None, help="Тикер (SBER); без него — нужен --all."),
    all_shares: bool = typer.Option(False, "--all", help="Все акции из справочника."),
    delay: float = typer.Option(1.0, "--delay", help="Пауза между запросами, сек (вежливость)."),
) -> None:
    """Скрейп годовой отчётности МСФО с smart-lab.ru → метрики по годам в карточку актива.

    `geo fundamentals scrape SBER` — один тикер; `geo fundamentals scrape --all` — все акции.
    Многопериодно и идемпотентно (source=smartlab). Источник хрупкий: при сбое тикер
    пропускается без падения."""
    import time

    from sqlalchemy import select

    from geoanalytics.analytics.fundamentals import scrape_fundamentals
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    if not ticker and not all_shares:
        console.print("[yellow]Укажите тикер или --all.[/]")
        raise typer.Exit(1)
    with session_scope() as session:
        if all_shares:
            tickers = list(session.scalars(
                select(Asset.ticker).where(Asset.kind == "share").order_by(Asset.ticker)))
        else:
            tickers = [ticker.upper()]
        table = Table(title="Скрейп фундаменталки (smart-lab)")
        table.add_column("Тикер")
        table.add_column("Метрик", justify="right")
        table.add_column("Статус")
        total = 0
        for i, tk in enumerate(tickers):
            res = scrape_fundamentals(session, tk)
            total += res.stored
            status = "[green]ок[/]" if res.stored else f"[yellow]{res.note or 'пусто'}[/]"
            table.add_row(tk, str(res.stored), status)
            if delay and i < len(tickers) - 1:
                time.sleep(delay)
    console.print(table)
    console.print(f"Всего метрик сохранено: [bold]{total}[/] по {len(tickers)} тикерам.")


@fundamentals_app.command("list")
def fundamentals_list(
    ticker: str = typer.Argument(..., help="Тикер актива."),
) -> None:
    """Показать сохранённые фундаментальные метрики актива (свежие по каждой)."""
    from sqlalchemy import select

    from geoanalytics.analytics.fundamentals import fundamentals_for_asset
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    with session_scope() as session:
        asset = session.scalars(
            select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            console.print(f"[yellow]Актив {ticker.upper()} не найден.[/]")
            raise typer.Exit(1)
        rows = fundamentals_for_asset(session, asset.id)
    if not rows:
        console.print(f"[yellow]Нет фундаментальных метрик для {ticker.upper()}. "
                      f"Загрузите: geo fundamentals add {ticker.upper()} отчёт.pdf[/]")
        return
    table = Table(title=f"Фундаменталка — {ticker.upper()}")
    table.add_column("Метрика")
    table.add_column("Значение", justify="right")
    table.add_column("Период")
    for r in rows:
        table.add_row(r["label"], r["display"], r["period"] or "—")
    console.print(table)


@segments_app.command("add")
def segments_add(
    ticker: str = typer.Argument(..., help="Тикер актива (SBER)."),
    segment: str = typer.Option(..., "--segment", "-s", help="Название сегмента."),
    value: float = typer.Option(..., "--value", "-v", help="Выручка сегмента, ₽."),
    share: float = typer.Option(None, "--share", help="Доля сегмента в выручке, %."),
    period: str = typer.Option(None, "--period", "-p", help="Период (2024)."),
    source: str = typer.Option("manual", "--source", help="Метка источника."),
) -> None:
    """Добавить/обновить сегмент выручки эмитента (идемпотентно по компании/сегменту/периоду).

    `geo segments add MTSS --segment "Связь" --value 500000000000 --share 70 --period 2024`."""
    from sqlalchemy import select

    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset
    from geoanalytics.storage.repositories import RevenueSegmentRepository

    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            console.print(f"[yellow]Актив {ticker.upper()} не найден.[/]")
            raise typer.Exit(1)
        if asset.company_id is None:
            console.print(f"[yellow]У актива {ticker.upper()} нет привязанной компании.[/]")
            raise typer.Exit(1)
        RevenueSegmentRepository(session).upsert(
            asset.company_id, segment, value, share=share, period=period, source=source)
    console.print(f"[green]✓[/] {ticker.upper()}: сегмент «{segment}» сохранён")
    segments_list(ticker)


@segments_app.command("list")
def segments_list(
    ticker: str = typer.Argument(..., help="Тикер актива."),
) -> None:
    """Показать сегменты выручки эмитента за свежайший период."""
    from sqlalchemy import select

    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset
    from geoanalytics.storage.repositories import RevenueSegmentRepository

    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None or asset.company_id is None:
            console.print(f"[yellow]Актив {ticker.upper()} не найден или без компании.[/]")
            raise typer.Exit(1)
        rows = RevenueSegmentRepository(session).for_company(asset.company_id)
        data = [(r.segment, r.value, r.share, r.period) for r in rows]
    if not data:
        console.print(f"[yellow]Нет сегментов для {ticker.upper()}. "
                      f"Добавьте: geo segments add {ticker.upper()} --segment … --value …[/]")
        return
    table = Table(title=f"Сегменты выручки — {ticker.upper()}")
    table.add_column("Сегмент")
    table.add_column("Выручка, ₽", justify="right")
    table.add_column("Доля", justify="right")
    table.add_column("Период")
    for seg, val, sh, per in data:
        table.add_row(seg, f"{val:,.0f}".replace(",", " "),
                      f"{sh:.0f}%" if sh is not None else "—", per or "—")
    console.print(table)
