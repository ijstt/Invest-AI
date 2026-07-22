"""Команды рыночной аналитики: отчеты по активам, факторы, сентимент и сценарии."""

from __future__ import annotations

import typer
from rich.panel import Panel
from rich.table import Table

from geoanalytics.cli.common import _rich_link, app, console


@app.command()
def attribution(
    ticker: str = typer.Argument(..., help="Тикер актива."),
    day: str | None = typer.Option(None, "--day", "-d",
                                   help="День разложения YYYY-MM-DD (дефолт — последний)."),
    window: int = typer.Option(250, "--window", "-w", help="Окно оценки бет, торговых дней."),
) -> None:
    """Факторная атрибуция (G3): рынок/сектор/FX/Brent → идиосинкразия."""
    from datetime import date as _date

    from geoanalytics.analytics.attribution import attribute_asset
    from geoanalytics.storage.db import session_scope

    target = _date.fromisoformat(day) if day else None
    with session_scope() as session:
        r = attribute_asset(session, ticker, day=target, window=window)
    if r.error:
        console.print(f"[yellow]{r.ticker}: {r.error}[/]")
        return
    console.print(
        f"{r.ticker} {r.day}: доходность [bold]{r.asset_return_pct:+.2f}%[/] "
        f"(R²={r.r2:.2f}, n={r.n_obs})"
    )
    table = Table(title="Разложение дня")
    table.add_column("Фактор")
    table.add_column("β", justify="right")
    table.add_column("Вклад, %", justify="right")
    for name in r.betas:
        table.add_row(name, f"{r.betas[name]:+.3f}",
                      f"{r.contributions_pct[name]:+.2f}")
    table.add_row("α (дневная)", "—", f"{r.alpha_pct:+.2f}")
    table.add_row("[bold]идиосинкразия[/]", "—", f"[bold]{r.idio_pct:+.2f}[/]")
    console.print(table)


