"""Команды NLP-аналитики: сводки, дайджесты, сюжеты, аудиты, события и оценщики."""

from __future__ import annotations

import typer
from rich.panel import Panel
from rich.table import Table

from geoanalytics.cli.common import app, console


@app.command()
def news(
    no_llm: bool = typer.Option(False, "--no-llm", help="Не вызывать LLM-сводку."),
    hours: int = typer.Option(24, "--hours", "-h", help="Окно новостей в часах."),
) -> None:
    """Сводка «что по новостям»: ставка, курсы, топ-движения, тональность, заголовки."""
    from geoanalytics.query.news_summary import build_snapshot

    snap = build_snapshot(hours=hours, use_llm=not no_llm)

    # LLM-сводка (если доступна).
    if snap.llm_summary:
        console.print(Panel(snap.llm_summary, title="Сводка"))

    # Макро-блок.
    macro_lines = []
    if snap.key_rate is not None:
        macro_lines.append(f"Ключевая ставка ЦБ: [bold]{snap.key_rate}%[/] ({snap.key_rate_date})")
    for cur, val in snap.fx.items():
        if val is not None:
            macro_lines.append(f"{cur}/RUB: [bold]{val:.2f}[/]")
    console.print(Panel("\n".join(macro_lines) or "нет макро-данных", title="Макро"))

    # Тональность и темы.
    if snap.sentiment_breakdown or snap.top_events:
        sb = snap.sentiment_breakdown
        mood = (f"[green]+{sb.get('positive', 0)}[/] / "
                f"[dim]={sb.get('neutral', 0)}[/] / "
                f"[red]-{sb.get('negative', 0)}[/]")
        events = ", ".join(f"{e} ({n})" for e, n in snap.top_events) or "—"
        console.print(Panel(f"Тональность (поз/нейтр/нег): {mood}\nТемы: {events}",
                            title="Настроение рынка"))

    # Топ-движения.
    if snap.top_gainers or snap.top_losers:
        table = Table(title="Топ-движения (TQBR)")
        table.add_column("Тикер")
        table.add_column("Изм. %", justify="right")
        table.add_column("Цена", justify="right")
        for m in snap.top_gainers:
            table.add_row(f"[green]{m['ticker']}[/]", f"{m['change_pct']:+.2f}", str(m["last"]))
        for m in snap.top_losers:
            table.add_row(f"[red]{m['ticker']}[/]", f"{m['change_pct']:+.2f}", str(m["last"]))
        console.print(table)

    # Заголовки с маркером тональности.
    if snap.headlines:
        marker = {"positive": "[green]▲[/]", "negative": "[red]▼[/]", "neutral": "[dim]■[/]"}
        lines = [f"{marker.get(h.get('sentiment'), ' ')} {h['title']}" for h in snap.headlines]
        console.print(Panel("\n".join(lines), title="Свежие заголовки"))

    if not (macro_lines or snap.top_gainers or snap.headlines):
        console.print("[yellow]Данных пока нет. Сначала: geo pipeline[/]")


@app.command()
def digest(
    send: bool = typer.Option(False, "--send", help="Отправить в Telegram (дедуп по дате)."),
    hours: int = typer.Option(24, "--hours", "-h", help="Окно новостей в часах."),
) -> None:
    """J2: ежедневный дайджест — макро, движения, фон по активам, события, новости."""
    from geoanalytics.query.digest import build_digest_text, send_daily_digest

    if send:
        sent = send_daily_digest(hours=hours)
        console.print("[green]Дайджест отправлен.[/]" if sent
                      else "[yellow]Дайджест уже отправлен сегодня (или нет каналов).[/]")
    else:
        console.print(build_digest_text(hours=hours))


@app.command()
def forecasts(
    ticker: str | None = typer.Option(None, "--ticker", "-t", help="Фильтр по тикеру."),
    limit: int = typer.Option(20, "--limit", "-n", help="Сколько прогнозов показать."),
) -> None:
    """F10: прогнозы брокеров (целевая цена/дивиденд) по активам."""
    from sqlalchemy import select

    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Asset
    from geoanalytics.storage.repositories import ForecastRepository

    with session_scope() as session:
        asset_id = None
        if ticker:
            asset = session.scalars(
                select(Asset).where(Asset.ticker == ticker.upper())
            ).first()
            if asset is None:
                console.print(f"[red]Актив {ticker.upper()} не найден[/]")
                raise typer.Exit(1)
            asset_id = asset.id
        rows = ForecastRepository(session).list_forecasts(asset_id=asset_id, limit=limit)
        if not rows:
            console.print("[yellow]Прогнозов нет[/] (запустите `geo reforecast`)")
            return
        table = Table(title="Прогнозы брокеров (F10)")
        for col in ("Тикер", "Вид", "Значение", "Горизонт", "Канал", "Добавлен"):
            table.add_column(col)
        for f, a in rows:
            table.add_row(
                a.ticker, f.kind, f"{f.value:g} {f.unit}",
                f.target_date.isoformat() if f.target_date else "—",
                f.source_channel or "—",
                f.created_at.date().isoformat() if f.created_at else "—",
            )
        console.print(table)


@app.command()
def stories(
    assign: bool = typer.Option(False, "--assign", help="Прогнать кластеризацию (backfill)."),
    hours: int = typer.Option(48, "--hours", "-h", help="Окно для топа сюжетов, часов."),
    limit: int = typer.Option(15, "--limit", "-n", help="Сколько сюжетов показать."),
) -> None:
    """Сюжеты (F6): кластеры статей об одном событии; топ за окно."""
    from geoanalytics.context.stories import assign_stories, top_stories

    if assign:
        r = assign_stories()
        console.print(
            f"[green]Кластеризация[/]: к сюжетам={r.assigned}, новых сюжетов={r.created}, "
            f"ошибки={r.errors}"
        )
    table = Table(title=f"Топ сюжетов за {hours} ч")
    table.add_column("ID", justify="right")
    table.add_column("Статей", justify="right")
    table.add_column("Сюжет")
    table.add_column("Последняя", justify="right")
    for s in top_stories(hours=hours, limit=limit):
        last = s["last_seen_at"].strftime("%m-%d %H:%M") if s["last_seen_at"] else "—"
        table.add_row(str(s["id"]), str(s["n_articles"]), s["title"][:80], last)
    console.print(table)


@app.command()
def calendar(
    sync: bool = typer.Option(False, "--sync",
                              help="Затянуть график ЦБ, отсечки MOEX и smart-lab."),
    days: int = typer.Option(30, "--days", "-d", help="Горизонт списка, дней вперёд."),
) -> None:
    """Календарь событий (H2): заседания ЦБ по ставке, дивидендные отсечки."""
    from geoanalytics.context.calendar import sync_calendar, upcoming_events
    from geoanalytics.storage.db import session_scope

    if sync:
        r = sync_calendar()
        console.print(
            f"[green]Синк календаря[/]: заседаний ЦБ={r.cbr}, отсечек MOEX={r.dividends}, "
            f"будущих smart-lab={r.smartlab}, ошибки={r.errors}"
        )
    table = Table(title=f"События ближайших {days} дн.")
    table.add_column("Дата", justify="right")
    table.add_column("Тикер")
    table.add_column("Событие")
    with session_scope() as session:
        for ev in upcoming_events(session, days_ahead=days):
            table.add_row(ev["event_date"].strftime("%Y-%m-%d"),
                          ev["ticker"] or "—", ev["title"][:80])
    console.print(table)


@app.command()
def outcomes(
    limit: int | None = typer.Option(None, "--limit", "-n", help="Ограничить число пар."),
) -> None:
    """Рыночная авто-разметка новостей (E2): форвардные доходности → news_outcomes."""
    from geoanalytics.analytics.outcomes import label_news_outcomes

    r = label_news_outcomes(limit=limit)
    console.print(
        f"[green]Разметка исходов[/]: записано={r.labeled}, ждут горизонта={r.pending}, "
        f"без истории цен={r.no_history}, ошибки={r.errors}"
    )
    if r.by_asset:
        top = sorted(r.by_asset.items(), key=lambda kv: -kv[1])[:10]
        console.print("По активам: " + ", ".join(f"{t}={n}" for t, n in top))