@app.command()
def graph(
    ticker: str = typer.Argument(..., help="Тикер актива."),
    hours: int = typer.Option(168, "--hours", "-h", help="Окно событий соседей, часов."),
) -> None:
    """Граф связей эмитента (G7): соседи + косвенные влияния их событий."""
    from sqlalchemy import select

    from geoanalytics.analytics.graph_impact import (
        _neighbors,
        graph_impacts_for_asset,
    )
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    with session_scope() as session:
        asset = session.scalars(
            select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            console.print(f"[yellow]Актив {ticker.upper()} не найден[/]")
            return
        neighbors = _neighbors(session, asset.id)
        id_to_ticker = {a.id: a.ticker for a in session.scalars(
            select(Asset).where(Asset.id.in_(list(neighbors) or [0])))}
        impacts = graph_impacts_for_asset(session, asset.id, hours=hours)

    if not neighbors:
        console.print(f"[yellow]У {asset.ticker} нет рёбер графа[/]")
        return
    rel_table = Table(title=f"Связи {asset.ticker}")
    rel_table.add_column("Сосед")
    rel_table.add_column("Связь")
    rel_table.add_column("Вес", justify="right")
    for nid, (_pred, label, weight) in sorted(
            neighbors.items(), key=lambda kv: -kv[1][2]):
        rel_table.add_row(id_to_ticker.get(nid, str(nid)), label, f"{weight:.2f}")
    console.print(rel_table)

    if impacts:
        arrow = {"positive": "[green]▲[/]", "negative": "[red]▼[/]", "neutral": "[dim]■[/]"}
        lines = [f"{arrow.get(g.direction, ' ')} {g.via_ticker} ({g.relation}): "
                 f"{g.title} (сила {g.magnitude})" for g in impacts]
        console.print(Panel("\n".join(lines),
                            title=f"Косвенные влияния за {hours} ч"))
    else:
        console.print("[dim]Значимых событий у соседей за окно нет.[/]")


@app.command()
def regime(
    states: int = typer.Option(3, "--states", "-s", help="Число режимов HMM (2 или 3)."),
    persist: bool = typer.Option(False, "--persist",
                                 help="L5: записать историю режимов в market_regimes."),
) -> None:
    """Режимы рынка (G2): HMM по волатильности IMOEX и USD/RUB."""
    from geoanalytics.analytics.regimes import market_regimes
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        r = market_regimes(session, n_states=states)
        if persist and not r.error:
            from geoanalytics.analytics.regimes import record_regimes
            days = record_regimes(session, n_states=states)
            console.print(f"[green]История режимов записана[/]: дней {days}.")
    if r.error:
        console.print(f"[yellow]{r.error}[/]")
        return
    console.print(
        f"Текущий режим: [bold]{r.current}[/] (с {r.current_since}), "
        f"история {len(r.dates)} торговых дней"
    )
    table = Table(title="Состояния")
    table.add_column("Режим")
    table.add_column("Доля дней", justify="right")
    table.add_column("Ср. vol IMOEX, %/день", justify="right")
    for name in r.labels:
        table.add_row(name, f"{r.state_share[name]:.0%}", f"{r.state_vol[name]:.2f}")
    console.print(table)


@app.command()
def pressure(
    ticker: str = typer.Argument(..., help="Тикер актива."),
    window: int = typer.Option(7, "--window", "-w", help="Окно анализа, дней."),
) -> None:
    """Индекс новостного давления (G5): Σ значимости салиентных новостей / окно."""
    from sqlalchemy import select

    from geoanalytics.analytics.pressure import news_pressure
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            console.print(f"[red]Актив {ticker.upper()} не найден.[/]")
            raise typer.Exit(code=1)
        value = news_pressure(session, asset.id, window=window)
    console.print(
        f"{ticker.upper()} давление за {window}д: [bold]{value:.4f}[/] "
        f"(Σ sig / {window})"
    )


@app.command("sentiment-trend")
def sentiment_trend(
    ticker: str = typer.Argument(..., help="Тикер актива."),
    days: int = typer.Option(60, "--days", "-d", help="Глубина истории, дней."),
    span: int = typer.Option(14, "--span", help="Период EWMA."),
    last: int = typer.Option(10, "--last", "-n", help="Показать последних N точек."),
) -> None:
    """Тональный моментум (G6): EWMA суточного сентимента по активу."""
    from sqlalchemy import select

    from geoanalytics.analytics.sentiment_trend import sentiment_momentum
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    with session_scope() as session:
        asset = session.scalars(select(Asset).where(Asset.ticker == ticker.upper())).first()
        if asset is None:
            console.print(f"[red]Актив {ticker.upper()} не найден.[/]")
            raise typer.Exit(code=1)
        series = sentiment_momentum(session, asset.id, days=days, span=span)
    if not series:
        console.print(f"[yellow]{ticker.upper()}: недостаточно данных сентимента.[/]")
        return
    table = Table(title=f"{ticker.upper()} тональный моментум EWMA-{span}")
    table.add_column("Дата")
    table.add_column("EMA сентимента", justify="right")
    for d, v in series[-last:]:
        color = "green" if v > 0.05 else "red" if v < -0.05 else "white"
        table.add_row(str(d), f"[{color}]{v:+.4f}[/]")
    console.print(table)
    console.print(f"Последнее значение: [bold]{series[-1][1]:+.4f}[/]")


@app.command()
def backfill(
    ticker: str | None = typer.Option(
        None, "--ticker", "-t", help="Тикер; без него — все из справочника."),
    days: int | None = typer.Option(None, "--days", "-d", help="Глубина истории, дней."),
    fx: bool = typer.Option(False, "--fx",
                            help="История курсов ЦБ (USD/EUR/CNY) вместо котировок."),
    metals: bool = typer.Option(False, "--metals",
                                help="История учётных цен металлов ЦБ "
                                     "(золото/серебро/платина/палладий, ₽/г)."),
    brent: bool = typer.Option(False, "--brent",
                               help="История нефти Brent из FRED (DCOILBRENTEU) — "
                                    "активирует brent-фактор атрибуции/whatif."),
) -> None:
    """Загрузить историю дневных котировок с MOEX (для индикаторов)."""
    if brent:
        from geoanalytics.analytics.history import backfill_fred_brent

        br = backfill_fred_brent(days=days)
        if br.error:
            console.print(f"[red]Ошибка: {br.error}[/]")
            raise typer.Exit(1)
        console.print(f"Brent (FRED): загружено точек — {br.points}")
        return
    if metals:
        from geoanalytics.analytics.history import backfill_metals

        mr = backfill_metals(days=days)
        if mr.error:
            console.print(f"[red]Ошибка: {mr.error}[/]")
            raise typer.Exit(1)
        console.print(f"Учётные цены металлов ЦБ: загружено точек — {mr.points}")
        return
    if fx:
        from geoanalytics.analytics.history import backfill_fx

        fx_table = Table(title="Загрузка истории курсов ЦБ")
        fx_table.add_column("Валюта")
        fx_table.add_column("Точек", justify="right")
        fx_table.add_column("Ошибка")
        for fr in backfill_fx(days=days):
            fx_table.add_row(fr.currency, str(fr.points), fr.error or "—")
        console.print(fx_table)
        return
    from geoanalytics.analytics.history import backfill_all, backfill_asset

    results = [backfill_asset(ticker, days)] if ticker else backfill_all(days)
    table = Table(title="Загрузка истории")
    table.add_column("Тикер")
    table.add_column("Свечей", justify="right")
    table.add_column("Ошибка")
    for r in results:
        table.add_row(r.ticker, str(r.candles), r.error or "—")
    console.print(table)


@app.command()
def context(
    ticker: str = typer.Argument(..., help="Тикер для пересборки контекста."),
    no_llm: bool = typer.Option(False, "--no-llm", help="Не вызывать LLM."),
) -> None:
    """Пересобрать и показать контекст актива."""
    from geoanalytics.context.asset_context import build_context

    ctx = build_context(ticker, use_llm=not no_llm)
    if ctx is None:
        console.print(f"[yellow]Актив {ticker.upper()} не найден[/]")
        return
    console.print(Panel(ctx["narrative"], title=f"Контекст {ticker.upper()} (v{ctx['version']})"))


@app.command()
def asset(
    ticker: str = typer.Argument(..., help="Тикер, напр. SBER"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Не вызывать LLM."),
    no_rebuild: bool = typer.Option(False, "--no-rebuild", help="Не пересобирать контекст."),
    period: str = typer.Option(
        "D", "--period", "-p",
        help="Таймфрейм индикаторов: D (дни) | W (недели) | M (месяцы)."
    ),
) -> None:
    """Аналитика по конкретному активу: индикаторы, макро, факторы, новости, контекст."""
    from geoanalytics.query.asset_report import build_report

    rep = build_report(ticker, rebuild=not no_rebuild, use_llm=not no_llm,
                       period=period.upper())
    if not rep.found:
        console.print(Panel(rep.note, title=f"{rep.ticker}", style="yellow"))
        return

    header = f"{rep.name} ({rep.ticker})" + (f" — {rep.sector}" if rep.sector else "")
    if rep.narrative:
        console.print(Panel(rep.narrative, title=header))
    else:
        console.print(Panel(header, title="Актив"))

    # Индикаторы.
    ind = rep.indicators
    if ind:
        _tf = {"D": "дни", "W": "недели", "M": "месяцы"}.get(period.upper(), period)
        table = Table(title=f"Технические индикаторы ({_tf})")
        table.add_column("Показатель")
        table.add_column("Значение", justify="right")
        labels = {
            "last": "Цена", "trend": "Тренд", "rsi14": "RSI(14)",
            "stoch_k": "Стох. %K", "stoch_d": "Стох. %D", "atr14": "ATR(14)",
            "macd": "MACD", "macd_signal": "MACD сигнал", "macd_hist": "MACD гист.",
            "boll_lower": "Bollinger ↓", "boll_mid": "Bollinger сред.", "boll_upper": "Bollinger ↑",
            "sma50": "SMA50", "sma200": "SMA200", "vol_annual": "Волат., %год",
            "obv": "OBV", "vol_sma20": "Ср. объём 20", "vol_ratio": "Объём ×ср.",
            "ret_1w": "Доход. 1н, %", "ret_1m": "Доход. 1м, %", "ret_3m": "Доход. 3м, %",
            "high_52w": "Макс 52н", "low_52w": "Мин 52н",
            "pct_from_52w_high": "% от макс 52н", "pct_from_52w_low": "% от мин 52н",
        }
        for key, label in labels.items():
            if key in ind:
                table.add_row(label, str(ind[key]))
        console.print(table)
    else:
        console.print("[yellow]Нет истории котировок. Загрузите: "
                      f"geo backfill -t {rep.ticker}[/]")

    # Макро и факторы.
    macro_lines = []
    if rep.macro.get("key_rate") is not None:
        macro_lines.append(f"Ключевая ставка: {rep.macro['key_rate']}%")
    for cur, val in (rep.macro.get("fx") or {}).items():
        macro_lines.append(f"{cur}/RUB: {val:.2f}")
    if rep.factors.get("macro_factors"):
        macro_lines.append("Драйверы: " + ", ".join(rep.factors["macro_factors"]))
    if rep.factors.get("peers"):
        macro_lines.append("Пиры: " + ", ".join(rep.factors["peers"]))
    if macro_lines:
        console.print(Panel("\n".join(macro_lines), title="Макро и факторы"))

    # Корреляции актив ↔ факторы.
    if rep.correlations:
        names = {"usd_rub": "USD/RUB", "usd_eur": "USD/EUR", "brent": "Нефть Brent",
                 "gold": "Золото ₽/г", "silver": "Серебро ₽/г",
                 "platinum": "Платина ₽/г", "palladium": "Палладий ₽/г",
                 "sector_peers": "Сектор-пиры"}
        lines = [f"{names.get(k, k)}: [bold]{v:+.2f}[/]" for k, v in rep.correlations.items()]
        console.print(Panel("\n".join(lines), title="Корреляции (дн. доходности)"))

    # F5: дивиденд из новостей + доходность к текущей цене.
    if rep.dividend:
        d = rep.dividend
        when = d["published_at"].strftime("%d.%m.%Y") if d.get("published_at") else "?"
        line = f"Дивиденд (из новостей {when}): {d['value']:g} ₽/акция"
        if d.get("yield_pct") is not None:
            line += f" ≈ {d['yield_pct']}% доходности к текущей цене"
        console.print(Panel(line, title="Дивиденды"))

    # G5/G6: новостное давление и тональный моментум.
    news_lines = []
    if rep.news_pressure_7d is not None:
        news_lines.append(f"Давление 7д: [bold]{rep.news_pressure_7d:.4f}[/] (Σ sig / 7)")
    if rep.sentiment_ema_14d is not None:
        color = ("green" if rep.sentiment_ema_14d > 0.05
                 else "red" if rep.sentiment_ema_14d < -0.05 else "white")
        news_lines.append(f"Тональный моментум EWMA-14: [{color}]{rep.sentiment_ema_14d:+.4f}[/]")
    if news_lines:
        console.print(Panel("\n".join(news_lines), title="Новостной фон"))

    # Топ-события влияния.
    if rep.events:
        arrow = {"positive": "[green]▲[/]", "negative": "[red]▼[/]", "neutral": "[dim]■[/]"}
        lines = [f"{arrow.get(e['direction'], ' ')} [{e['type']}] "
                 f"{_rich_link(e['title'], e.get('url'))} (сила {e['magnitude']})"
                 for e in rep.events]
        console.print(Panel("\n".join(lines), title="События влияния"))

    # G7: косвенные влияния через граф связей (события соседей-эмитентов).
    if rep.graph_impacts:
        arrow = {"positive": "[green]▲[/]", "negative": "[red]▼[/]", "neutral": "[dim]■[/]"}
        lines = [f"{arrow.get(g['direction'], ' ')} {g['via']} ({g['relation']}): "
                 f"{_rich_link(g['title'], g.get('url'))} (сила {g['magnitude']})"
                 for e in rep.graph_impacts]
        console.print(Panel("\n".join(lines), title="Косвенно через граф связей"))

    # Новости.
    if rep.news:
        marker = {"positive": "[green]▲[/]", "negative": "[red]▼[/]", "neutral": "[dim]■[/]"}
        lines = [f"{marker.get(n.get('sentiment'), ' ')} {_rich_link(n['title'], n.get('url'))}"
                 for n in rep.news[:10]]
        console.print(Panel("\n".join(lines), title="Связанные новости"))

    if rep.note:
        console.print(f"[yellow]{rep.note}[/]")


@app.command("asset-context-accumulate")
def asset_context_accumulate(
    use_llm: bool = typer.Option(False, "--llm",
                                 help="Генерить нарратив через Ollama (по умолчанию — шаблон)."),
) -> None:
    """L5: накопить средне-долгосрочный нарратив (AssetContext) по всем акциям.

    Прогоняет `build_context` по каждой акции и копит версии в asset_context. По умолчанию без
    Ollama (шаблонный нарратив, батч не конкурирует за GPU); `--llm` — с ИИ-разбором."""
    from sqlalchemy import select

    from geoanalytics.context.asset_context import build_context
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    with session_scope() as session:
        tickers = list(session.scalars(
            select(Asset.ticker).where(Asset.kind == "share").order_by(Asset.ticker)))
    built = 0
    with console.status(f"Накопление контекста по {len(tickers)} акциям…"):
        for tk in tickers:
            try:
                if build_context(tk, use_llm=use_llm) is not None:
                    built += 1
            except Exception as exc:  # noqa: BLE001 — один тикер не должен ронять батч
                console.print(f"[yellow]{tk}: {exc}[/]")
    console.print(f"[green]Контекст накоплен[/]: {built}/{len(tickers)} акций.")


@app.command("factor-scores")
def factor_scores(
    show: str = typer.Option(None, "--show", "-s", help="Показать факторы тикера (без пересчёта)."),
) -> None:
    """L3: пересчитать кросс-секционные факторы (value/quality/growth/композит) по вселенной акций.

    Без аргументов — считает срез за сегодня, пишет в `factor_scores` и печатает топ по композиту.
    `--show SBER` — только показать свежие факторные ранги тикера."""
    from sqlalchemy import select

    from geoanalytics.analytics.factor_model import (
        factor_scores_for_asset,
        record_factor_scores,
    )
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset

    if show:
        with session_scope() as session:
            asset = session.scalars(select(Asset).where(Asset.ticker == show.upper())).first()
            if asset is None:
                console.print(f"[yellow]Актив {show.upper()} не найден.[/]")
                raise typer.Exit(1)
            ranks = factor_scores_for_asset(session, asset.id)
        if not ranks:
            console.print(f"[yellow]Нет факторных скоров для {show.upper()}. "
                          f"Сначала: geo factor-scores[/]")
            return
        table = Table(title=f"Факторные ранги — {show.upper()}")
        table.add_column("Фактор")
        table.add_column("z-скор", justify="right")
        table.add_column("Перцентиль", justify="right")
        for r in ranks:
            table.add_row(r["label"], f"{r['zscore']:+.2f}σ",
                          f"{r['percentile']:.0f}" if r["percentile"] is not None else "—")
        console.print(table)
        return

    with session_scope() as session:
        n = record_factor_scores(session)
        # топ-5 по композиту за сегодняшний срез
        from geoanalytics.storage.models import FactorScore
        top = session.execute(
            select(Asset.ticker, FactorScore.zscore, FactorScore.percentile)
            .join(FactorScore, FactorScore.asset_id == Asset.id)
            .where(FactorScore.factor == "composite")
            .order_by(FactorScore.zscore.desc()).limit(5)
        ).all()
    console.print(f"[green]Факторный срез записан[/]: строк {n}.")
    if top:
        table = Table(title="Топ по композиту")
        table.add_column("Тикер")
        table.add_column("Композит z", justify="right")
        table.add_column("Перцентиль", justify="right")
        for tk, z, pct in top:
            table.add_row(tk, f"{z:+.2f}σ", f"{pct:.0f}" if pct is not None else "—")
        console.print(table)


@app.command()
def candles(
    ticker: str = typer.Argument(..., help="Тикер актива."),
    days: int = typer.Option(90, "--days", "-n", help="Окно последних баров."),
    trend: int = typer.Option(10, "--trend",
                              help="Окно SMA для контекста тренда."),
) -> None:
    """Свечные паттерны Нисона по дневным барам (детекторы + контекст тренда).

    Доджи и перевёрнутый молот — информационные (в торговый сигнал стратегии
    `candles` не входят); проверка прибыльности — `geo walkforward -S candles`."""
    from geoanalytics.analytics.candlesticks import patterns_for_asset

    hits = patterns_for_asset(ticker, days=days, trend=trend)
    if hits is None:
        console.print(f"[red]Актив {ticker.upper()} не найден.[/]")
        raise typer.Exit(code=1)
    if not hits:
        console.print(f"За последние {days} баров паттернов не найдено.")
        return
    table = Table(title=f"Свечные паттерны {ticker.upper()} (посл. {days} баров)")
    table.add_column("Дата")
    table.add_column("Паттерн")
    table.add_column("Сигнал")
    arrows = {1: "[green]бычий ↑[/]", -1: "[red]медвежий ↓[/]", 0: "[dim]нейтрален[/]"}
    for day, hit in hits:
        table.add_row(day.strftime("%d.%m.%Y"), hit.name, arrows[hit.direction])
    console.print(table)


@app.command()
def whatif(
    market: float | None = typer.Option(None, "--market",
                                        help="Шок IMOEX, % (напр. -5)."),
    usd: float | None = typer.Option(None, "--usd", help="Шок USD/RUB, %."),
    brent: float | None = typer.Option(None, "--brent", help="Шок Brent, %."),
    gold: float | None = typer.Option(None, "--gold",
                                      help="Шок золота (учётная цена ЦБ, ₽/г), %."),
    ticker: str | None = typer.Option(None, "--ticker", "-t",
                                      help="Один актив вместо портфеля."),
    window: int = typer.Option(250, "--window", "-w", help="Окно оценки бет."),
) -> None:
    """Сценарий «что-если» (J4): портфель при шоках факторов.

    Ставочный сценарий выражайте через --market/--usd: ставка не фактор
    атрибуции (решений ЦБ ~8/год — регрессии не на чем учиться)."""
    from geoanalytics.analytics.whatif import whatif_asset, whatif_portfolio
    from geoanalytics.storage.db import session_scope

    shocks: dict[str, float] = {}
    if market is not None:
        shocks["market"] = market
    if usd is not None:
        shocks["usd_rub"] = usd
    if brent is not None:
        shocks["brent"] = brent
    if gold is not None:
        shocks["gold"] = gold
    if not shocks:
        console.print("[red]Задайте хотя бы один шок: "
                      "--market / --usd / --brent / --gold.[/]")
        raise typer.Exit(code=1)

    with session_scope() as session:
        r = (whatif_asset(session, ticker, shocks, window=window) if ticker
             else whatif_portfolio(session, shocks, window=window))
    if r.error:
        console.print(f"[yellow]{r.error}[/]")
        raise typer.Exit(code=1)

    shocks_str = ", ".join(f"{f} {v:+g}%" for f, v in r.shocks_pct.items())
    console.print(f"Сценарий: [bold]{shocks_str}[/]")
    table = Table(title="Ожидаемая реакция")
    table.add_column("Тикер")
    factors = sorted({f for a in r.assets for f in a.contributions_pct})
    for f in factors:
        table.add_column(f, justify="right")
    table.add_column("Итог", justify="right")
    table.add_column("R²", justify="right")
    for a in sorted(r.assets, key=lambda x: x.expected_move_pct):
        color = "red" if a.expected_move_pct < 0 else "green"
        table.add_row(
            a.ticker,
            *(f"{a.contributions_pct[f]:+.2f}%" if f in a.contributions_pct
              else "—" for f in factors),
            f"[{color}]{a.expected_move_pct:+.2f}%[/]", f"{a.r2:.2f}",
        )
    console.print(table)
    if r.portfolio_move_pct is not None:
        color = "red" if r.portfolio_move_pct < 0 else "green"
        console.print(
            f"Портфель ({r.total_value_rub:,.0f} ₽): "
            f"[bold {color}]{r.portfolio_move_pct:+.2f}%[/] = "
            f"[bold {color}]{r.portfolio_pnl_rub:+,.0f} ₽[/]"
        )
    console.print(Panel("\n".join(f"• {c}" for c in r.caveats),
                        title="Оговорки", border_style="yellow"))