@app.command("continuous-eval")
def continuous_eval(
    days: int = typer.Option(90, "--days", "-d", help="Окно исходов для оценки."),
    no_alerts: bool = typer.Option(False, "--no-alerts", help="Не слать дрейф-алерт."),
) -> None:
    """Непрерывная оценка значимости против рынка (I2): precision/recall гейта + дрейф.

    Учитель — фактическая реакция цены (news_outcomes). Пишет метрику в eval_runs; при
    деградации относительно трейлинг-базы шлёт алерт model_drift. Обычно — еженедельный джоб."""
    from geoanalytics.analytics.continuous_eval import run_continuous_eval, run_stance_eval
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        s = run_continuous_eval(session, days=days, send_alerts=not no_alerts)
    a = s.agreement
    if not s.recorded:
        console.print(f"[yellow]significance: {s.note or 'прогон не записан'}[/] (n={a.n})")
    else:
        console.print(
            f"[green]significance vs рынок[/] (n={a.n}, помечено={a.n_flagged}, "
            f"двинулось={a.n_moved}): precision={a.precision:.3f}"
            + (f", recall={a.recall:.3f}" if a.recall else ""))
        d = s.drift
        if d.drifted:
            console.print(f"[red]⚠ Дрейф[/]: {d.reason}"
                          + (" · алерт отправлен" if s.alerted else ""))
        elif d.baseline is not None:
            console.print(f"База {d.baseline:.3f} — {d.reason}.")

    # C1: калибровка направленной стойки против реализованного движения рынка (дозрело).
    with session_scope() as session:
        sd = run_stance_eval(session, days=days, send_alerts=not no_alerts)
    sa = sd.agreement
    if not sd.recorded:
        console.print(f"[yellow]стойка: {sd.note or 'прогон не записан'}[/]")
    else:
        console.print(f"[green]стойка vs рынок[/] (направленных={sa.n_flagged}): "
                      f"directional_precision={sa.precision:.3f}"
                      + (f" · [red]⚠ дрейф[/]: {sd.drift.reason}" if sd.drift.drifted else ""))


@app.command("active-learn")
def active_learn(
    task: str = typer.Option("sentiment", "--task", "-t", help="sentiment | significance."),
    threshold: float = typer.Option(0.25, "--threshold", help="Порог уверенности (ниже — берём)."),
    limit: int = typer.Option(30, "--limit", "-n", help="Сколько кандидатов."),
    days: int = typer.Option(30, "--days", "-d", help="Окно статей."),
) -> None:
    """Активное обучение (I5): низкоуверенные предсказания на ручную разметку (у границы решения).

    Берём из хранимых полей (sentiment_score / significance) — новых данных не нужно. Помогает
    наполнять золото там, где модель спорит, а не случайно."""
    from geoanalytics.analytics.active_learning import low_confidence_candidates
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        cands = low_confidence_candidates(session, task=task, threshold=threshold,
                                          limit=limit, days=days)
    if not cands:
        console.print(f"[yellow]Нет низкоуверенных кандидатов ({task}, порог {threshold}).[/]")
        return
    table = Table(title=f"Активное обучение — {task} (уверенность < {threshold})")
    table.add_column("Увер.", justify="right")
    table.add_column("Метка")
    table.add_column("Скор", justify="right")
    table.add_column("Заголовок")
    for c in cands:
        table.add_row(f"{c['confidence']:.3f}", str(c["label"]), f"{c['score']:.3f}",
                      (c["title"] or "")[:70])
    console.print(table)


@app.command("sentiment-index")
def sentiment_index(
    backfill: bool = typer.Option(False, "--backfill", help="Перезаполнить за N дней назад."),
    days: int = typer.Option(30, "--days", "-d", help="Окно бэкфилла (дни)."),
) -> None:
    """Индекс настроения рынка (B1): дневной агрегат сентимента по рынку/секторам/активам.

    Без `--backfill` считает один день (вчера) — как ежедневный джоб. С `--backfill` — ряд за
    `--days` дней (EWMA-перенос по возрастанию). Материализует тренд/breadth/дивергенцию."""
    from geoanalytics.analytics.market_sentiment import backfill as bf
    from geoanalytics.analytics.market_sentiment import latest, record_day
    from geoanalytics.storage.db import session_scope

    with session_scope() as session:
        rows = bf(session, days=days) if backfill else record_day(session)
        mkt = latest(session, "market")
        if mkt is not None:
            console.print(
                f"[green]Настроение рынка[/] на {mkt.day}: среднее={mkt.sent_mean:+.3f}, "
                f"EWMA={mkt.sent_ewma:+.3f}, breadth={mkt.breadth:+.2f}, "
                f"разброс={mkt.dispersion:.2f} (n={mkt.n_docs})")
    console.print(f"[green]Записано строк:[/] {rows}"
                  + (f" (бэкфилл {days} дн.)" if backfill else " (вчера)"))


@app.command()
def reliability() -> None:
    """Надёжность источников (F7): априор доверия + точность по исходам E2 + доля слухов."""
    from geoanalytics.analytics.source_reliability import source_reliability_report

    reports = source_reliability_report()
    if not reports:
        console.print("[yellow]Нет данных по источникам[/]")
        return
    table = Table(title="Надёжность источников")
    table.add_column("Источник")
    table.add_column("Статей", justify="right")
    table.add_column("Исходов", justify="right")
    table.add_column("Точность", justify="right")
    table.add_column("Априор", justify="right")
    table.add_column("Скор", justify="right")
    table.add_column("Слухи %", justify="right")
    for r in reports:
        acc = f"{r.accuracy:.0%}" if r.accuracy is not None else "—"
        table.add_row(
            r.source, str(r.articles), str(r.n), acc,
            f"{r.prior:.2f}", f"{r.score:.2f}", f"{r.rumor_share:.0%}",
        )
    console.print(table)


@app.command("significance-audit")
def significance_audit(
    hours: int = typer.Option(168, "--hours", "-H", help="Окно анализа распределения."),
) -> None:
    """Б6: аудит каскада значимости — инвариант + распределение + что проходит каждый гейт.

    Заменяет «дрейф порогов руками без A/B» наблюдаемостью: видно, сколько новостей режет
    инжест-фильтр и алерт-гейт, и как они лежат относительно бакетов модели."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from geoanalytics.nlp.significance import (
        significance_bucket,
        significance_gates,
        validate_cascade,
    )
    from geoanalytics.storage.db import session_scope
    from geoanalytics.storage.models import Article

    gates = significance_gates()
    problems = validate_cascade()
    if problems:
        console.print("[red]Инвариант каскада нарушен:[/]")
        for p in problems:
            console.print(f"  • {p}")
    else:
        console.print(f"[green]Каскад согласован[/]: инжест {gates['ingest']} ≤ "
                      f"алерт {gates['alert']}")

    since = datetime.now(UTC) - timedelta(hours=hours)
    with session_scope() as session:
        vals = [v for (v,) in session.execute(
            select(Article.significance).where(
                Article.published_at >= since, Article.significance.isnot(None))
        )]
    if not vals:
        console.print(f"[yellow]Нет статей за {hours}ч[/]")
        return

    buckets = {"low": 0, "medium": 0, "high": 0}
    for v in vals:
        buckets[significance_bucket(v)] += 1
    n = len(vals)
    ge_alert = sum(1 for v in vals if v >= gates["alert"])
    lt_ingest = sum(1 for v in vals if v < gates["ingest"])
    table = Table(title=f"Распределение значимости за {hours}ч (n={n})")
    table.add_column("Метрика")
    table.add_column("Значение", justify="right")
    table.add_row("медиана", f"{sorted(vals)[n // 2]:.3f}")
    for b in ("low", "medium", "high"):
        table.add_row(f"бакет {b}", f"{buckets[b]} ({buckets[b] / n:.0%})")
    table.add_row(f"≥ алерт-гейт {gates['alert']}", f"{ge_alert} ({ge_alert / n:.0%})")
    table.add_row(f"< инжест {gates['ingest']} (кандидаты на отсев)",
                  f"{lt_ingest} ({lt_ingest / n:.0%})")
    console.print(table)


@app.command("event-study")
def event_study(
    save: bool = typer.Option(False, "--save", help="Сохранить JSON в data/eval/event_study.json."),
    min_n: int = typer.Option(5, "--min-n", help="Минимум исходов на тип события."),
    keep_confounded: bool = typer.Option(
        False, "--keep-confounded", help="Не исключать исходы с соседними событиями другого типа."
    ),
    confound_window: int = typer.Option(
        0, "--confound-window", help="Окно конфаундеров, ±дней (0 = только тот же день)."
    ),
) -> None:
    """Event study (E1): фактическая реакция рынка по типам событий vs ручные веса."""
    import json as _json
    from pathlib import Path

    from geoanalytics.analytics.event_study import event_study_report

    rep = event_study_report(exclude_confounded=not keep_confounded, min_n=min_n,
                             confound_window=confound_window)
    console.print(
        f"Исходов: {rep['total']}, в расчёте: {rep['used']}, "
        f"конфаундеры исключены: {rep['confounded']}"
    )
    table = Table(title="Реакция рынка по типам событий (abnormal return, %)")
    table.add_column("Тип")
    table.add_column("n", justify="right")
    table.add_column("AAR 1д", justify="right")
    table.add_column("|AR| 1д", justify="right")
    table.add_column("|AR| 5д", justify="right")
    table.add_column("hit≥2%", justify="right")
    table.add_column("Вес: факт", justify="right")
    table.add_column("Вес: ручной", justify="right")
    for s in rep["stats"]:
        et = s["event_type"]
        table.add_row(
            et, str(s["n"]),
            f"{s['aar'].get('1', 0):+.2f}", f"{s['mean_abs'].get('1', 0):.2f}",
            f"{s['mean_abs'].get('5', 0):.2f}", f"{s['hit_rate']:.0%}",
            f"{rep['empirical_weights'].get(et, 0):.2f}",
            f"{rep['hand_weights'].get(et, 0):.2f}",
        )
    console.print(table)
    if save:
        out = Path("data/eval/event_study.json")
        out.write_text(_json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]Сохранено:[/] {out}")


@app.command("alert-outcomes")
def alert_outcomes(
    days: int = typer.Option(30, "--days", "-d", help="Trailing-окно precision, дней."),
    send: bool = typer.Option(False, "--send",
                              help="Отправить отчёт в Telegram (дедуп по неделе)."),
) -> None:
    """Скоринг исходов алертов (E4) + precision по типам — главная метрика системы."""
    from geoanalytics.alerts.outcomes import (
        precision_summary,
        score_alert_outcomes,
        send_weekly_report,
    )

    r = score_alert_outcomes()
    console.print(
        f"[green]Скоринг алертов[/]: новых исходов={r.scored} (hits={r.hits}), "
        f"ждут горизонта={r.pending}, без цен={r.skipped}, ошибки={r.errors}"
    )
    summary = precision_summary(days=days)
    if not summary:
        console.print("Скоренных исходов за окно пока нет.")
    else:
        table = Table(title=f"Precision алертов за {days} дн.")
        table.add_column("Тип")
        table.add_column("Исходов", justify="right")
        table.add_column("Hits", justify="right")
        table.add_column("Precision", justify="right")
        for s in summary:
            table.add_row(s["alert_type"], str(s["n"]), str(s["hits"]),
                          f"{s['precision']:.0%}" if s["precision"] is not None else "—")
        console.print(table)
    if send:
        sent = send_weekly_report(days=days)
        console.print("[green]Отчёт отправлен.[/]" if sent
                      else "Отчёт этой недели уже отправлялся (или нет каналов).")


@app.command()
def events(
    hours: int = typer.Option(168, "--hours", "-h", help="Окно в часах."),
    limit: int = typer.Option(20, "--limit", "-n", help="Сколько событий показать."),
) -> None:
    """Последние значимые события и их влияние на активы."""
    from geoanalytics.query.events_feed import recent_events

    table = Table(title=f"События за {hours} ч")
    table.add_column("Тип")
    table.add_column("Заголовок")
    table.add_column("Активы (влияние)")
    mark = {"positive": "▲", "negative": "▼", "neutral": "■"}
    for ev in recent_events(hours=hours, limit=limit):
        assets = ", ".join(
            f"{i['ticker']}{mark.get(i['direction'], '')}{i['magnitude']}"
            for i in ev["impacts"]
        ) or "—"
        table.add_row(ev["event_type"], ev["title"][:60], assets)
    console.print(table)
